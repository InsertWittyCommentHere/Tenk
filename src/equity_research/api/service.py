"""Service layer: assemble dashboard data from the deterministic engines.

Kept separate from the FastAPI routing so it can be unit-tested without HTTP and
reused by other surfaces (a future report generator, the CLI). Returns plain
JSON-serializable dicts; every figure carries its source where one exists.
"""

from __future__ import annotations

import time
from datetime import date

from equity_research.alerts import run_alerts
from equity_research.analytics.dcf import DCFAssumptions, scenario_range
from equity_research.analytics.market import market_context
from equity_research.analytics.metrics import MetricStore
from equity_research.analytics.ratios import compute_ratios
from equity_research.config import CompanyConfig
from equity_research.normalize.xbrl import fiscal_period_ends, reconstruct_statements
from equity_research.storage import Database

# Headline metrics shown on the dashboard overview.
OVERVIEW_METRICS = ["revenue", "net_income", "operating_income", "eps_diluted"]
OVERVIEW_DERIVED = ["free_cash_flow"]


def _cited(value, formula, sources) -> dict:
    return {
        "value": value,
        "formula": formula,
        "sources": sorted({p.accession for p in sources if p.accession}),
    }


# Short TTL cache for quotes so the watchlist home doesn't re-hit the price feed
# once per card on every load.
_QUOTE_CACHE: dict[str, tuple[float, object]] = {}
_QUOTE_TTL = 600  # 10 minutes


def _quote(company: CompanyConfig):
    hit = _QUOTE_CACHE.get(company.ticker)
    if hit and time.time() - hit[0] < _QUOTE_TTL:
        return hit[1]
    quote = None
    try:
        from equity_research.providers.prices import PriceFeed

        with PriceFeed() as prices:
            quote = prices.get_quote(company.ticker, exchange=company.exchange)
    except Exception:  # noqa: BLE001 — price is enrichment, never load-bearing
        quote = None
    _QUOTE_CACHE[company.ticker] = (time.time(), quote)
    return quote


def _market_block(company: CompanyConfig, store: MetricStore) -> dict | None:
    """Compute market context from a (cached) quote; None on any failure."""
    quote = _quote(company)
    if quote is None:
        return None
    ctx = market_context(store, quote)
    return ctx.to_dict() if ctx else None


def _verdict(dcf_base: float | None, price: float | None) -> str | None:
    if not dcf_base or not price:
        return None
    up = (dcf_base - price) / price
    if up > 0.15:
        return "cheap"
    if up < -0.15:
        return "expensive"
    return "fair"


def company_card(company: CompanyConfig, db: Database) -> dict:
    """Compact summary for the watchlist home grid (cheap; uses cached quote)."""
    store = MetricStore.from_db(db, company.cik)
    periods = store.periods("revenue")
    latest = periods[-1] if periods else None
    revenue = store.value("revenue", latest) if latest else None
    val = _valuation(store)
    dcf_base = val["per_share"]["base"] if val else None
    market = _market_block(company, store)
    price = market["price"] if market else None
    return {
        "ticker": company.ticker,
        "name": company.name,
        "latest_fiscal_year": latest.year if latest else None,
        "revenue": revenue,
        "price": price,
        "pe_ratio": market["pe_ratio"] if market else None,
        "implied_fcf_growth": market["implied_fcf_growth"] if market else None,
        "dcf_base": dcf_base,
        "verdict": _verdict(dcf_base, price),
    }


def company_overview(
    company: CompanyConfig, db: Database, *, as_of: date | None = None, with_price: bool = True
) -> dict:
    store = MetricStore.from_db(db, company.cik)
    periods = store.periods("revenue")
    latest = periods[-1] if periods else None

    headline = {}
    if latest:
        for m in OVERVIEW_METRICS:
            f = store.series(m).get(latest)
            if f:
                headline[m] = {
                    "value": f.value,
                    "fiscal_year": latest.year,
                    "sources": [f.accession] if f.accession else [],
                }
        for m in OVERVIEW_DERIVED:
            c = store.derived(m).get(latest)
            if c:
                headline[m] = {
                    "value": c.value,
                    "fiscal_year": latest.year,
                    "formula": c.formula,
                    "sources": sorted({p.accession for p in c.sources if p.accession}),
                }

    ratios = {}
    rep = compute_ratios(store, last_n=1)
    if rep:
        pe = sorted(rep)[-1]
        ratios = {"fiscal_year": pe.year, "values": rep[pe]}

    valuation = _valuation(store)
    alerts = [
        {
            "kind": a.kind, "severity": a.severity, "message": a.message,
            "as_of": a.as_of.isoformat() if a.as_of else None, "sources": a.sources,
        }
        for a in run_alerts(db, store, company.cik, as_of=as_of)
    ]

    return {
        "ticker": company.ticker,
        "name": company.name,
        "exchange": company.exchange,
        "currency": company.currency,
        "fiscal_year_end": company.fiscal_year_end,
        "peers": company.peers,
        "latest_fiscal_year": latest.year if latest else None,
        "headline": headline,
        "ratios": ratios,
        "valuation": valuation,
        "market": _market_block(company, store) if with_price else None,
        "alerts": alerts,
    }


