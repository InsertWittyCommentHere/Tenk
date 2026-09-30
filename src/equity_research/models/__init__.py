"""Canonical, vendor-neutral data models.

These types are what the rest of the platform speaks. Providers translate their
vendor-specific payloads into these models, so swapping EDGAR for a paid feed
(or adding Bloomberg/FactSet later) never touches agent or analytics code.
"""

from equity_research.models.provenance import Provenance
from equity_research.models.filings import Filing, SubmissionsIndex
from equity_research.models.financials import Fact, FactSeries

__all__ = ["Provenance", "Filing", "SubmissionsIndex", "Fact", "FactSeries"]
