"""MetricStore — clean access to base + derived financial metrics, with provenance.

This is the façade the rest of Phase 2 (ratios, DCF, comps) and later the agent
tools build on. It wraps raw `Fact`s with:
  * base metric series (from the normalizer),
  * derived metrics (margins, FCF, derived total_liabilities, ...),
  * growth helpers (YoY, CAGR),
each returned as a `Computed` that records the formula and the source facts so
nothing is an anonymous number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from equity_research.models import Fact
from equity_research.models.provenance import Provenance
from equity_research.normalize.xbrl import build_metric_series


@dataclass
class Computed:
    """A computed value with its formula and the facts it traces back to."""

    metric: str
    period_end: date
    value: float
    formula: str
    sources: list[Provenance] = field(default_factory=list)
    unit: str = "USD"

    def citation(self) -> str:
        accns = sorted({p.accession for p in self.sources if p.accession})
        return f"{self.formula}  [{', '.join(accns) or 'n/a'}]"


# Derived metrics: name -> (formula string, required base metrics, fn(values)->float).
# `values` maps base-metric name -> float for a single period_end.
def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


DERIVED: dict[str, tuple[str, list[str], object]] = {
    "total_liabilities_derived": (
        "total_assets - stockholders_equity",
        ["total_assets", "stockholders_equity"],
        lambda v: v["total_assets"] - v["stockholders_equity"],
    ),
    "free_cash_flow": (
        "operating_cash_flow - capex",
        ["operating_cash_flow", "capex"],
        lambda v: v["operating_cash_flow"] - v["capex"],
    ),
    "gross_profit_derived": (
        "revenue - cost_of_revenue",
        ["revenue", "cost_of_revenue"],
        lambda v: v["revenue"] - v["cost_of_revenue"],
    ),
    "gross_margin": (
        # Many service firms (e.g. ACN) don't tag GrossProfit; derive from cost.
        "(revenue - cost_of_revenue) / revenue",
        ["revenue", "cost_of_revenue"],
        lambda v: _safe_div(v["revenue"] - v["cost_of_revenue"], v["revenue"]),
    ),
    "operating_margin": (
        "operating_income / revenue",
        ["operating_income", "revenue"],
        lambda v: _safe_div(v["operating_income"], v["revenue"]),
    ),
    "net_margin": (
        "net_income / revenue",
        ["net_income", "revenue"],
        lambda v: _safe_div(v["net_income"], v["revenue"]),
    ),
    "fcf_margin": (
        "(operating_cash_flow - capex) / revenue",
        ["operating_cash_flow", "capex", "revenue"],
        lambda v: _safe_div(v["operating_cash_flow"] - v["capex"], v["revenue"]),
    ),
    "return_on_equity": (
        "net_income / stockholders_equity",
        ["net_income", "stockholders_equity"],
        lambda v: _safe_div(v["net_income"], v["stockholders_equity"]),
    ),
    "return_on_assets": (
        "net_income / total_assets",
        ["net_income", "total_assets"],
        lambda v: _safe_div(v["net_income"], v["total_assets"]),
    ),
    "current_ratio": (
        "total_current_assets / total_current_liabilities",
        ["total_current_assets", "total_current_liabilities"],
        lambda v: _safe_div(v["total_current_assets"], v["total_current_liabilities"]),
    ),
    "effective_tax_rate": (
        "income_tax_expense / pretax_income",
        ["income_tax_expense", "pretax_income"],
        lambda v: _safe_div(v["income_tax_expense"], v["pretax_income"]),
    ),
}

# Unitless (ratio) derived metrics for display formatting.
RATIO_METRICS = {
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "return_on_equity",
    "return_on_assets",
    "current_ratio",
    "effective_tax_rate",
}


class MetricStore:
    def __init__(self, facts: list[Fact], *, period: str = "annual") -> None:
        self._facts = facts
        self._period = period
        self._base_cache: dict[str, dict[date, Fact]] = {}

    # -- base metrics -------------------------------------------------------

    def series(self, metric: str) -> dict[date, Fact]:
        """Base canonical metric series {period_end -> Fact}."""
        if metric not in self._base_cache:
            self._base_cache[metric] = build_metric_series(
                self._facts, metric, period=self._period
            )
        return self._base_cache[metric]

    def value(self, metric: str, period_end: date) -> float | None:
        f = self.series(metric).get(period_end)
        return f.value if f else None

    def periods(self, metric: str = "revenue") -> list[date]:
        return sorted(self.series(metric))

    # -- derived metrics ----------------------------------------------------

    def derived(self, metric: str) -> dict[date, Computed]:
        """Compute a derived metric across all periods where its inputs exist."""
        if metric not in DERIVED:
            raise KeyError(f"Unknown derived metric: {metric!r}")
        formula, inputs, fn = DERIVED[metric]
        # Periods where every required base metric is present.
        base_series = {m: self.series(m) for m in inputs}
        common = set.intersection(*(set(s) for s in base_series.values())) if base_series else set()

        out: dict[date, Computed] = {}
        for pe in common:
            vals = {m: base_series[m][pe].value for m in inputs}
            result = fn(vals)
            if result is None:
                continue
            sources = [base_series[m][pe].provenance for m in inputs]
            out[pe] = Computed(
                metric=metric,
                period_end=pe,
                value=result,
                formula=formula,
                sources=sources,
                unit="ratio" if metric in RATIO_METRICS else "USD",
            )
        return out

    # -- growth -------------------------------------------------------------

    def yoy_growth(self, metric: str) -> dict[date, float]:
        """Year-over-year growth from consecutive fiscal-year values."""
        series = self.series(metric)
        periods = sorted(series)
        out: dict[date, float] = {}
        for prev, cur in zip(periods, periods[1:]):
            p, c = series[prev].value, series[cur].value
            if p not in (0, None):
                out[cur] = c / p - 1.0
        return out

    def cagr(self, metric: str, years: int) -> float | None:
        """Compound annual growth rate over the last `years` fiscal years."""
        periods = self.periods(metric)
        if len(periods) <= years:
            return None
        end = periods[-1]
        start = periods[-1 - years]
        v_end = self.value(metric, end)
        v_start = self.value(metric, start)
        if not v_start or v_start <= 0 or v_end is None or v_end <= 0:
            return None
        return (v_end / v_start) ** (1.0 / years) - 1.0

    @classmethod
    def from_db(cls, db, cik: int, *, period: str = "annual") -> "MetricStore":
        return cls(db.get_all_facts(cik), period=period)
