"""Market-aware valuation context: multiples + reverse DCF.

Given a current price and the deterministic metric store, compute the figures a
retail user actually wants when deciding: P/E, FCF yield, market cap, and — the
flagship — the **reverse-DCF implied growth** (the FCF growth rate the current
price is baking in). That reframes valuation as "what must you believe?", which
is more honest than a single point estimate.

All inputs are cited financials; the only external number is the price.
"""

from __future__ import annotations

from dataclasses import dataclass

from equity_research.analytics.dcf import reverse_dcf
from equity_research.analytics.metrics import MetricStore
from equity_research.providers.prices import Quote


@dataclass
class MarketContext:
    price: float
    price_as_of: str | None
    market_cap: float | None
    pe_ratio: float | None
    fcf_yield: float | None
    implied_fcf_growth: float | None  # reverse DCF
    assumptions: dict

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "price_as_of": self.price_as_of,
            "market_cap": self.market_cap,
            "pe_ratio": self.pe_ratio,
            "fcf_yield": self.fcf_yield,
            "implied_fcf_growth": self.implied_fcf_growth,
            "assumptions": self.assumptions,
        }


def market_context(
    store: MetricStore,
    quote: Quote,
    *,
    discount_rate: float = 0.09,
    terminal_growth: float = 0.025,
) -> MarketContext | None:
    periods = store.periods("revenue")
    if not periods:
        return None
    pe = periods[-1]
    shares = store.value("shares_outstanding", pe) or store.value("shares_diluted", pe)
    eps = store.value("eps_diluted", pe)
    fcf_c = store.derived("free_cash_flow").get(pe)
    cash = store.value("cash_and_equivalents", pe) or 0.0
    debt = store.value("long_term_debt", pe) or 0.0
    net_cash = cash - debt

    market_cap = quote.price * shares if shares else None
    pe_ratio = quote.price / eps if eps else None
    fcf_yield = (fcf_c.value / market_cap) if (fcf_c and market_cap) else None

    implied = None
    if fcf_c and shares:
        try:
            implied = reverse_dcf(
                market_price_per_share=quote.price,
                base_fcf=fcf_c.value,
                shares_outstanding=shares,
                terminal_growth=terminal_growth,
                discount_rate=discount_rate,
                net_cash=net_cash,
            )
        except (ValueError, ZeroDivisionError):
            implied = None

    return MarketContext(
        price=quote.price,
        price_as_of=quote.as_of.isoformat() if quote.as_of else None,
        market_cap=market_cap,
        pe_ratio=pe_ratio,
        fcf_yield=fcf_yield,
        implied_fcf_growth=implied,
        assumptions={"discount_rate": discount_rate, "terminal_growth": terminal_growth},
    )
