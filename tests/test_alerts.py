"""Tests for the deterministic alert engine.

Fundamental alerts use synthetic facts (no network); filing/insider alerts use a
small synthetic DB so the date-window logic is exercised deterministically.
"""

from __future__ import annotations

from datetime import date

import pytest

from equity_research.alerts import (
    Alert,
    fundamental_alerts,
    filing_alerts,
    insider_alerts,
)
from equity_research.analytics.metrics import MetricStore
from equity_research.models import Fact
from equity_research.models.provenance import Provenance


def _fact(concept, end, val, *, instant=False, fp="FY", accn="a"):
    e = date.fromisoformat(end)
    start = None if instant else date(e.year - 1, e.month, e.day)
    return Fact(
        cik=1, taxonomy="us-gaap", concept=concept, unit="USD", value=val,
        period_start=start, period_end=e, is_instant=instant, fiscal_period=fp,
        form="10-K", accession=accn, filed_date=e,
        provenance=Provenance(source="test", accession=accn),
    )


def test_revenue_deceleration_alert_fires():
    # FY1->FY2 +20%, FY2->FY3 +1% (sharp decel, below 5%).
    facts = [
        _fact("Revenues", "2023-08-31", 100.0),
        _fact("Revenues", "2024-08-31", 120.0),
        _fact("Revenues", "2025-08-31", 121.2),
    ]
    alerts = fundamental_alerts(MetricStore(facts))
    kinds = {a.kind for a in alerts}
    assert "revenue_deceleration" in kinds


def test_margin_compression_alert_fires():
    facts = [
        _fact("Revenues", "2024-08-31", 1000.0),
        _fact("Revenues", "2025-08-31", 1000.0),
        _fact("OperatingIncomeLoss", "2024-08-31", 200.0),  # 20%
        _fact("OperatingIncomeLoss", "2025-08-31", 150.0),  # 15% -> 5pp compression
    ]
    alerts = fundamental_alerts(MetricStore(facts))
    comp = [a for a in alerts if a.kind == "margin_compression"]
    assert comp and comp[0].severity == "warn"


def test_leverage_increase_alert_fires():
    facts = [
        _fact("StockholdersEquity", "2024-08-31", 1000.0, instant=True),
        _fact("StockholdersEquity", "2025-08-31", 1000.0, instant=True),
        _fact("LongTermDebtNoncurrent", "2024-08-31", 0.0, instant=True),
        _fact("LongTermDebtNoncurrent", "2025-08-31", 500.0, instant=True),  # D/E 0 -> 0.5
    ]
    alerts = fundamental_alerts(MetricStore(facts))
    assert any(a.kind == "leverage_increase" for a in alerts)


def test_no_alert_when_stable():
    facts = [
        _fact("Revenues", "2024-08-31", 1000.0),
        _fact("Revenues", "2025-08-31", 1080.0),  # +8%, healthy
    ]
    assert fundamental_alerts(MetricStore(facts)) == []


def test_alert_sorting_by_severity():
    a_info = Alert(kind="x", severity="info", message="i")
    a_high = Alert(kind="y", severity="high", message="h")
    assert a_high.rank > a_info.rank


# -- filing/insider alerts use a tiny in-memory-ish DB --------------------------

@pytest.fixture
def db_with_filings(tmp_path):
    from equity_research.models.filings import Filing
    from equity_research.storage import Database

    db = Database(tmp_path / "t.db")
    filings = [
        Filing(cik=1, accession="0000000000-25-000001", form="10-K", filed_date=date(2025, 10, 10)),
        Filing(cik=1, accession="0000000000-26-000002", form="8-K", filed_date=date(2026, 6, 1)),
    ]
    # Six Form 4s in May/June 2026 -> insider cluster.
    for i in range(6):
        filings.append(
            Filing(cik=1, accession=f"0000000000-26-0001{i:02d}", form="4",
                   filed_date=date(2026, 6, 1 + i))
        )
    db.upsert_filings(filings)
    return db


def test_filing_alert_within_window(db_with_filings):
    alerts = filing_alerts(db_with_filings, 1, as_of=date(2026, 6, 17), window_days=30)
    assert any(a.kind == "new_filing" and "8-K" in a.message for a in alerts)
    # The 2025 10-K is outside the 30-day window.
    assert all("10-K" not in a.message for a in alerts)


def test_insider_cluster_alert(db_with_filings):
    alerts = insider_alerts(db_with_filings, 1, as_of=date(2026, 6, 17), window_days=90, threshold=5)
    assert len(alerts) == 1
    assert alerts[0].kind == "insider_cluster"
    assert "6 insider" in alerts[0].message
