"""Index filings into the chunk store: fetch -> parse -> section -> chunk -> store."""

from __future__ import annotations

from dataclasses import dataclass

from equity_research.models.filings import Filing
from equity_research.providers.edgar import EdgarProvider
from equity_research.rag.chunk import chunk_sections
from equity_research.rag.documents import extract_sections, parse_filing_html
from equity_research.rag.store import ChunkStore


@dataclass
class IndexResult:
    accession: str
    form: str
    sections: int
    chunks: int
    skipped: bool = False


def index_filing(
    provider: EdgarProvider, store: ChunkStore, filing: Filing, *, force: bool = False
) -> IndexResult:
    """Fetch a single filing's primary document and index its chunks."""
    if not force and store.has_filing(filing.accession):
        return IndexResult(filing.accession, filing.form, 0, 0, skipped=True)
    url = filing.primary_document_url
    if not url:
        return IndexResult(filing.accession, filing.form, 0, 0, skipped=True)

    html = provider.fetch_document(url)
    text = parse_filing_html(html)
    sections = extract_sections(text)
    chunks = chunk_sections(
        sections,
        cik=filing.cik,
        accession=filing.accession,
        form=filing.form,
        filed_date=filing.filed_date,
    )
    n = store.add_chunks(chunks)
    return IndexResult(filing.accession, filing.form, len(sections), n)
