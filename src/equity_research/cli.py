"""Command-line interface for the data spine.

Commands (Phase 1):
  equity companies                 list configured companies
  equity ingest ACN                pull EDGAR submissions + facts into the DB
  equity filings ACN --form 10-K   list stored filings
  equity statements ACN            print reconstructed annual statements (cited)
  equity metric ACN revenue        print one canonical metric's annual series
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from equity_research import config as cfg
from equity_research.normalize.xbrl import (
    build_metric_series,
    fiscal_period_ends,
    reconstruct_statements,
)
from equity_research.providers.edgar import EdgarProvider
from equity_research.storage import Database

app = typer.Typer(help="Equity Research — SEC EDGAR data spine (Phase 1).", no_args_is_help=True)
console = Console()


def _provider() -> EdgarProvider:
    s = cfg.settings
    return EdgarProvider(
        user_agent=s.sec_user_agent, cache_dir=s.cache_dir, max_per_second=s.sec_max_rps
    )


def _db() -> Database:
    return Database(cfg.settings.db_path)


def _fmt(v: float) -> str:
    """Human-format a magnitude (e.g. 64.1B, 1.23)."""
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:,.2f}M"
    if a >= 1e3:
        return f"{v / 1e3:,.2f}K"
    return f"{v:,.2f}"


@app.command()
def companies() -> None:
    """List configured companies."""
    names = cfg.list_companies()
    if not names:
        console.print("[yellow]No companies configured.[/]")
        raise typer.Exit()
    table = Table("Ticker", "CIK", "Name", "FY end", "Peers")
    for t in names:
        c = cfg.load_company(t)
        table.add_row(c.ticker, str(c.cik), c.name, c.fiscal_year_end or "-", ", ".join(c.peers))
    console.print(table)


@app.command()
def ingest(ticker: str) -> None:
    """Fetch SEC EDGAR submissions + XBRL facts for a company and store them."""
    from equity_research.ingest import ingest_company

    company = cfg.load_company(ticker)
    console.print(f"Ingesting [bold]{company.name}[/] (CIK {company.cik}) from SEC EDGAR…")
    with _provider() as provider, _db() as db:
        result = ingest_company(provider, db, company.cik)
    console.print(
        f"[green]Done.[/] {result.filings_ingested} filings, "
        f"{result.facts_ingested} XBRL facts stored for {result.name}."
    )


@app.command()
def filings(ticker: str, form: str = typer.Option(None, help="Filter by form, e.g. 10-K")) -> None:
    """List stored filings for a company."""
    company = cfg.load_company(ticker)
    with _db() as db:
        rows = db.list_filings(company.cik, form=form, limit=25)
    if not rows:
        console.print("[yellow]No filings stored. Run `equity ingest` first.[/]")
        raise typer.Exit()
    table = Table("Form", "Filed", "Period", "Accession", "Document")
    for f in rows:
        table.add_row(
            f.form,
            f.filed_date.isoformat(),
            f.report_date.isoformat() if f.report_date else "-",
            f.accession,
            f.primary_document or "-",
        )
    console.print(table)


@app.command()
def metric(ticker: str, name: str, period: str = "annual") -> None:
    """Print one canonical metric's series with citations."""
    company = cfg.load_company(ticker)
    with _db() as db:
        facts = db.get_all_facts(company.cik)
    if not facts:
        console.print("[yellow]No facts stored. Run `equity ingest` first.[/]")
        raise typer.Exit()
    series = build_metric_series(facts, name, period=period)
    if not series:
        console.print(f"[yellow]No data for metric {name!r}.[/]")
        raise typer.Exit()
    table = Table("Period end", "Value", "Form", "Filed", "Accession")
    for period_end in sorted(series):
        f = series[period_end]
        table.add_row(
            period_end.isoformat(),
            _fmt(f.value),
            f.form or "-",
            f.filed_date.isoformat() if f.filed_date else "-",
            f.accession or "-",
        )
    console.print(table)


