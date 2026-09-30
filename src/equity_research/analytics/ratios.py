"""Consolidated ratio report across periods.

Thin layer over `MetricStore.derived` that assembles a per-period dict of the
ratios an analyst scans first: margins, returns, liquidity, leverage, plus
revenue/EPS growth. Each value still traces to source facts via the underlying
`Computed` objects (see `ratio_sources`).
"""

from __future__ import annotations

from datetime import date

from equity_research.analytics.metrics import MetricStore

# Ratios surfaced in the standard report, in display order.
REPORT_RATIOS = [
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "return_on_equity",
    "return_on_assets",
    "current_ratio",
    "effective_tax_rate",
]


def compute_ratios(store: MetricStore, *, last_n: int | None = None) -> dict[date, dict[str, float]]:
    """Return {period_end -> {ratio_name -> value}} plus revenue/EPS YoY growth."""
    out: dict[date, dict[str, float]] = {}
    for name in REPORT_RATIOS:
        for pe, comp in store.derived(name).items():
            out.setdefault(pe, {})[name] = comp.value

    # Growth metrics layered in.
    for pe, g in store.yoy_growth("revenue").items():
        out.setdefault(pe, {})["revenue_growth"] = g
    for pe, g in store.yoy_growth("eps_diluted").items():
        out.setdefault(pe, {})["eps_growth"] = g

    # Leverage: debt / equity (uses derived FCF-independent base metrics).
    for pe in list(out):
        debt = store.value("long_term_debt", pe)
        equity = store.value("stockholders_equity", pe)
        if debt is not None and equity:
            out[pe]["debt_to_equity"] = debt / equity

    periods = sorted(out)
    if last_n:
        periods = periods[-last_n:]
    return {pe: out[pe] for pe in periods}


def ratio_sources(store: MetricStore, ratio: str) -> dict[date, list[str]]:
    """Accession numbers backing each period's value of a ratio (for citations)."""
    out: dict[date, list[str]] = {}
    for pe, comp in store.derived(ratio).items():
        out[pe] = sorted({p.accession for p in comp.sources if p.accession})
    return out
