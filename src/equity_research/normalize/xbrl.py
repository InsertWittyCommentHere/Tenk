"""Turn raw XBRL facts into canonical metric series and reconstructed statements.

Two point-in-time subtleties handled here:

  * **Concept fallback.** For each period we try the candidate concepts in
    priority order (see concepts.py) and take the first that has a value.
  * **Restatements / amendments.** The same (concept, period) can appear in
    multiple filings with different values. We keep them all in storage, but for
    a "current view" we pick the value from the *most recently filed* filing.
    A future point-in-time query can instead pick "as originally filed".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from equity_research.models import Fact
from equity_research.normalize.concepts import CANONICAL_CONCEPTS

# Balance-sheet metrics are point-in-time (instant); income & cash-flow are flows
# (durations). Knowing a metric's kind lets us reject the wrong fact type — e.g.
# a filer may emit stray *instant* dividend facts labeled fp='FY' on payment dates,
# which must not be mistaken for the annual *flow* of dividends paid.
INSTANT_METRICS = {
    "cash_and_equivalents",
    "total_current_assets",
    "total_assets",
    "total_current_liabilities",
    "total_liabilities",
    "long_term_debt",
    "total_debt",
    "stockholders_equity",
    "shares_outstanding",
}


def _is_annual(f: Fact, *, instant_metric: bool) -> bool:
    if instant_metric:
        # Balance-sheet snapshot for the full year: an instant fact labeled FY.
        return f.is_instant and f.fiscal_period == "FY"
    # Flow over the full fiscal year: a ~year-long duration the filer labels FY.
    if f.is_instant:
        return False
    return f.is_annual and f.fiscal_period in (None, "FY")


def _is_quarterly(f: Fact, *, instant_metric: bool) -> bool:
    if instant_metric:
        return f.is_instant and f.fiscal_period in ("Q1", "Q2", "Q3", "Q4")
    if f.is_instant:
        return False
    d = f.duration_days
    return d is not None and 80 <= d <= 100 and f.fiscal_period in ("Q1", "Q2", "Q3", "Q4")


def _pick_latest_filed(facts: list[Fact]) -> Fact:
    """Among facts for one period, choose the one from the most recent filing."""
    return max(facts, key=lambda f: (f.filed_date or date.min, f.accession or ""))


def build_metric_series(
    facts: list[Fact],
    metric: str,
    *,
    period: str = "annual",
) -> dict[date, Fact]:
    """Return {period_end -> Fact} for a canonical metric, deduped to latest-filed.

    `period` is 'annual' or 'quarterly'. Concept priority resolves which tag wins
    when several are present for the same period_end.
    """
    if metric not in CANONICAL_CONCEPTS:
        raise KeyError(f"Unknown canonical metric: {metric!r}")
    concepts = CANONICAL_CONCEPTS[metric]
    instant_metric = metric in INSTANT_METRICS
    period_filter = _is_annual if period == "annual" else _is_quarterly

    def keep(f: Fact) -> bool:
        return period_filter(f, instant_metric=instant_metric)

    # Group candidate facts by period_end, tracking concept priority.
    by_period: dict[date, dict[str, list[Fact]]] = defaultdict(lambda: defaultdict(list))
    concept_rank = {c: i for i, c in enumerate(concepts)}
    for f in facts:
        if f.concept not in concept_rank or not keep(f):
            continue
        by_period[f.period_end][f.concept].append(f)

    out: dict[date, Fact] = {}
    for period_end, by_concept in by_period.items():
        # Highest-priority concept that has data for this period wins.
        for concept in concepts:
            if concept in by_concept:
                out[period_end] = _pick_latest_filed(by_concept[concept])
                break
    return out


# Canonical line ordering for a reconstructed statement view.
INCOME_STATEMENT = [
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "sga_expense",
    "rnd_expense",
    "operating_income",
    "interest_expense",
    "pretax_income",
    "income_tax_expense",
    "net_income",
    "eps_basic",
    "eps_diluted",
    "shares_diluted",
]
BALANCE_SHEET = [
    "cash_and_equivalents",
    "total_current_assets",
    "total_assets",
    "total_current_liabilities",
    "total_liabilities",
    "long_term_debt",
    "stockholders_equity",
    "shares_outstanding",
]
CASH_FLOW = [
    "operating_cash_flow",
    "capex",
    "dividends_paid",
    "stock_repurchased",
]


def fiscal_period_ends(
    facts: list[Fact], *, period: str = "annual", anchor: str = "revenue", n: int | None = None
) -> list[date]:
    """The fiscal period-end dates to use as statement columns.

    Anchored on an income-statement metric (revenue) so columns are true fiscal
    year-ends. This excludes off-cycle dates that some instant facts carry — e.g.
    the `dei` cover-page share count dated to the filing date, not the FYE.
    """
    ends = sorted(build_metric_series(facts, anchor, period=period))
    return ends[-n:] if n else ends


def reconstruct_statements(
    facts: list[Fact],
    *,
    period: str = "annual",
) -> dict[str, dict[str, dict[date, Fact]]]:
    """Build the three statements as {statement -> metric -> {period_end -> Fact}}.

    Missing metrics are simply absent (a filer may not tag every line). Each Fact
    retains its provenance so downstream rendering can cite every number.
    """
    statements = {
        "income_statement": INCOME_STATEMENT,
        "balance_sheet": BALANCE_SHEET,
        "cash_flow": CASH_FLOW,
    }
    result: dict[str, dict[str, dict[date, Fact]]] = {}
    for name, metrics in statements.items():
        result[name] = {}
        for metric in metrics:
            series = build_metric_series(facts, metric, period=period)
            if series:
                result[name][metric] = series
    return result