@app.command()
def ratios(ticker: str, years: int = 5) -> None:
    """Profitability, returns, liquidity, leverage and growth ratios (cited)."""
    from equity_research.analytics.metrics import MetricStore
    from equity_research.analytics.ratios import REPORT_RATIOS, compute_ratios

    company = cfg.load_company(ticker)
    with _db() as db:
        store = MetricStore.from_db(db, company.cik)
    report = compute_ratios(store, last_n=years)
    if not report:
        console.print("[yellow]No ratio data. Run `equity ingest` first.[/]")
        raise typer.Exit()
    cols = sorted(report)
    rows_order = REPORT_RATIOS + ["revenue_growth", "eps_growth", "debt_to_equity"]
    pct = set(REPORT_RATIOS) - {"current_ratio"} | {"revenue_growth", "eps_growth"}
    table = Table(title=f"{company.ticker} — Key ratios")
    table.add_column("Ratio")
    for c in cols:
        table.add_column(c.isoformat(), justify="right")
    for name in rows_order:
        row = [name]
        for c in cols:
            v = report[c].get(name)
            if v is None:
                row.append("·")
            elif name in pct:
                row.append(f"{v * 100:.1f}%")
            else:
                row.append(f"{v:.2f}")
        table.add_row(*row)
    console.print(table)


@app.command()
def dcf(
    ticker: str,
    discount: float = typer.Option(0.09, help="Discount rate / cost of equity."),
    terminal: float = typer.Option(0.025, help="Terminal growth rate."),
    base_growth: float = typer.Option(0.08, help="Base-case FCF growth."),
    bear: float = typer.Option(0.03, help="Bear-case FCF growth."),
    bull: float = typer.Option(0.12, help="Bull-case FCF growth."),
    years: int = 10,
) -> None:
    """Multi-scenario DCF using the latest free cash flow (bear/base/bull range)."""
    from equity_research.analytics.dcf import DCFAssumptions, scenario_range
    from equity_research.analytics.metrics import MetricStore

    company = cfg.load_company(ticker)
    with _db() as db:
        store = MetricStore.from_db(db, company.cik)
    periods = store.periods("revenue")
    if not periods:
        console.print("[yellow]No data. Run `equity ingest` first.[/]")
        raise typer.Exit()
    pe = periods[-1]
    fcf = store.derived("free_cash_flow").get(pe)
    if fcf is None:
        console.print("[yellow]Cannot compute free cash flow for the latest period.[/]")
        raise typer.Exit()
    cash = store.value("cash_and_equivalents", pe) or 0.0
    debt = store.value("long_term_debt", pe) or 0.0
    shares = store.value("shares_outstanding", pe) or store.value("shares_diluted", pe)

    base = DCFAssumptions(
        base_fcf=fcf.value, growth_rate=base_growth, years=years,
        terminal_growth=terminal, discount_rate=discount,
        net_cash=cash - debt, shares_outstanding=shares,
    )
    scenarios = scenario_range(base, bear_growth=bear, bull_growth=bull)
    console.print(
        f"[bold]{company.ticker}[/] DCF — base FCF {_fmt(fcf.value)} (FY{pe.year}), "
        f"r={discount:.1%}, g_terminal={terminal:.1%}, {years}y window"
    )
    console.print(f"[dim]FCF source: {fcf.citation()}[/]")
    table = Table("Scenario", "FCF growth", "Equity value", "Value / share")
    growths = {"bear": bear, "base": base_growth, "bull": bull}
    for name in ["bear", "base", "bull"]:
        r = scenarios[name]
        table.add_row(
            name, f"{growths[name]:.1%}", _fmt(r.equity_value),
            f"${r.value_per_share:,.2f}" if r.value_per_share else "n/a",
        )
    console.print(table)


@app.command()
def comps(ticker: str) -> None:
    """Latest-fiscal-year fundamentals vs the configured peer set (live from EDGAR)."""
    from equity_research.analytics.comps import build_comps

    company = cfg.load_company(ticker)
    console.print(f"Building comps for {company.ticker} vs {', '.join(company.peers)} (live)…")
    with _provider() as provider:
        rows = build_comps(provider, company.ticker, company.peers)
    table = Table("Ticker", "Name", "Revenue", "Rev growth", "Gross %", "Op %", "Net %", "ROE")
    for r in rows:
        if r.note:
            table.add_row(r.ticker, f"[dim]{r.note}[/]", "·", "·", "·", "·", "·", "·")
            continue
        m = r.metrics
        def p(key: str) -> str:
            v = m.get(key)
            return f"{v * 100:.1f}%" if v is not None else "·"
        table.add_row(
            r.ticker, (r.name or "")[:22], _fmt(m["revenue"]) if m.get("revenue") else "·",
            p("revenue_growth"), p("gross_margin"), p("operating_margin"),
            p("net_margin"), p("return_on_equity"),
        )
    console.print(table)


