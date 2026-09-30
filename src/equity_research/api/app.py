"""FastAPI application: dashboard data + chat Q&A."""

from __future__ import annotations

import os
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from equity_research import config as cfg
from equity_research.api import service
from equity_research.api.dashboard import DASHBOARD_HTML
from equity_research.storage import AlertStore, Database


class ChatRequest(BaseModel):
    question: str
    verify: bool = False


def _load_company_or_404(ticker: str):
    try:
        return cfg.load_company(ticker)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _db() -> Database:
    return Database(cfg.settings.db_path)


def _start_scheduled_refresh(interval_seconds: int) -> None:
    """Background thread that periodically recomputes + persists alerts (opt-in)."""
    from equity_research.watchlist import refresh_alerts

    def loop() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                refresh_alerts(
                    db_path=cfg.settings.db_path, alerts_db_path=cfg.settings.alerts_db_path
                )
            except Exception:  # noqa: BLE001 — never let the background loop kill the server
                pass

    threading.Thread(target=loop, daemon=True, name="alert-refresh").start()


def create_app() -> FastAPI:
    app = FastAPI(title="Equity Research", version="0.1.0")

    # Opt-in scheduled refresh: set EQR_REFRESH_INTERVAL (seconds) to enable.
    interval = int(os.environ.get("EQR_REFRESH_INTERVAL", "0"))
    if interval > 0:
        _start_scheduled_refresh(interval)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD_HTML

    @app.get("/api/companies")
    def companies() -> dict:
        out = []
        for t in cfg.list_companies():
            c = cfg.load_company(t)
            out.append({"ticker": c.ticker, "name": c.name, "exchange": c.exchange})
        return {"companies": out}

    @app.get("/api/watchlist")
    def watchlist() -> dict:
        store = AlertStore(cfg.settings.alerts_db_path)
        cards = []
        with _db() as db:
            for t in cfg.list_companies():
                company = cfg.load_company(t)
                if db.count_facts(company.cik) == 0:
                    continue
                card = service.company_card(company, db)
                card["unread_alerts"] = store.unread_count(company.ticker)
                cards.append(card)
        store.close()
        return {"companies": cards}

    @app.get("/api/companies/{ticker}/trends")
    def trends(ticker: str, years: int = 8) -> dict:
        company = _load_company_or_404(ticker)
        with _db() as db:
            if db.count_facts(company.cik) == 0:
                raise HTTPException(404, f"No data for {ticker}.")
            return service.company_trends(company, db, years=years)

    @app.get("/api/companies/{ticker}/overview")
    def overview(ticker: str) -> dict:
        company = _load_company_or_404(ticker)
        with _db() as db:
            if db.count_facts(company.cik) == 0:
                raise HTTPException(404, f"No data for {ticker}. Run `equity ingest {ticker}`.")
            return service.company_overview(company, db)

    @app.get("/api/companies/{ticker}/statements")
    def statements(ticker: str, years: int = 5) -> dict:
        company = _load_company_or_404(ticker)
        with _db() as db:
            if db.count_facts(company.cik) == 0:
                raise HTTPException(404, f"No data for {ticker}.")
            return service.company_statements(company, db, years=years)

    @app.get("/api/companies/{ticker}/filings")
    def filings(ticker: str, form: str | None = None, limit: int = 25) -> dict:
        company = _load_company_or_404(ticker)
        with _db() as db:
            return service.company_filings(company, db, form=form, limit=limit)

    @app.get("/api/companies/{ticker}/comps")
    def comps(ticker: str) -> dict:
        company = _load_company_or_404(ticker)
        return service.company_comps(
            company, user_agent=cfg.settings.sec_user_agent, cache_dir=cfg.settings.cache_dir
        )

    @app.post("/api/refresh")
    def refresh(ticker: str | None = None) -> dict:
        from equity_research.watchlist import refresh_alerts

        tickers = [ticker] if ticker else None
        s = refresh_alerts(
            db_path=cfg.settings.db_path,
            alerts_db_path=cfg.settings.alerts_db_path,
            tickers=tickers,
        )
        return {
            "checked": s.checked,
            "new_by_ticker": s.new_by_ticker,
            "total_new": s.total_new,
            "skipped": s.skipped,
        }

    @app.get("/api/feed")
    def feed(ticker: str | None = None, only_unread: bool = False) -> dict:
        store = AlertStore(cfg.settings.alerts_db_path)
        items = store.feed(ticker=ticker, only_unread=only_unread)
        store.close()
        return {
            "unread": sum(1 for a in items if not a.acknowledged),
            "alerts": [
                {
                    "fingerprint": a.fingerprint, "ticker": a.ticker, "kind": a.kind,
                    "severity": a.severity, "message": a.message, "as_of": a.as_of,
                    "sources": a.sources, "first_seen": a.first_seen,
                    "acknowledged": a.acknowledged,
                }
                for a in items
            ],
        }

    @app.post("/api/alerts/{fingerprint}/ack")
    def ack(fingerprint: str) -> dict:
        store = AlertStore(cfg.settings.alerts_db_path)
        ok = store.acknowledge(fingerprint)
        store.close()
        if not ok:
            raise HTTPException(404, "Alert not found or already acknowledged.")
        return {"acknowledged": fingerprint}

    @app.get("/api/companies/{ticker}/alerts")
    def alerts(ticker: str) -> dict:
        company = _load_company_or_404(ticker)
        with _db() as db:
            if db.count_facts(company.cik) == 0:
                raise HTTPException(404, f"No data for {ticker}.")
            ov = service.company_overview(company, db)
            return {"ticker": ticker, "alerts": ov["alerts"]}

    @app.post("/api/companies/{ticker}/chat")
    def chat(ticker: str, req: ChatRequest) -> dict:
        company = _load_company_or_404(ticker)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(
                503, "Chat unavailable: ANTHROPIC_API_KEY not set. Dashboard endpoints still work."
            )
        from equity_research.agents.supervisor import Supervisor
        from equity_research.agents.tools import ResearchContext
        from equity_research.rag.store import ChunkStore

        db = _db()
        if db.count_facts(company.cik) == 0:
            raise HTTPException(404, f"No data for {ticker}.")
        chunks_path = cfg.settings.chunks_db_path
        chunks = ChunkStore(chunks_path) if chunks_path.exists() else None
        ctx = ResearchContext(company=company, db=db, chunks=chunks)
        result = Supervisor(ctx).research(req.question, verify=req.verify)
        return {
            "ticker": ticker,
            "question": req.question,
            "answer": result.answer,
            "tool_calls": [{"name": n, "input": i} for n, i in result.tool_calls],
            "citation_coverage": (
                result.citation_report.coverage if result.citation_report else None
            ),
            "critique": (
                {"verdict": result.critique.verdict, "text": result.critique.critique}
                if result.critique else None
            ),
        }

    return app


app = create_app()
