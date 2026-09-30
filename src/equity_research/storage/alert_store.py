"""Persistence + change-detection for alerts.

An alerts product needs to know what is *new*, not just recompute the same list
every load. This store records each fired alert under a stable fingerprint
(cik|kind|message), so re-running the engine on unchanged data records nothing
new, while a genuinely new filing/trend/insider-cluster surfaces as new. Alerts
can be acknowledged so they drop out of the "unread" feed.

Lives in the same SQLite file as the data spine but owns its own table, so it
stays decoupled from `Database`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a circular import (alerts -> storage -> alert_store)
    from equity_research.alerts import Alert

SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_state (
    fingerprint TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    ticker TEXT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    as_of TEXT,
    sources TEXT,
    first_seen TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_ticker ON alert_state(ticker, acknowledged);
"""


def fingerprint(cik: int, alert: Alert) -> str:
    """Stable id for an alert: same underlying state -> same fingerprint.

    Uses the alert's explicit `dedup_key` when set (so a volatile message like a
    rolling insider count doesn't re-fire daily); falls back to the message.
    """
    key = f"{cik}|{alert.kind}|{alert.dedup_key or alert.message}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


@dataclass
class StoredAlert:
    fingerprint: str
    cik: int
    ticker: str | None
    kind: str
    severity: str
    message: str
    as_of: str | None
    sources: list[str]
    first_seen: str
    acknowledged: bool
    rank: int  # severity rank for sorting (high=3..info=1)

    _RANK = {"high": 3, "warn": 2, "info": 1}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlertStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "AlertStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record(self, cik: int, ticker: str | None, alerts: list[Alert]) -> list[Alert]:
        """Persist alerts; return only the ones not seen before (the 'new' set)."""
        new: list[Alert] = []
        for a in alerts:
            fp = fingerprint(cik, a)
            exists = self.conn.execute(
                "SELECT 1 FROM alert_state WHERE fingerprint=?", (fp,)
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                """INSERT INTO alert_state (fingerprint, cik, ticker, kind, severity, message,
                       as_of, sources, first_seen, acknowledged)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    fp, cik, ticker, a.kind, a.severity, a.message,
                    a.as_of.isoformat() if a.as_of else None,
                    json.dumps(a.sources), _utcnow(),
                ),
            )
            new.append(a)
        self.conn.commit()
        return new

    def feed(
        self, *, ticker: str | None = None, only_unread: bool = False, limit: int = 100
    ) -> list[StoredAlert]:
        """Cross-company (or per-ticker) alert inbox, newest + highest severity first."""
        where = []
        params: list[object] = []
        if ticker:
            where.append("ticker = ?")
            params.append(ticker)
        if only_unread:
            where.append("acknowledged = 0")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM alert_state {clause} ORDER BY first_seen DESC LIMIT ?", params
        ).fetchall()
        out = [self._row(r) for r in rows]
        return sorted(out, key=lambda a: (a.rank, a.first_seen), reverse=True)

    def acknowledge(self, fingerprint: str) -> bool:
        cur = self.conn.execute(
            "UPDATE alert_state SET acknowledged=1, acknowledged_at=? WHERE fingerprint=? "
            "AND acknowledged=0",
            (_utcnow(), fingerprint),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def unread_count(self, ticker: str | None = None) -> int:
        if ticker:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM alert_state WHERE acknowledged=0 AND ticker=?",
                (ticker,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM alert_state WHERE acknowledged=0"
            ).fetchone()
        return int(row["n"])

    @staticmethod
    def _row(r: sqlite3.Row) -> StoredAlert:
        return StoredAlert(
            fingerprint=r["fingerprint"], cik=r["cik"], ticker=r["ticker"], kind=r["kind"],
            severity=r["severity"], message=r["message"], as_of=r["as_of"],
            sources=json.loads(r["sources"] or "[]"), first_seen=r["first_seen"],
            acknowledged=bool(r["acknowledged"]),
            rank=StoredAlert._RANK.get(r["severity"], 0),
        )
