"""Provider contracts (the vendor-swap boundary).

These are `Protocol`s, not base classes, so any object with the right methods
satisfies them — including thin adapters around paid feeds added later. Each
carries a `name` and `redistributable` flag; the latter governs whether raw
values from this source may be embedded in user-facing output or only used as
internal input (DESIGN.md §2.5 data-licensing guardrails).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from equity_research.models import Fact, FactSeries, SubmissionsIndex


@runtime_checkable
class FilingsProvider(Protocol):
    """Authoritative filings + structured financials. Implemented by EDGAR in Phase 1."""

    name: str
    redistributable: bool

    def get_submissions(self, cik: int) -> SubmissionsIndex:
        """Company identity + recent filings index."""
        ...

    def get_facts(self, cik: int) -> list[Fact]:
        """All XBRL facts for the company (every concept, every period, point-in-time)."""
        ...

    def get_concept(self, cik: int, taxonomy: str, concept: str) -> FactSeries:
        """Time series for a single XBRL concept."""
        ...


@runtime_checkable
class PricesProvider(Protocol):
    """Market prices. Implemented by Stooq (free, dependency-free) in Phase 5."""

    name: str
    redistributable: bool

    def get_quote(self, ticker: str) -> object:
        """Latest available price for a ticker, or None if unavailable."""
        ...


@runtime_checkable
class EstimatesProvider(Protocol):
    """Analyst consensus estimates and price targets. Retail: FMP; Fund: Visible Alpha."""

    name: str
    redistributable: bool

    def get_consensus(self, ticker: str) -> object: ...


@runtime_checkable
class TranscriptsProvider(Protocol):
    """Earnings-call transcripts. IR site / licensed transcript API."""

    name: str
    redistributable: bool

    def get_transcripts(self, ticker: str) -> object: ...


@runtime_checkable
class NewsProvider(Protocol):
    """News and catalyst events. GDELT (free) / Benzinga (paid)."""

    name: str
    redistributable: bool

    def get_news(self, ticker: str, start: date, end: date) -> object: ...


@runtime_checkable
class MacroProvider(Protocol):
    """Macro series (rates, FX, CPI). FRED."""

    name: str
    redistributable: bool

    def get_series(self, series_id: str) -> object: ...


@runtime_checkable
class OwnershipProvider(Protocol):
    """Insider (Form 4) and institutional (13F) activity. EDGAR-derived."""

    name: str
    redistributable: bool

    def get_insider_transactions(self, cik: int) -> object: ...
