"""Watchlist orchestration: refresh alerts across configured companies, build the feed.

This is the app-level glue the retail surface needs — a single cross-company
inbox of "what changed" rather than per-ticker recomputation. `refresh_alerts`
recomputes each company's alerts and persists only the new ones (change
detection lives in `AlertStore`); the dashboard/CLI read the resulting feed.

Used by both the CLI (`equity refresh` / `equity feed`) and the API, so it takes
explicit paths rather than reaching for globals — easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from equity_research import config as cfg
from equity_research.alerts import run_alerts
from equity_research.analytics.metrics import MetricStore
from equity_research.storage import AlertStore, Database


@dataclass
class RefreshSummary:
    checked: list[str] = field(default_factory=list)
    new_by_ticker: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)  # ticker -> reason

    @property
    def total_new(self) -> int:
        return sum(self.new_by_ticker.values())


def refresh_alerts(
    *,
    db_path: Path,
    alerts_db_path: Path,
    tickers: list[str] | None = None,
) -> RefreshSummary:
    """Recompute alerts for each company and persist new ones. Returns a summary."""
    tickers = tickers or cfg.list_companies()
    summary = RefreshSummary()
    with Database(db_path) as db, AlertStore(alerts_db_path) as store:
        for t in tickers:
            try:
                company = cfg.load_company(t)
            except FileNotFoundError as e:
                summary.skipped[t] = str(e)
                continue
            if db.count_facts(company.cik) == 0:
                summary.skipped[company.ticker] = "no data ingested"
                continue
            store_metrics = MetricStore.from_db(db, company.cik)
            alerts = run_alerts(db, store_metrics, company.cik)
            new = store.record(company.cik, company.ticker, alerts)
            summary.checked.append(company.ticker)
            summary.new_by_ticker[company.ticker] = len(new)
    return summary
