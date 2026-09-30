"""Tests for market context (multiples + reverse DCF) on synthetic data."""

from datetime import date

import pytest

from equity_research.analytics.market import market_context
from equity_research.analytics.metrics import MetricStore
from equity_research.models import Fact
from equity_research.models.provenance import Provenance
from equity_research.providers.prices import Quote

FY = date(2025, 8, 31)


def _fact(concept, val, *, instant=False):
    start = None if instant else date(2024, 8, 31)
    return Fact(
        cik=1, taxonomy="us-gaap", concept=concept, unit="USD", value=val,
        period_start=start, period_end=FY, is_instant=instant, fiscal_period="FY",
        accession="acc-1", filed_date=FY,
        provenance=Provenance(source="test", accession="acc-1"),
    )


@pytest.fixture
def store():
    return MetricStore([
        _fact("Revenues", 1000.0),
        _fact("EarningsPerShareDiluted", 10.0),
        _fact("NetCashProvidedByUsedInOperatingActivities", 120.0),
        _fact("PaymentsToAcquirePropertyPlantAndEquipment", 20.0),  # FCF = 100
        _fact("CashAndCashEquivalentsAtCarryingValue", 50.0, instant=True),
        _fact("LongTermDebtNoncurrent", 0.0, instant=True),
        _fact("WeightedAverageNumberOfDilutedSharesOutstanding", 10.0),
    ])


def test_multiples_computed(store):
    ctx = market_context(store, Quote("X", price=100.0, as_of=date(2026, 6, 16)))
    assert ctx is not None
    # P/E = 100 / 10 = 10
    assert ctx.pe_ratio == pytest.approx(10.0)
    # Market cap = 100 * 10 shares = 1000
    assert ctx.market_cap == pytest.approx(1000.0)
    # FCF yield = 100 / 1000 = 10%
    assert ctx.fcf_yield == pytest.approx(0.10)


def test_implied_growth_reverse_dcf(store):
    # Price set so the implied growth is recoverable and finite.
    ctx = market_context(store, Quote("X", price=120.0, as_of=None))
    assert ctx.implied_fcf_growth is not None
    # Implied growth should be a plausible rate, below the discount rate.
    assert -0.2 < ctx.implied_fcf_growth < 0.09


def test_higher_price_implies_higher_growth(store):
    low = market_context(store, Quote("X", price=80.0, as_of=None)).implied_fcf_growth
    high = market_context(store, Quote("X", price=160.0, as_of=None)).implied_fcf_growth
    assert high > low


def test_to_dict_serializable(store):
    d = market_context(store, Quote("X", price=100.0, as_of=None)).to_dict()
    assert set(d) >= {"price", "pe_ratio", "fcf_yield", "implied_fcf_growth", "market_cap"}
