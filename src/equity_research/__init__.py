"""Equity Research — AI investment research platform.

Phase 1 implements the SEC EDGAR data spine: the authoritative, free source of
truth for company financials and filings. Everything downstream (analytics,
RAG, agents) is only as trustworthy as this layer, so every fact carries
provenance and point-in-time metadata.
"""

__version__ = "0.1.0"
