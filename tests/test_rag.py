"""Offline tests for the RAG layer: parsing, sectioning, chunking, retrieval."""

from __future__ import annotations

from datetime import date

from equity_research.rag.chunk import chunk_sections
from equity_research.rag.documents import Section, extract_sections, parse_filing_html
from equity_research.rag.store import ChunkStore

# Section bodies are padded to be realistically long (real filings have large
# sections; extract_sections filters out near-empty slices below ~200 chars).
_PAD = " Additional context and disclosure language follows in the filing body."
SAMPLE_HTML = f"""
<html><body>
<p>Table of Contents</p>
<p>Item 1A. Risk Factors ..... 12</p>
<h2>Item 1. Business</h2>
<p>Accenture is a leading global professional services company.{_PAD * 4}</p>
<h2>Item 1A. Risk Factors</h2>
<p>Generative AI could disrupt our traditional consulting demand and pricing.{_PAD * 3}</p>
<p>Cybersecurity incidents could harm our reputation and results.{_PAD * 3}</p>
<h2>Item 7. Management's Discussion and Analysis</h2>
<p>New bookings were a record this year, supporting future revenue.{_PAD * 4}</p>
</body></html>
"""


def test_parse_strips_tags():
    text = parse_filing_html(SAMPLE_HTML)
    assert "Accenture is a leading" in text
    assert "<p>" not in text and "<h2>" not in text


def test_extract_sections_finds_real_body_not_toc():
    text = parse_filing_html(SAMPLE_HTML)
    sections = extract_sections(text)
    labels = [s.label for s in sections]
    assert "Item 1A. Risk Factors" in labels
    rf = next(s for s in sections if s.label == "Item 1A. Risk Factors")
    # Should capture the real risk-factor body, not the TOC line.
    assert "Generative AI could disrupt" in rf.text


def test_chunking_carries_metadata():
    sections = [Section("Item 1A. Risk Factors", "AI risk. " * 500, 0)]
    chunks = chunk_sections(
        sections, cik=1467373, accession="acc-1", form="10-K",
        filed_date=date(2025, 10, 10), target_chars=400,
    )
    assert len(chunks) > 1  # long section split into multiple chunks
    c = chunks[0]
    assert c.cik == 1467373 and c.accession == "acc-1" and c.section == "Item 1A. Risk Factors"
    assert c.chunk_id.startswith("acc-1:Item 1A. Risk Factors:")


def test_store_search_with_filters(tmp_path):
    sections = extract_sections(parse_filing_html(SAMPLE_HTML))
    chunks = chunk_sections(
        sections, cik=1467373, accession="acc-1", form="10-K", filed_date=date(2025, 10, 10)
    )
    with ChunkStore(tmp_path / "chunks.db") as store:
        assert not store.has_filing("acc-1")
        store.add_chunks(chunks)
        assert store.has_filing("acc-1")

        # Section-filtered search returns only Risk Factors passages.
        hits = store.search("generative AI disrupt", cik=1467373, section_like="Risk", limit=3)
        assert hits
        assert all("Risk Factors" in h.section for h in hits)
        assert "Generative AI" in hits[0].text
        # Provenance is intact.
        assert hits[0].provenance.accession == "acc-1"
        assert hits[0].provenance.source == "SEC EDGAR"

        # A term only in MD&A shouldn't surface under a Risk filter.
        rf_hits = store.search("bookings record", cik=1467373, section_like="Risk", limit=3)
        assert all("bookings were a record" not in h.text for h in rf_hits)


def test_query_sanitization_handles_punctuation(tmp_path):
    # FTS5 special chars in the query must not raise.
    with ChunkStore(tmp_path / "c.db") as store:
        store.add_chunks(
            chunk_sections(
                [Section("Item 1A. Risk Factors", "AI and data privacy risks abound.", 0)],
                cik=1, accession="a", form="10-K", filed_date=None,
            )
        )
        hits = store.search('AI / "privacy" (risks)*', cik=1, limit=3)
        assert hits  # did not crash, found the chunk
