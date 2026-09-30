"""SQLite FTS5 store for filing chunks — local-first keyword retrieval.

FTS5 (built into SQLite) gives BM25-ranked full-text search with zero external
dependencies or API cost, which fits the personal/retail tier. Metadata
(cik, accession, form, section) lives alongside the indexed text so retrieval can
be filtered — e.g. "search only Risk Factors in 10-Ks". The `search` method
returns chunks already carrying provenance for citation.

A future embedding/vector backend can implement the same `search` shape behind a
`Retriever` protocol without changing the agent tools.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from equity_research.models.provenance import Provenance
from equity_research.rag.chunk import Chunk

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    chunk_id UNINDEXED,
    cik UNINDEXED,
    accession UNINDEXED,
    form UNINDEXED,
    filed_date UNINDEXED,
    section,
    text,
    tokenize = 'porter unicode61'
);
"""


@dataclass
class RetrievedChunk:
    chunk_id: str
    cik: int
    accession: str
    form: str
    filed_date: date | None
    section: str
    text: str
    score: float  # FTS5 bm25 (lower = better match); we negate to "higher = better"

    @property
    def provenance(self) -> Provenance:
        fd = date.fromisoformat(self.filed_date) if isinstance(self.filed_date, str) else self.filed_date
        return Provenance(source="SEC EDGAR", accession=self.accession, form=self.form, filed_date=fd)

    def citation(self) -> str:
        fd = f" filed {self.filed_date}" if self.filed_date else ""
        return f"{self.form} {self.accession}{fd} — {self.section}"


def _sanitize_query(q: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression (OR of bare terms)."""
    terms = re.findall(r"[A-Za-z0-9]+", q)
    # Quote each term to avoid FTS5 operator injection; OR them for recall.
    return " OR ".join(f'"{t}"' for t in terms) if terms else '""'


class ChunkStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ChunkStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def has_filing(self, accession: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM chunks WHERE accession=? LIMIT 1", (accession,)
        )
        return cur.fetchone() is not None

    def add_chunks(self, chunks: list[Chunk]) -> int:
        rows = [
            (
                c.chunk_id, c.cik, c.accession, c.form,
                c.filed_date.isoformat() if c.filed_date else None,
                c.section, c.text,
            )
            for c in chunks
        ]
        self.conn.executemany(
            "INSERT INTO chunks (chunk_id, cik, accession, form, filed_date, section, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def search(
        self,
        query: str,
        *,
        cik: int | None = None,
        form: str | None = None,
        section_like: str | None = None,
        limit: int = 5,
    ) -> list[RetrievedChunk]:
        """BM25-ranked keyword search with optional metadata filters."""
        where = ["chunks MATCH ?"]
        params: list[object] = [_sanitize_query(query)]
        if cik is not None:
            where.append("cik = ?")
            params.append(cik)
        if form is not None:
            where.append("form = ?")
            params.append(form)
        if section_like is not None:
            where.append("section LIKE ?")
            params.append(f"%{section_like}%")
        params.append(limit)
        sql = (
            "SELECT chunk_id, cik, accession, form, filed_date, section, text, "
            "bm25(chunks) AS score FROM chunks "
            f"WHERE {' AND '.join(where)} ORDER BY score LIMIT ?"
        )
        cur = self.conn.execute(sql, params)
        out: list[RetrievedChunk] = []
        for r in cur.fetchall():
            out.append(
                RetrievedChunk(
                    chunk_id=r["chunk_id"], cik=int(r["cik"]), accession=r["accession"],
                    form=r["form"], filed_date=r["filed_date"], section=r["section"],
                    text=r["text"], score=-float(r["score"]),
                )
            )
        return out
