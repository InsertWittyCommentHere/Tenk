"""Static export: snapshot the dashboard + its JSON API into a folder of plain files.

The live app (FastAPI + SQLite + optional background refresh + LLM chat) needs a
running server. For a free, always-on public demo we instead render a *snapshot*:
every GET endpoint the dashboard reads is called once, in-process, and written to
`<out>/data/<path>.json`. The dashboard HTML is written with `TENK_STATIC = true`,
which makes it read those files and disables the write actions (refresh, ack,
chat). The result can be served by any static host (Vercel, GitHub Pages).

Exporting through the real app (FastAPI's TestClient) rather than re-calling the
service layer guarantees the static JSON is byte-for-byte what the live API
returns — there is no second code path to drift.

Saved example analyses (`showcase/research/<ticker>.json`, produced locally by
`equity save-research` with your own API key) are copied in so the demo can show
the agent's output without exposing a key.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from equity_research import config as cfg

SHOWCASE_DIR = cfg.REPO_ROOT / "showcase" / "research"

# Per-company endpoints the dashboard reads (see api/dashboard.py).
COMPANY_ENDPOINTS = ["overview", "trends", "statements", "filings", "alerts"]


@dataclass
class ExportSummary:
    out_dir: Path
    files: int = 0
    companies: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # "TICKER/endpoint" -> reason


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":"), default=str))


def static_dashboard_html() -> str:
    """The dashboard page with static mode switched on."""
    from equity_research.api.dashboard import DASHBOARD_HTML

    flag = "<script>window.TENK_STATIC = true;</script>\n"
    return DASHBOARD_HTML.replace("<script>", flag + "<script>", 1)


def export_site(
    out_dir: Path,
    *,
    with_comps: bool = True,
    showcase_dir: Path = SHOWCASE_DIR,
) -> ExportSummary:
    """Write a self-contained static snapshot of the dashboard to `out_dir`."""
    from fastapi.testclient import TestClient

    from equity_research.api.app import create_app

    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    data = out_dir / "data"
    summary = ExportSummary(out_dir=out_dir)

    def save(rel: str, payload: object) -> None:
        _write(data / f"{rel}.json", payload)
        summary.files += 1

    client = TestClient(create_app())

    def get(rel: str) -> dict | None:
        r = client.get(f"/api/{rel}")
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.status_code)
            except ValueError:
                detail = r.status_code
            summary.skipped[rel] = str(detail)
            return None
        return r.json()

    for rel in ("companies", "watchlist", "feed"):
        payload = get(rel)
        if payload is not None:
            save(rel, payload)

    from equity_research.storage import Database

    with Database(cfg.settings.db_path) as db:
        ingested = {t for t in cfg.list_companies() if db.count_facts(cfg.load_company(t).cik)}

    endpoints = COMPANY_ENDPOINTS + (["comps"] if with_comps else [])
    for ticker in cfg.list_companies():
        company = cfg.load_company(ticker)
        t = company.ticker
        if ticker not in ingested:
            summary.skipped[f"companies/{t}"] = "no data ingested"
            continue
        wrote_any = False
        for ep in endpoints:
            try:
                payload = get(f"companies/{t}/{ep}")
            except Exception as e:  # noqa: BLE001 — one bad endpoint must not sink the export
                summary.skipped[f"companies/{t}/{ep}"] = f"{type(e).__name__}: {e}"
                payload = None
            if payload is not None:
                save(f"companies/{t}/{ep}", payload)
                wrote_any = True
        saved = showcase_dir / f"{t.lower()}.json"
        if saved.exists():
            save(f"companies/{t}/research", json.loads(saved.read_text()))
        if wrote_any:
            summary.companies.append(t)

    save(
        "meta",
        {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "companies": summary.companies,
            "note": "Static snapshot of the Tenk dashboard. Not investment advice.",
        },
    )

    (out_dir / "index.html").write_text(static_dashboard_html())
    # Serve as plain static files on Vercel (no framework detection, no build step).
    _write(out_dir / "vercel.json", {"framework": None, "buildCommand": None})
    summary.files += 2
    return summary


def save_research(ticker: str, result: dict, *, showcase_dir: Path = SHOWCASE_DIR) -> Path:
    """Append one agent answer to `showcase/research/<ticker>.json` (committed to the repo)."""
    path = showcase_dir / f"{ticker.lower()}.json"
    doc = json.loads(path.read_text()) if path.exists() else {"ticker": ticker.upper(), "items": []}
    doc["items"] = [i for i in doc["items"] if i["question"] != result["question"]] + [result]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str) + "\n")
    return path