@app.command()
def alerts(ticker: str) -> None:
    """Show deterministic alerts (new filings, insider clusters, fundamental trend breaks)."""
    from equity_research.alerts import run_alerts
    from equity_research.analytics.metrics import MetricStore

    company = cfg.load_company(ticker)
    with _db() as db:
        store = MetricStore.from_db(db, company.cik)
        items = run_alerts(db, store, company.cik)
    if not items:
        console.print("[green]No active alerts.[/]")
        raise typer.Exit()
    color = {"high": "red", "warn": "yellow", "info": "cyan"}
    for a in items:
        c = color.get(a.severity, "white")
        src = f"  [dim]{', '.join(a.sources)}[/]" if a.sources else ""
        console.print(f"[{c}]●[/] [{c}]{a.severity.upper()}[/] {a.message}{src}")


@app.command()
def refresh(ticker: str = typer.Argument(None, help="One ticker, or all if omitted.")) -> None:
    """Recompute alerts across the watchlist and record new ones (change detection)."""
    from equity_research.watchlist import refresh_alerts

    s = refresh_alerts(
        db_path=cfg.settings.db_path,
        alerts_db_path=cfg.settings.alerts_db_path,
        tickers=[ticker] if ticker else None,
    )
    for t in s.checked:
        n = s.new_by_ticker.get(t, 0)
        tag = f"[green]{n} new[/]" if n else "[dim]no change[/]"
        console.print(f"{t}: {tag}")
    for t, reason in s.skipped.items():
        console.print(f"[yellow]{t}: skipped ({reason})[/]")
    console.print(f"\n[bold]{s.total_new}[/] new alert(s) recorded.")


@app.command()
def feed(
    ticker: str = typer.Argument(None, help="Filter to one ticker."),
    unread: bool = typer.Option(False, help="Only unacknowledged alerts."),
) -> None:
    """Show the persisted cross-company alert feed (run `refresh` first)."""
    from equity_research.storage import AlertStore

    with AlertStore(cfg.settings.alerts_db_path) as store:
        items = store.feed(ticker=ticker, only_unread=unread)
    if not items:
        console.print("[dim]No alerts in the feed. Run `equity refresh`.[/]")
        raise typer.Exit()
    color = {"high": "red", "warn": "yellow", "info": "cyan"}
    for a in items:
        c = color.get(a.severity, "white")
        ack = "" if not a.acknowledged else " [dim](ack'd)[/]"
        console.print(
            f"[{c}]●[/] [{c}]{a.severity.upper()}[/] [{a.ticker}] {a.message}{ack}  "
            f"[dim]{a.fingerprint}[/]"
        )


@app.command()
def ack(fingerprint: str) -> None:
    """Acknowledge an alert by its fingerprint (from `equity feed`)."""
    from equity_research.storage import AlertStore

    with AlertStore(cfg.settings.alerts_db_path) as store:
        ok = store.acknowledge(fingerprint)
    if ok:
        console.print(f"[green]Acknowledged[/] {fingerprint}")
    else:
        console.print(f"[yellow]Not found or already acknowledged:[/] {fingerprint}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the dashboard + chat web server (FastAPI)."""
    import uvicorn

    console.print(f"Dashboard at [bold]http://{host}:{port}[/]  (Ctrl-C to stop)")
    uvicorn.run("equity_research.api.app:app", host=host, port=port, log_level="warning")


@app.command()
def research(
    ticker: str,
    question: str,
    model: str = typer.Option("claude-opus-4-8", help="Claude model id."),
    effort: str = typer.Option("high", help="Reasoning effort: low|medium|high|max."),
    verify: bool = typer.Option(False, help="Run the Critic/red-team review of the answer."),
) -> None:
    """Ask the Supervisor agent a research question (citation-backed). Needs ANTHROPIC_API_KEY."""
    import os

    from equity_research.agents.supervisor import Supervisor
    from equity_research.agents.tools import ResearchContext
    from equity_research.rag.store import ChunkStore

    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            "[red]ANTHROPIC_API_KEY not set.[/] The agent layer needs it; the data/analytics/"
            "search commands work without it."
        )
        raise typer.Exit(code=1)

    company = cfg.load_company(ticker)
    db = _db()
    if db.count_facts(company.cik) == 0:
        console.print("[yellow]No data ingested. Run `equity ingest` first.[/]")
        raise typer.Exit()
    chunks_path = cfg.settings.chunks_db_path
    chunks = ChunkStore(chunks_path) if chunks_path.exists() else None
    ctx = ResearchContext(company=company, db=db, chunks=chunks)

    console.print(f"[dim]Researching {company.ticker}: {question}[/]\n")
    sup = Supervisor(ctx, model=model, effort=effort)
    result = sup.research(question, verify=verify)
    for name, inp in result.tool_calls:
        console.print(f"[dim]→ {name}({inp})[/]")
    console.print()
    console.print(result.answer)

    if result.citation_report:
        rep = result.citation_report
        color = "green" if rep.passes() else "yellow"
        console.print(f"\n[{color}]✓ {rep.summary()}[/]")
        if not rep.passes() and rep.uncited_samples:
            console.print("[yellow]Uncited claims:[/]")
            for s in rep.uncited_samples:
                console.print(f"  [yellow]· {s}[/]")
    if result.critique:
        console.print(f"\n[bold]Critic verdict: {result.critique.verdict}[/]")
        console.print(result.critique.critique)


