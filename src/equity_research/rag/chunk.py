"""Chunk sectioned filing text into retrievable, citable units.

Each chunk carries the filing's accession/form/filed-date and its section label,
so a retrieved passage can always be cited back to a specific filing and section
(e.g. "ACN FY2025 10-K, Item 1A. Risk Factors"). Chunks overlap slightly so a
fact spanning a boundary isn't lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from equity_research.rag.documents import Section


@dataclass
class Chunk:
    cik: int
    accession: str
    form: str
    filed_date: date | None
    section: str
    chunk_index: int
    text: str

    @property
    def chunk_id(self) -> str:
        return f"{self.accession}:{self.section}:{self.chunk_index}"


def _split_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split on blank lines, then hard-split any paragraph longer than max_chars.

    Real filings contain very long single paragraphs (and de-tagged tables); a
    paragraph-only splitter would emit one giant chunk for them, so we sentence-
    pack the overflow.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            out.append(p)
            continue
        # Greedily pack sentences (fallback: raw character windows).
        pieces = re.split(r"(?<=[.;])\s+", p)
        buf = ""
        for piece in pieces:
            if buf and len(buf) + len(piece) + 1 > max_chars:
                out.append(buf.strip())
                buf = piece
            elif len(piece) > max_chars:  # a single monster token/run
                for i in range(0, len(piece), max_chars):
                    out.append(piece[i : i + max_chars])
                buf = ""
            else:
                buf = f"{buf} {piece}" if buf else piece
        if buf.strip():
            out.append(buf.strip())
    return out


def chunk_sections(
    sections: list[Section],
    *,
    cik: int,
    accession: str,
    form: str,
    filed_date: date | None,
    target_chars: int = 1800,
    overlap_chars: int = 200,
) -> list[Chunk]:
    """Greedily pack paragraphs into ~target_chars chunks, with overlap."""
    chunks: list[Chunk] = []
    for section in sections:
        paras = _split_paragraphs(section.text, target_chars)
        buf = ""
        idx = 0
        for para in paras:
            if buf and len(buf) + len(para) + 2 > target_chars:
                chunks.append(
                    Chunk(cik, accession, form, filed_date, section.label, idx, buf.strip())
                )
                idx += 1
                # Start next buffer with a tail overlap of the previous chunk.
                buf = buf[-overlap_chars:] + "\n\n" + para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf.strip():
            chunks.append(
                Chunk(cik, accession, form, filed_date, section.label, idx, buf.strip())
            )
    return chunks
