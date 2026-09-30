"""Data providers.

Each provider implements a contract from `base.py`. Agents and analytics depend
only on the contracts, never on a concrete vendor — so the retail tier can bind
`EstimatesProvider -> FMP` while a fund binds `EstimatesProvider -> VisibleAlpha`
with zero change to downstream code (DESIGN.md §2.2).

Phase 1 ships only the EDGAR `FilingsProvider`. The other contracts are defined
now (so the shape is locked) but implemented later.
"""

from equity_research.providers.base import (
    EstimatesProvider,
    FilingsProvider,
    MacroProvider,
    NewsProvider,
    OwnershipProvider,
    PricesProvider,
    TranscriptsProvider,
)
from equity_research.providers.edgar import EdgarProvider

__all__ = [
    "FilingsProvider",
    "PricesProvider",
    "EstimatesProvider",
    "TranscriptsProvider",
    "NewsProvider",
    "MacroProvider",
    "OwnershipProvider",
    "EdgarProvider",
]
