"""Retrieval over filings (Phase 3).

Fetches filing HTML from EDGAR, splits it into the standard 10-K/10-Q sections
("Item 1A. Risk Factors", "Item 7. MD&A", ...), chunks each section with
metadata, and indexes the chunks in a local SQLite FTS5 store for fast,
citation-preserving keyword retrieval. A `Retriever` protocol keeps the door
open for a vector/embedding backend later without changing callers.
"""

from equity_research.rag.documents import Section, parse_filing_html, extract_sections
from equity_research.rag.chunk import Chunk, chunk_sections
from equity_research.rag.store import ChunkStore, RetrievedChunk

__all__ = [
    "Section",
    "parse_filing_html",
    "extract_sections",
    "Chunk",
    "chunk_sections",
    "ChunkStore",
    "RetrievedChunk",
]
