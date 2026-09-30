"""Provenance — the citation that travels with every fact.

A core platform rule (see DESIGN.md §2.2): no number reaches a user without a
verifiable source. This model is that citation. It is attached to facts at
ingestion and rendered as footnotes downstream.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Provenance(BaseModel):
    """Where a piece of data came from, precisely enough to re-fetch and verify."""

    source: str = Field(description="Provider/source name, e.g. 'SEC EDGAR'.")
    url: str | None = Field(default=None, description="Direct URL to the source document/endpoint.")
    accession: str | None = Field(
        default=None, description="SEC accession number, e.g. '0001467373-24-000123'."
    )
    form: str | None = Field(default=None, description="Filing form type, e.g. '10-K'.")
    filed_date: date | None = Field(default=None, description="Date the source was filed/published.")
    retrieved_at: datetime = Field(
        default_factory=_utcnow, description="When we fetched it (UTC)."
    )

    def cite(self) -> str:
        """Render a compact human-readable citation."""
        bits = [self.source]
        if self.form:
            bits.append(self.form)
        if self.filed_date:
            bits.append(f"filed {self.filed_date.isoformat()}")
        if self.accession:
            bits.append(self.accession)
        return " · ".join(bits)
