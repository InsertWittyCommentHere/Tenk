"""DCF engine tests — deterministic, hand-verifiable golden numbers."""

import pytest

from equity_research.analytics.dcf import (
    DCFAssumptions,
    reverse_dcf,
    run_dcf,
    scenario_range,
)


def test_zero_growth_perpetuity_identity():
    # With zero growth everywhere and discount r, the DCF of a constant FCF must
    # equal FCF/r exactly (sum of projection window + terminal collapses to this).
    a = DCFAssumptions(
        base_fcf=100.0, growth_rate=0.0, years=10, terminal_growth=0.0, discount_rate=0.10
    )
    r = run_dcf(a)
    assert r.enterprise_value == pytest.approx(1000.0, rel=1e-9)


def test_projection_and_discounting_shapes():
    a = DCFAssumptions(base_fcf=100.0, growth_rate=0.05, years=10, discount_rate=0.09)
    r = run_dcf(a)
    assert len(r.projected_fcf) == 10
    assert len(r.discounted_fcf) == 10
    # First projected year is base*(1+g); discounting reduces it.
    assert r.projected_fcf[0] == pytest.approx(105.0)
    assert r.discounted_fcf[0] == pytest.approx(105.0 / 1.09)
    assert r.enterprise_value == pytest.approx(r.pv_of_fcf + r.pv_terminal_value)


def test_net_cash_and_per_share():
    a = DCFAssumptions(
        base_fcf=100.0, growth_rate=0.0, years=10, terminal_growth=0.0,
        discount_rate=0.10, net_cash=200.0, shares_outstanding=100.0,
    )
    r = run_dcf(a)
    assert r.equity_value == pytest.approx(1200.0)  # 1000 EV + 200 net cash
    assert r.value_per_share == pytest.approx(12.0)


def test_scenarios_are_ordered():
    base = DCFAssumptions(base_fcf=1000.0, growth_rate=0.08, years=10, discount_rate=0.09)
    s = scenario_range(base, bear_growth=0.03, bull_growth=0.13)
    assert s["bear"].enterprise_value < s["base"].enterprise_value < s["bull"].enterprise_value


def test_reverse_dcf_round_trips():
    # Build a price from a known growth, then recover that growth via reverse DCF.
    shares = 100.0
    base_fcf = 50.0
    a = DCFAssumptions(
        base_fcf=base_fcf, growth_rate=0.07, years=10, terminal_growth=0.025,
        discount_rate=0.09, net_cash=0.0, shares_outstanding=shares,
    )
    price = run_dcf(a).value_per_share
    implied = reverse_dcf(
        market_price_per_share=price, base_fcf=base_fcf, shares_outstanding=shares,
        years=10, terminal_growth=0.025, discount_rate=0.09,
    )
    assert implied == pytest.approx(0.07, abs=1e-3)


def test_terminal_must_be_below_discount():
    with pytest.raises(ValueError):
        DCFAssumptions(base_fcf=100, growth_rate=0.05, discount_rate=0.02, terminal_growth=0.03)
