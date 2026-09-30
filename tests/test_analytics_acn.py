"""Analytics tests against the live ACN data (skip if not ingested).

Validates derived metrics against hand calculations from known FY2025 10-K values
and asserts the consistency invariants an analyst would eyeball.
"""

import pytest
from datetime import date

ACN_CIK = 1467373
FY2025 = date(2025, 8, 31)

# From the FY2025 10-K (accession 0001467373-25-000217).
REVENUE = 69_672_977_000
NET_INCOME = 7_678_433_000
OCF = 11_474_399_000


@pytest.fixture(scope="module")
def store():
    from equity_research.config import settings
    from equity_research.storage import Database
    from equity_research.analytics.metrics import MetricStore

    if not settings.db_path.exists():
        pytest.skip("Live DB not present — run `equity ingest ACN`.")
    with Database(settings.db_path) as db:
        if db.count_facts(ACN_CIK) == 0:
            pytest.skip("ACN not ingested.")
        return MetricStore.from_db(db, ACN_CIK)


def test_net_margin_matches_hand_calc(store):
    nm = store.derived("net_margin")[FY2025]
    assert nm.value == pytest.approx(NET_INCOME / REVENUE, rel=1e-6)
    # The derived value must cite the underlying facts.
    accns = {p.accession for p in nm.sources}
    assert "0001467373-25-000217" in accns


def test_free_cash_flow_consistency(store):
    fcf = store.derived("free_cash_flow")[FY2025]
    capex = store.value("capex", FY2025)
    assert fcf.value == pytest.approx(OCF - capex, rel=1e-9)
    # ACN is asset-light: FCF should be a large fraction of operating cash flow.
    assert 0.7 * OCF < fcf.value <= OCF


def test_margins_in_sane_ranges(store):
    gm = store.derived("gross_margin")[FY2025].value
    om = store.derived("operating_margin")[FY2025].value
    nm = store.derived("net_margin")[FY2025].value
    # Ordering invariant: gross >= operating >= net.
    assert gm > om > nm > 0
    # ACN is a consulting firm: net margin roughly 9-13%.
    assert 0.08 < nm < 0.15


def test_revenue_cagr_positive(store):
    cagr = store.cagr("revenue", 5)
    assert cagr is not None
    # ACN has compounded revenue in the high single digits over 5y.
    assert 0.03 < cagr < 0.20


def test_derived_total_liabilities_reconciles(store):
    # ACN doesn't tag total Liabilities; we derive it as Assets - Equity and it
    # must be positive and less than total assets.
    tl = store.derived("total_liabilities_derived")[FY2025]
    assets = store.value("total_assets", FY2025)
    assert 0 < tl.value < assets