# Simple in-process TTL cache for comps (live EDGAR calls are slow).
_COMPS_CACHE: dict[str, tuple[float, dict]] = {}
_COMPS_TTL = 6 * 3600  # 6 hours


def company_comps(company: CompanyConfig, *, user_agent: str, cache_dir=None) -> dict:
    """Latest-FY peer fundamentals (cached). Live EDGAR; degrades per-peer on error."""
    key = company.ticker
    now = time.time()
    hit = _COMPS_CACHE.get(key)
    if hit and now - hit[0] < _COMPS_TTL:
        return hit[1]

    from equity_research.analytics.comps import build_comps
    from equity_research.providers.edgar import EdgarProvider

    with EdgarProvider(user_agent=user_agent, cache_dir=cache_dir) as provider:
        rows = build_comps(provider, company.ticker, company.peers)
    payload = {
        "ticker": company.ticker,
        "rows": [
            {
                "ticker": r.ticker, "name": r.name, "note": r.note,
                "metrics": r.metrics,
            }
            for r in rows
        ],
    }
    _COMPS_CACHE[key] = (now, payload)
    return payload


def _valuation(store: MetricStore) -> dict | None:
    periods = store.periods("revenue")
    if not periods:
        return None
    pe = periods[-1]
    fcf = store.derived("free_cash_flow").get(pe)
    if fcf is None:
        return None
    cash = store.value("cash_and_equivalents", pe) or 0.0
    debt = store.value("long_term_debt", pe) or 0.0
    shares = store.value("shares_outstanding", pe) or store.value("shares_diluted", pe)
    base = DCFAssumptions(
        base_fcf=fcf.value, growth_rate=0.08, terminal_growth=0.025,
        discount_rate=0.09, net_cash=cash - debt, shares_outstanding=shares,
    )
    scen = scenario_range(base, bear_growth=0.03, bull_growth=0.12)
    return {
        "method": "DCF (default assumptions: r=9%, g_terminal=2.5%, 10y)",
        "base_fcf": fcf.value,
        "fcf_sources": sorted({p.accession for p in fcf.sources if p.accession}),
        "per_share": {
            name: (scen[name].value_per_share if scen[name].value_per_share else None)
            for name in ("bear", "base", "bull")
        },
    }


def company_filings(company: CompanyConfig, db: Database, *, form: str | None = None,
                    limit: int = 25) -> dict:
    rows = db.list_filings(company.cik, form=form, limit=limit)
    return {
        "ticker": company.ticker,
        "filings": [
            {
                "form": f.form,
                "filed_date": f.filed_date.isoformat() if f.filed_date else None,
                "report_date": f.report_date.isoformat() if f.report_date else None,
                "accession": f.accession,
                "document_url": f.primary_document_url,
            }
            for f in rows
        ],
    }


def company_trends(company: CompanyConfig, db: Database, *, years: int = 8) -> dict:
    """Per-year series for charting: revenue, FCF, operating & net margin."""
    store = MetricStore.from_db(db, company.cik)
    ends = store.periods("revenue")[-years:]
    fcf = store.derived("free_cash_flow")
    om = store.derived("operating_margin")
    nm = store.derived("net_margin")
    return {
        "ticker": company.ticker,
        "years": [pe.year for pe in ends],
        "revenue": [store.value("revenue", pe) for pe in ends],
        "free_cash_flow": [fcf[pe].value if pe in fcf else None for pe in ends],
        "operating_margin": [om[pe].value if pe in om else None for pe in ends],
        "net_margin": [nm[pe].value if pe in nm else None for pe in ends],
    }


def company_statements(company: CompanyConfig, db: Database, *, years: int = 5) -> dict:
    facts = db.get_all_facts(company.cik)
    stmts = reconstruct_statements(facts)
    cols = fiscal_period_ends(facts, n=years)  # anchor to fiscal year-ends
    out: dict[str, dict] = {}
    for stmt_name, metrics in stmts.items():
        out[stmt_name] = {}
        for metric, series in metrics.items():
            rows = [
                {
                    "fiscal_year": pe.year,
                    "value": series[pe].value,
                    "accession": series[pe].accession,
                }
                for pe in cols
                if pe in series
            ]
            if rows:
                out[stmt_name][metric] = rows
    return {"ticker": company.ticker, "statements": out}
