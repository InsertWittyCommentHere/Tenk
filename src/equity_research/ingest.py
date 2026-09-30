"""Ingestion orchestration: pull from a FilingsProvider, persist to the Database.

This is the seam between the provider layer and storage. It is provider-agnostic
(takes any `FilingsProvider`), so the same flow works when EDGAR is swapped for a
licensed feed later.
"""

from __future__ import annotations

from dataclasses import dataclass

from equity_research.providers.base import FilingsProvider
from equity_research.storage import Database


@dataclass
class IngestResult:
    cik: int
    name: str
    filings_ingested: int
    facts_ingested: int


def ingest_company(provider: FilingsProvider, db: Database, cik: int) -> IngestResult:
    """Fetch submissions + all XBRL facts for a CIK and persist them idempotently."""
    idx = provider.get_submissions(cik)
    db.upsert_company(idx)
    n_filings = db.upsert_filings(idx.filings)

    facts = provider.get_facts(cik)
    n_facts = db.upsert_facts(facts)

    return IngestResult(
        cik=cik,
        name=idx.name,
        filings_ingested=n_filings,
        facts_ingested=n_facts,
    )
