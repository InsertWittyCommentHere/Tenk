"""SQLite storage for the data spine.

Schema mirrors the canonical models and preserves point-in-time history: the
`xbrl_facts` table keeps every (concept, period, filing) row, so restatements
are visible rather than overwritten. The unique key includes the accession, so
re-ingesting is idempotent.

This is deliberately plain SQL (stdlib `sqlite3`) — local-first, zero-setup. The
DESIGN.md target is Postgres+Timescale; the `Database` surface is small enough
to reimplement there without touching callers.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from equity_research.models import Fact, SubmissionsIndex
from equity_research.models.filings import Filing
from equity_research.models.provenance import Provenance

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    cik INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    tickers TEXT,
    exchanges TEXT,
    sic TEXT,
    sic_description TEXT,
    fiscal_year_end TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filings (
    accession TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    form TEXT NOT NULL,
    filed_date TEXT NOT NULL,
    report_date TEXT,
    primary_document TEXT,
    primary_doc_description TEXT,
    is_xbrl INTEGER DEFAULT 0,
    size INTEGER,
    FOREIGN KEY (cik) REFERENCES companies(cik)
);
CREATE INDEX IF NOT EXISTS idx_filings_cik_form ON filings(cik, form, filed_date);

CREATE TABLE IF NOT EXISTS xbrl_facts (
    cik INTEGER NOT NULL,
    taxonomy TEXT NOT NULL,
    concept TEXT NOT NULL,
    unit TEXT NOT NULL,
    value REAL NOT NULL,
    period_start TEXT,
    period_end TEXT NOT NULL,
    is_instant INTEGER DEFAULT 0,
    fiscal_year INTEGER,
    fiscal_period TEXT,
    frame TEXT,
    form TEXT,
    accession TEXT,
    filed_date TEXT,
    source_url TEXT,
    PRIMARY KEY (cik, concept, unit, period_start, period_end, accession)
);
CREATE INDEX IF NOT EXISTS idx_facts_cik_concept ON xbrl_facts(cik, concept);
CREATE INDEX IF NOT EXISTS idx_facts_period ON xbrl_facts(cik, concept, period_end);
"""


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writes -------------------------------------------------------------

    def upsert_company(self, idx: SubmissionsIndex) -> None:
        self.conn.execute(
            """INSERT INTO companies (cik, name, tickers, exchanges, sic, sic_description,
                   fiscal_year_end, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(cik) DO UPDATE SET
                   name=excluded.name, tickers=excluded.tickers, exchanges=excluded.exchanges,
                   sic=excluded.sic, sic_description=excluded.sic_description,
                   fiscal_year_end=excluded.fiscal_year_end, updated_at=CURRENT_TIMESTAMP""",
            (
                idx.cik,
                idx.name,
                ",".join(idx.tickers),
                ",".join(idx.exchanges),
                idx.sic,
                idx.sic_description,
                idx.fiscal_year_end,
            ),
        )
        self.conn.commit()

    def upsert_filings(self, filings: list[Filing]) -> int:
        rows = [
            (
                f.accession,
                f.cik,
                f.form,
                f.filed_date.isoformat(),
                f.report_date.isoformat() if f.report_date else None,
                f.primary_document,
                f.primary_doc_description,
                int(f.is_xbrl),
                f.size,
            )
            for f in filings
        ]
        self.conn.executemany(
            """INSERT INTO filings (accession, cik, form, filed_date, report_date,
                   primary_document, primary_doc_description, is_xbrl, size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(accession) DO UPDATE SET
                   form=excluded.form, filed_date=excluded.filed_date,
                   report_date=excluded.report_date,
                   primary_document=excluded.primary_document,
                   primary_doc_description=excluded.primary_doc_description,
                   is_xbrl=excluded.is_xbrl, size=excluded.size""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_facts(self, facts: list[Fact]) -> int:
        rows = [
            (
                f.cik,
                f.taxonomy,
                f.concept,
                f.unit,
                f.value,
                f.period_start.isoformat() if f.period_start else None,
                f.period_end.isoformat(),
                int(f.is_instant),
                f.fiscal_year,
                f.fiscal_period,
                f.frame,
                f.form,
                f.accession,
                f.filed_date.isoformat() if f.filed_date else None,
                f.provenance.url,
            )
            for f in facts
        ]
        # period_start NULL participates in the PK; SQLite treats NULLs as distinct,
        # so use INSERT OR REPLACE keyed on the full natural key via the table PK.
        self.conn.executemany(
            """INSERT OR REPLACE INTO xbrl_facts (cik, taxonomy, concept, unit, value,
                   period_start, period_end, is_instant, fiscal_year, fiscal_period,
                   frame, form, accession, filed_date, source_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # -- reads --------------------------------------------------------------

    def get_facts_for_concept(self, cik: int, concept: str) -> list[Fact]:
        cur = self.conn.execute(
            "SELECT * FROM xbrl_facts WHERE cik=? AND concept=? ORDER BY period_end",
            (cik, concept),
        )
        return [self._row_to_fact(r) for r in cur.fetchall()]

    def get_all_facts(self, cik: int) -> list[Fact]:
        cur = self.conn.execute("SELECT * FROM xbrl_facts WHERE cik=?", (cik,))
        return [self._row_to_fact(r) for r in cur.fetchall()]

    def count_facts(self, cik: int) -> int:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM xbrl_facts WHERE cik=?", (cik,))
        return int(cur.fetchone()["n"])

    def list_filings(self, cik: int, form: str | None = None, limit: int = 50) -> list[Filing]:
        if form:
            cur = self.conn.execute(
                "SELECT * FROM filings WHERE cik=? AND form=? ORDER BY filed_date DESC LIMIT ?",
                (cik, form, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM filings WHERE cik=? ORDER BY filed_date DESC LIMIT ?",
                (cik, limit),
            )
        return [self._row_to_filing(r) for r in cur.fetchall()]

    # -- mapping ------------------------------------------------------------

    @staticmethod
    def _row_to_fact(r: sqlite3.Row) -> Fact:
        return Fact(
            cik=r["cik"],
            taxonomy=r["taxonomy"],
            concept=r["concept"],
            unit=r["unit"],
            value=r["value"],
            period_start=_d(r["period_start"]),
            period_end=_d(r["period_end"]),
            is_instant=bool(r["is_instant"]),
            fiscal_year=r["fiscal_year"],
            fiscal_period=r["fiscal_period"],
            frame=r["frame"],
            form=r["form"],
            accession=r["accession"],
            filed_date=_d(r["filed_date"]),
            provenance=Provenance(
                source="SEC EDGAR",
                url=r["source_url"],
                accession=r["accession"],
                form=r["form"],
                filed_date=_d(r["filed_date"]),
            ),
        )

    @staticmethod
    def _row_to_filing(r: sqlite3.Row) -> Filing:
        return Filing(
            cik=r["cik"],
            accession=r["accession"],
            form=r["form"],
            filed_date=_d(r["filed_date"]),
            report_date=_d(r["report_date"]),
            primary_document=r["primary_document"],
            primary_doc_description=r["primary_doc_description"],
            is_xbrl=bool(r["is_xbrl"]),
            size=r["size"],
        )
