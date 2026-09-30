"""Discounted cash flow — forward (multi-scenario) and reverse.

Design choices that keep this honest:
  * It is a **transparent**, fully-deterministic model. Every intermediate (each
    year's projected FCF, discount factor, terminal value) is returned, so the
    agent narrates a model it cannot fudge.
  * Forward DCF outputs a **value range** across bear/base/bull, never a single
    false-precision number.
  * Reverse DCF answers "what FCF growth is the current price implying?" — often
    more decision-useful than a point estimate, because it frames the market's
    expectations rather than competing with them.

This is a standard unlevered-FCF-to-equity simplification suitable for a stable,
asset-light compounder like ACN (we start from current free cash flow and grow
it). It is intentionally simple and auditable; a fund tier can swap in a full
unlevered-FCFF/WACC build behind the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DCFAssumptions:
    """Inputs for one DCF scenario."""

    base_fcf: float  # latest annual free cash flow (USD)
    growth_rate: float  # annual FCF growth during the projection window
    years: int = 10
    terminal_growth: float = 0.025  # perpetuity growth after the window
    discount_rate: float = 0.09  # required return / cost of equity
    net_cash: float = 0.0  # cash minus debt, added to PV of FCF
    shares_outstanding: float | None = None  # to derive per-share value

    def __post_init__(self) -> None:
        if self.discount_rate <= self.terminal_growth:
            raise ValueError(
                f"discount_rate ({self.discount_rate}) must exceed terminal_growth "
                f"({self.terminal_growth}) for a finite terminal value."
            )


@dataclass
class DCFResult:
    assumptions: DCFAssumptions
    projected_fcf: list[float] = field(default_factory=list)
    discounted_fcf: list[float] = field(default_factory=list)
    pv_of_fcf: float = 0.0
    terminal_value: float = 0.0
    pv_terminal_value: float = 0.0
    enterprise_value: float = 0.0
    equity_value: float = 0.0
    value_per_share: float | None = None


def run_dcf(a: DCFAssumptions) -> DCFResult:
    """Project FCF, discount it, add a Gordon-growth terminal value."""
    projected: list[float] = []
    discounted: list[float] = []
    fcf = a.base_fcf
    for t in range(1, a.years + 1):
        fcf = fcf * (1 + a.growth_rate)
        df = (1 + a.discount_rate) ** t
        projected.append(fcf)
        discounted.append(fcf / df)

    pv_fcf = sum(discounted)

    # Terminal value via Gordon growth on the final-year FCF.
    final_fcf = projected[-1]
    tv = final_fcf * (1 + a.terminal_growth) / (a.discount_rate - a.terminal_growth)
    pv_tv = tv / (1 + a.discount_rate) ** a.years

    enterprise_value = pv_fcf + pv_tv
    equity_value = enterprise_value + a.net_cash

    vps = None
    if a.shares_outstanding and a.shares_outstanding > 0:
        vps = equity_value / a.shares_outstanding

    return DCFResult(
        assumptions=a,
        projected_fcf=projected,
        discounted_fcf=discounted,
        pv_of_fcf=pv_fcf,
        terminal_value=tv,
        pv_terminal_value=pv_tv,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        value_per_share=vps,
    )


def scenario_range(
    base: DCFAssumptions,
    *,
    bear_growth: float,
    bull_growth: float,
) -> dict[str, DCFResult]:
    """Run bear/base/bull by varying the projection-window growth rate."""
    from dataclasses import replace

    return {
        "bear": run_dcf(replace(base, growth_rate=bear_growth)),
        "base": run_dcf(base),
        "bull": run_dcf(replace(base, growth_rate=bull_growth)),
    }


def reverse_dcf(
    *,
    market_price_per_share: float,
    base_fcf: float,
    shares_outstanding: float,
    years: int = 10,
    terminal_growth: float = 0.025,
    discount_rate: float = 0.09,
    net_cash: float = 0.0,
    tol: float = 1e-4,
    max_iter: int = 200,
) -> float:
    """Solve for the FCF growth rate the current price implies (binary search).

    Returns the implied annual FCF growth such that the DCF value-per-share equals
    the market price. This reframes valuation as "what must you believe?".
    """
    target_equity = market_price_per_share * shares_outstanding - net_cash

    def equity_value_for(g: float) -> float:
        a = DCFAssumptions(
            base_fcf=base_fcf,
            growth_rate=g,
            years=years,
            terminal_growth=terminal_growth,
            discount_rate=discount_rate,
            net_cash=net_cash,
            shares_outstanding=shares_outstanding,
        )
        return run_dcf(a).enterprise_value  # compare on EV (net_cash handled in target)

    # Monotonic in g: binary search between a wide bracket.
    lo, hi = -0.50, 0.50
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        ev = equity_value_for(mid)
        if abs(ev - target_equity) / max(abs(target_equity), 1) < tol:
            return mid
        if ev < target_equity:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
