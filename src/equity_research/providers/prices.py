"""Free price providers (no pandas, no API key) — Yahoo primary, Stooq fallback.

Connecting the DCF engine to the actual market price unlocks the flagship retail
feature: a **reverse DCF** ("what FCF growth is the price implying?") and simple
multiples (P/E, FCF yield). We hit Yahoo's public chart JSON (covers US listings
and ADRs) and fall back to Stooq's CSV quote, both via httpx.

`redistributable=False`: these quotes are fine as a personal-use *input* but must
not be republished as raw data. Like every provider they sit behind a contract,
so a fund tier can swap in Bloomberg/FactSet without touching callers. Failures
degrade gracefully (return None) — price is enrichment, never load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
STOOQ_URL = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; EquityResearch/0.1)"}

# Exchanges Stooq serves with the ".us" suffix. Non-US tickers (ADRs on foreign
# venues, Euronext, etc.) aren't reliably available — we skip them rather than guess.
_US_EXCHANGES = {"NYSE", "NASDAQ", "NYSE ARCA", "NYSEAMERICAN", "BATS", None}


@dataclass
class Quote:
    ticker: str
    price: float
    as_of: date | None
    source: str = "Stooq"


class YahooProvider:
    """Latest quote via Yahoo's public chart JSON (US listings + ADRs)."""

    name = "Yahoo Finance"
    redistributable = False

    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.Client(timeout=timeout, follow_redirects=True, headers=_UA)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "YahooProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_quote(self, ticker: str, *, exchange: str | None = None) -> Quote | None:
        try:
            resp = self._client.get(YAHOO_URL.format(symbol=ticker.upper()))
            resp.raise_for_status()
            return self._parse(ticker, resp.json())
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    @staticmethod
    def _parse(ticker: str, payload: dict) -> Quote | None:
        results = (payload.get("chart") or {}).get("result") or []
        if not results:
            return None
        meta = results[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            return None
        ts = meta.get("regularMarketTime")
        as_of = (
            datetime.fromtimestamp(ts, tz=timezone.utc).date() if isinstance(ts, (int, float))
            else None
        )
        return Quote(ticker=ticker.upper(), price=float(price), as_of=as_of, source="Yahoo Finance")


class StooqProvider:
    name = "Stooq"
    redistributable = False

    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "StooqProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _symbol(ticker: str) -> str:
        # Stooq uses lowercase + .us for US listings (e.g. "acn" -> "acn.us").
        return f"{ticker.lower()}.us"

    def get_quote(self, ticker: str, *, exchange: str | None = None) -> Quote | None:
        if exchange is not None and exchange.upper() not in _US_EXCHANGES:
            return None
        try:
            resp = self._client.get(STOOQ_URL.format(symbol=self._symbol(ticker)))
            resp.raise_for_status()
            return self._parse(ticker, resp.text)
        except (httpx.HTTPError, ValueError):
            return None

    @staticmethod
    def _parse(ticker: str, csv_text: str) -> Quote | None:
        lines = [ln for ln in csv_text.strip().splitlines() if ln]
        if len(lines) < 2:
            return None
        header = [h.strip().lower() for h in lines[0].split(",")]
        row = lines[1].split(",")
        rec = dict(zip(header, row))
        close = rec.get("close")
        if not close or close.upper() == "N/D":
            return None
        try:
            price = float(close)
        except ValueError:
            return None
        as_of = None
        if rec.get("date") and rec["date"].upper() != "N/D":
            try:
                as_of = datetime.strptime(rec["date"], "%Y-%m-%d").date()
            except ValueError:
                as_of = None
        return Quote(ticker=ticker.upper(), price=price, as_of=as_of)


class PriceFeed:
    """Composite provider: tries each source in order, returns the first quote."""

    name = "PriceFeed"
    redistributable = False

    def __init__(self) -> None:
        self._providers = [YahooProvider(), StooqProvider()]

    def close(self) -> None:
        for p in self._providers:
            p.close()

    def __enter__(self) -> "PriceFeed":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_quote(self, ticker: str, *, exchange: str | None = None) -> Quote | None:
        for p in self._providers:
            q = p.get_quote(ticker, exchange=exchange)
            if q is not None:
                return q
        return None
