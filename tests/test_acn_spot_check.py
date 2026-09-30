"""Direct document spot-check: ACN FY2025 10-K vs our stored values.

Ground-truth source: EDGAR companyfacts API, accession 0001467373-25-000217,
queried 2026-06-17. We assert the exact raw values (in USD, not millions) that
EDGAR itself reported for that specific filing. This test:
  - catches unit errors (thousands vs raw dollars)
  - catches concept-selection mistakes (wrong tag with plausible-looking value)
  - catches fiscal-year misalignment (Aug 31 year-end conflated with calendar year)
  - verifies our restatement-dedup kept the most-recently-filed value

If any assertion fails, first check whether the value changed due to a subsequent
amended filing (a later accession) before assuming a normalizer bug.
"""

import pytest
from datetime import date

ACN_CIK = 1467373
FY2025_END = date(2025, 8, 31)
FY2025_ACCN = "0001467373-25-000217"

# Ground-truth values extracted from EDGAR companyfacts for accn=0001467373-25-000217
# All flow figures are for the fiscal year ended 2025-08-31.
# All instant figures are as of 2025-08-31.
GROUND_TRUTH = {
    # Income statement (FY2025)
    "revenue":             69_672_977_000,
    "net_income":           7_678_433_000,
    "eps_diluted":                  12.15,
    "eps_basic":                    12.29,
    # Balance sheet (as of 2025-08-31)
    "total_assets":        65_394_897_000,
    "cash_and_equivalents":11_478_729_000,
    # Cash flow (FY2025)
    "operating_cash_flow": 11_474_399_000,
}


@pytest.fixture(scope="module")
def acn_series(tmp_path_factory):
    """Load ACN facts from the shared live DB and build metric series."""
    from pathlib import Path
    from equity_research.config import settings
    from equity_research.storage import Database
    from equity_research.normalize.xbrl import build_metric_series

    db_path = settings.db_path
    if not db_path.exists():
        pytest.skip("Live DB not present — run `equity ingest ACN` first.")

    with Database(db_path) as db:
        if db.count_facts(ACN_CIK) == 0:
            pytest.skip("ACN not ingested — run `equity ingest ACN` first.")
        facts = db.get_all_facts(ACN_CIK)

    return {metric: build_metric_series(facts, metric) for metric in GROUND_TRUTH}


def _get(series, metric):
    """Retrieve the Fact for FY2025 or fail with a clear message."""
    s = series[metric]
    assert FY2025_END in s, (
        f"No FY2025 value for {metric!r}. "
        f"Available period-ends: {sorted(s)[-5:]}"
    )
    return s[FY2025_END]


@pytest.mark.parametrize("metric,expected", [
    ("revenue",              69_672_977_000),
    ("net_income",            7_678_433_000),
    ("total_assets",         65_394_897_000),
    ("cash_and_equivalents", 11_478_729_000),
    ("operating_cash_flow",  11_474_399_000),
])
def test_integer_values_match_10k(acn_series, metric, expected):
    fact = _get(acn_series, metric)
    assert fact.value == pytest.approx(expected, rel=1e-4), (
        f"{metric}: got {fact.value:,.0f}, expected {expected:,.0f}  "
        f"(accn={fact.accession}, filed={fact.filed_date})"
    )


@pytest.mark.parametrize("metric,expected", [
    ("eps_diluted", 12.15),
    ("eps_basic",   12.29),
])
def test_eps_matches_10k(acn_series, metric, expected):
    fact = _get(acn_series, metric)
    assert fact.value == pytest.approx(expected, abs=0.005), (
        f"{metric}: got {fact.value}, expected {expected}  "
        f"(accn={fact.accession}, filed={fact.filed_date})"
    )


def test_values_sourced_from_fy2025_10k(acn_series):
    """Every spot-checked metric should trace back to the FY2025 10-K accession."""
    for metric in ["revenue", "net_income", "total_assets", "eps_diluted"]:
        fact = _get(acn_series, metric)
        assert fact.accession == FY2025_ACCN, (
            f"{metric} came from {fact.accession!r}, expected {FY2025_ACCN!r}. "
            "Check whether a later amendment superseded this filing."
        )
        assert fact.form in ("10-K", "10-K/A"), (
            f"{metric} sourced from form {fact.form!r}, not a 10-K."
        )


def test_provenance_populated(acn_series):
    """Every fact must carry provenance — no anonymous numbers."""
    for metric in GROUND_TRUTH:
        fact = _get(acn_series, metric)
        p = fact.provenance
        assert p.source == "SEC EDGAR"
        assert p.accession is not None, f"{metric} has no accession in provenance."
        assert p.filed_date is not None, f"{metric} has no filed_date in provenance."
