"""Peer comparison ("comps").

Builds a relative table of the latest-fiscal-year fundamentals for a company and
its peer set, pulled from EDGAR. Non-US filers without SEC XBRL (e.g. Capgemini
on Euronext) are skipped with a recorded reason rather than silently dropped —
the agent should *know* a peer is missing, not assume the set is complete.

Trading multiples (P/E, EV/EBITDA) require a price feed and are deferred to the
Prices provider (Phase 2.5); this module covers the fundamental, filing-derived
side that needs no market data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from equity_research.analytics.metrics import MetricStore
from equity_research.providers.edgar import EdgarProvider

# Fundamental comp metrics (latest fiscal year), each a (label, accessor) pair.
COMP_FUNDAMENTALS = [
    "revenue",
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "return_on_equity",
]


@dataclass
class CompRow:
    ticker: str
    cik: int | None
    name: str | None
    metrics: dict[str, float | None] = field(default_factory=dict)
    note: str | None = None


def _latest_metrics(store: MetricStore) -> dict[str, float | None]:
    periods = store.periods("revenue")
    if not periods:
        return {}
    pe = periods[-1]
    out: dict[str, float | None] = {"revenue": store.value("revenue", pe)}

    g = store.yoy_growth("revenue")
    out["revenue_growth"] = g.get(pe)

    for ratio in ["gross_margin", "operating_margin", "net_margin", "fcf_margin",
                  "return_on_equity"]:
        comp = store.derived(ratio).get(pe)
        out[ratio] = comp.value if comp else None
    return out


def build_comps(
    provider: EdgarProvider,
    subject_ticker: str,
    peer_tickers: list[str],
) -> list[CompRow]:
    """Return one CompRow per ticker (subject first), with latest-FY fundamentals."""
    rows: list[CompRow] = []
    for ticker in [subject_ticker, *peer_tickers]:
        cik = provider.resolve_ticker(ticker)
        if cik is None:
            rows.append(
                CompRow(ticker=ticker, cik=None, name=None,
                        note="no SEC XBRL (non-US filer or unmapped ticker)")
            )
            continue
        try:
            idx = provider.get_submissions(cik)
            store = MetricStore(provider.get_facts(cik))
            rows.append(
                CompRow(ticker=ticker, cik=cik, name=idx.name, metrics=_latest_metrics(store))
            )
        except Exception as e:  # noqa: BLE001 - record, don't crash the whole table
            rows.append(CompRow(ticker=ticker, cik=cik, name=None, note=f"error: {e}"))
    return rows