@app.command()
def index(
    ticker: str,
    form: str = typer.Option("10-K", help="Form type to index, e.g. 10-K, 10-Q."),
    limit: int = typer.Option(2, help="How many recent filings of this form to index."),
    force: bool = typer.Option(False, help="Re-index even if already present."),
) -> None:
    """Fetch recent filings' text and index them for retrieval (RAG)."""
    from equity_research.rag.index import index_filing
    from equity_research.rag.store import ChunkStore

    company = cfg.load_company(ticker)
    with _db() as db:
        filings = db.list_filings(company.cik, form=form, limit=limit)
    if not filings:
        console.print(f"[yellow]No {form} filings stored. Run `equity ingest` first.[/]")
        raise typer.Exit()
    console.print(f"Indexing {len(filings)} {form} filing(s) for {company.ticker}…")
    with _provider() as provider, ChunkStore(cfg.settings.chunks_db_path) as store:
        for f in filings:
            r = index_filing(provider, store, f, force=force)
            if r.skipped:
                console.print(f"  [dim]{f.accession} ({f.form}) — skipped (already indexed)[/]")
            else:
                console.print(
                    f"  [green]{r.accession}[/] ({r.form}) — {r.sections} sections, {r.chunks} chunks"
                )


@app.command()
def search(
    ticker: str,
    query: str,
    section: str = typer.Option(None, help="Filter by section, e.g. 'Risk Factors', 'MD&A'."),
    form: str = typer.Option(None, help="Filter by form."),
    limit: int = typer.Option(5, help="Number of passages."),
) -> None:
    """Keyword search over a company's indexed filings (cited passages)."""
    from equity_research.rag.store import ChunkStore

    company = cfg.load_company(ticker)
    with ChunkStore(cfg.settings.chunks_db_path) as store:
        hits = store.search(
            query, cik=company.cik, form=form, section_like=section, limit=limit
        )
    if not hits:
        console.print("[yellow]No matches. Have you run `equity index`?[/]")
        raise typer.Exit()
    for h in hits:
        console.print(f"[bold cyan]{h.citation()}[/]  [dim](score {h.score:.2f})[/]")
        snippet = h.text[:600].replace("\n", " ")
        console.print(f"  {snippet}…\n")


@app.command()
def statements(ticker: str, period: str = "annual", years: int = 5) -> None:
    """Print reconstructed financial statements (last N periods), each number cited."""
    company = cfg.load_company(ticker)
    with _db() as db:
        facts = db.get_all_facts(company.cik)
    if not facts:
        console.print("[yellow]No facts stored. Run `equity ingest` first.[/]")
        raise typer.Exit()
    stmts = reconstruct_statements(facts, period=period)
    # Anchor columns to fiscal year-ends (revenue), not every metric's own dates.
    cols = fiscal_period_ends(facts, period=period, n=years)

    for stmt_name, metrics in stmts.items():
        table = Table(title=f"{company.ticker} — {stmt_name.replace('_', ' ').title()} ({period})")
        table.add_column("Metric")
        for c in cols:
            table.add_column(c.isoformat(), justify="right")
        for metric_name, series in metrics.items():
            row = [metric_name]
            for c in cols:
                row.append(_fmt(series[c].value) if c in series else "·")
            table.add_row(*row)
        console.print(table)
        console.print()


if __name__ == "__main__":
    app()
