"""Unit tests for the normalizer — the trust-critical logic.

These are offline and deterministic: they build synthetic `Fact`s and assert the
two behaviors that matter most for correctness:
  1. Concept fallback respects priority order.
  2. Restatement dedup keeps the most-recently-filed value per period.
"""

from __future__ import annotations

from datetime import date

from equity_research.models import Fact
from equity_research.models.provenance import Provenance
from equity_research.normalize.xbrl import build_metric_series, reconstruct_statements


def _fact(concept: str, end: str, val: float, *, start: str | None = "x", filed: str = "2020-01-01",
          accn: str = "a", fp: str = "FY") -> Fact:
    # start="x" sentinel -> derive a ~annual duration ending at `end`.
    if start == "x":
        e = date.fromisoformat(end)
        s = date(e.year - 1, e.month, e.day)
        start_d = s.isoformat()
    else:
        start_d = start
    sd = date.fromisoformat(start_d) if start_d else None
    return Fact(
        cik=1,
        taxonomy="us-gaap",
        concept=concept,
        unit="USD",
        value=val,
        period_start=sd,
        period_end=date.fromisoformat(end),
        is_instant=sd is None,
        fiscal_period=fp,
        form="10-K",
        accession=accn,
        filed_date=date.fromisoformat(filed),
        provenance=Provenance(source="test", accession=accn, filed_date=date.fromisoformat(filed)),
    )


def test_concept_priority_fallback():
    # Higher-priority RevenueFromContract... should win over Revenues for the same period.
    facts = [
        _fact("Revenues", "2023-08-31", 100.0),
        _fact("RevenueFromContractWithCustomerExcludingAssessedTax", "2023-08-31", 200.0),
    ]
    series = build_metric_series(facts, "revenue")
    assert series[date(2023, 8, 31)].value == 200.0


def test_concept_fallback_when_primary_absent():
    # Only the lower-priority concept exists -> it is used.
    facts = [_fact("Revenues", "2022-08-31", 90.0)]
    series = build_metric_series(facts, "revenue")
    assert series[date(2022, 8, 31)].value == 90.0


def test_restatement_keeps_latest_filed():
    # Same concept+period, two filings; the later-filed value wins.
    facts = [
        _fact("NetIncomeLoss", "2021-08-31", 50.0, filed="2021-10-01", accn="orig"),
        _fact("NetIncomeLoss", "2021-08-31", 55.0, filed="2022-10-01", accn="restated"),
    ]
    series = build_metric_series(facts, "net_income")
    f = series[date(2021, 8, 31)]
    assert f.value == 55.0
    assert f.accession == "restated"


def test_provenance_survives_normalization():
    facts = [_fact("Revenues", "2023-08-31", 100.0, accn="acc-1", filed="2023-10-15")]
    series = build_metric_series(facts, "revenue")
    f = series[date(2023, 8, 31)]
    assert f.provenance.accession == "acc-1"
    assert f.provenance.cite().startswith("test")


def test_flow_metric_rejects_stray_instant_fact():
    # Regression: filers (e.g. Accenture) emit *instant* dividend facts labeled
    # fp='FY' on payment dates. A flow metric must ignore those and keep only the
    # real ~annual duration fact.
    facts = [
        _fact("PaymentsOfOrdinaryDividends", "2025-08-31", 3700.0),  # real annual flow
        _fact("PaymentsOfOrdinaryDividends", "2025-08-15", 922.0, start=None),  # stray instant FY
    ]
    series = build_metric_series(facts, "dividends_paid")
    assert list(series) == [date(2025, 8, 31)]
    assert series[date(2025, 8, 31)].value == 3700.0


def test_instant_metric_rejects_duration_fact():
    # A balance-sheet (instant) metric must not pick up a duration fact.
    facts = [
        _fact("Assets", "2025-08-31", 500.0, start=None),  # correct instant
        _fact("Assets", "2025-08-31", 999.0),  # bogus duration variant
    ]
    series = build_metric_series(facts, "total_assets")
    assert series[date(2025, 8, 31)].value == 500.0


def test_reconstruct_statements_groups_metrics():
    facts = [
        _fact("Revenues", "2023-08-31", 100.0),
        _fact("NetIncomeLoss", "2023-08-31", 10.0),
        _fact("Assets", "2023-08-31", 500.0, start=None),  # instant
    ]
    stmts = reconstruct_statements(facts)
    assert "revenue" in stmts["income_statement"]
    assert "net_income" in stmts["income_statement"]
    assert "total_assets" in stmts["balance_sheet"]
