"""Alert engine (Phase 5).

Deterministic, filing-derived alerts — the "what changed / what to watch" signals
that drive the dashboard. Two families:

  * **Filing/insider** — new SEC filings and Form 4 (insider) clustering within a
    lookback window of an `as_of` date.
  * **Fundamental** — trend breaks computed from the metric store (revenue-growth
    deceleration, margin compression, a leverage jump).

Everything here is computed from the data spine (no LLM, no external feeds), so it
is testable and citeable. Estimate-revision and guidance-change alerts need the
Estimates/IR providers and are deferred to those (DESIGN.md §2.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from equity_research.analytics.metrics import MetricStore
from equity_research.storage import Database

# Severity ordering for sorting/coloring.
SEVERITY_RANK = {"high": 3, "warn": 2, "info": 1}


@dataclass
class Alert:
    kind: str
    severity: str  # "high" | "warn" | "info"
    message: str
    as_of: date | None = None
    sources: list[str] = field(default_factory=list)
    dedup_key: str | None = None  # stable identity for change-detection (defaults to message)

    @property
    def rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)


def _yoy(store: MetricStore, metric: str) -> dict[date, float]:
    return store.yoy_growth(metric)


def fundamental_alerts(store: MetricStore) -> list[Alert]:
    """Trend-break signals from the latest vs prior fiscal year."""
    out: list[Alert] = []

    # Revenue-growth deceleration.
    growth = sorted(_yoy(store, "revenue").items())
    if len(growth) >= 2:
        (_, prior_g), (cur_pe, cur_g) = growth[-2], growth[-1]
        if cur_g < prior_g - 0.05 and cur_g < 0.05:
            out.append(
                Alert(
                    kind="revenue_deceleration",
                    severity="warn",
                    message=(
                        f"Revenue growth decelerated to {cur_g:.1%} in FY{cur_pe.year} "
                        f"from {prior_g:.1%} the prior year."
                    ),
                    as_of=cur_pe,
                )
            )

    # Operating-margin compression.
    om = store.derived("operating_margin")
    periods = sorted(om)
    if len(periods) >= 2:
        prev, cur = periods[-2], periods[-1]
        delta = om[cur].value - om[prev].value
        if delta <= -0.03:
            sev = "warn"
        elif delta <= -0.01:
            sev = "info"
        else:
            sev = None
        if sev:
            out.append(
                Alert(
                    kind="margin_compression",
                    severity=sev,
                    message=(
                        f"Operating margin compressed {abs(delta) * 100:.1f}pp to "
                        f"{om[cur].value:.1%} in FY{cur.year}."
                    ),
                    as_of=cur,
                    sources=sorted({p.accession for p in om[cur].sources if p.accession}),
                )
            )

    # Leverage jump (debt/equity).
    de_periods = sorted(store.series("long_term_debt"))
    if len(de_periods) >= 2:
        def de(pe: date) -> float | None:
            d = store.value("long_term_debt", pe)
            e = store.value("stockholders_equity", pe)
            return d / e if (d is not None and e) else None

        prev, cur = de_periods[-2], de_periods[-1]
        de_prev, de_cur = de(prev), de(cur)
        if de_prev is not None and de_cur is not None and de_cur - de_prev > 0.10:
            out.append(
                Alert(
                    kind="leverage_increase",
                    severity="info",
                    message=(
                        f"Debt/equity rose to {de_cur:.2f} in FY{cur.year} "
                        f"from {de_prev:.2f} — new borrowing."
                    ),
                    as_of=cur,
                )
            )
    return out


def filing_alerts(
    db: Database, cik: int, *, as_of: date, window_days: int = 30
) -> list[Alert]:
    """New material filings within the lookback window."""
    cutoff = as_of - timedelta(days=window_days)
    out: list[Alert] = []
    severity_by_form = {"10-K": "high", "10-Q": "warn", "8-K": "info", "DEF 14A": "info"}
    for f in db.list_filings(cik, limit=50):
        if f.filed_date and cutoff <= f.filed_date <= as_of and f.form in severity_by_form:
            out.append(
                Alert(
                    kind="new_filing",
                    severity=severity_by_form[f.form],
                    message=f"New {f.form} filed {f.filed_date.isoformat()}.",
                    as_of=f.filed_date,
                    sources=[f.accession],
                )
            )
    return out


def insider_alerts(
    db: Database, cik: int, *, as_of: date, window_days: int = 90, threshold: int = 5
) -> list[Alert]:
    """Form 4 (insider transaction) clustering within the window."""
    cutoff = as_of - timedelta(days=window_days)
    form4 = [
        f for f in db.list_filings(cik, form="4", limit=200)
        if f.filed_date and cutoff <= f.filed_date <= as_of
    ]
    if len(form4) >= threshold:
        return [
            Alert(
                kind="insider_cluster",
                severity="info",
                message=(
                    f"{len(form4)} insider (Form 4) filings in the last {window_days} days — "
                    f"elevated insider activity."
                ),
                as_of=as_of,
                sources=[f.accession for f in form4[:5]],
                # Bucket by month so the daily-shifting count doesn't re-fire constantly.
                dedup_key=f"insider_cluster|{as_of:%Y-%m}",
            )
        ]
    return []


def run_alerts(
    db: Database, store: MetricStore, cik: int, *, as_of: date | None = None
) -> list[Alert]:
    """All alert families, sorted by severity (high first)."""
    as_of = as_of or date.today()
    alerts = (
        fundamental_alerts(store)
        + filing_alerts(db, cik, as_of=as_of)
        + insider_alerts(db, cik, as_of=as_of)
    )
    return sorted(alerts, key=lambda a: a.rank, reverse=True)
