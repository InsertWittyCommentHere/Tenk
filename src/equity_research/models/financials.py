"""Canonical financial-fact models.

An XBRL "fact" is one reported number for one concept over one period, as stated
in one filing. We keep every fact (not just the latest) so we can answer
point-in-time questions ("what did the 2021 10-K say revenue was?") and detect
restatements. The `Fact` is the atomic unit of the data spine.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from equity_research.models.provenance import Provenance


class Fact(BaseModel):
    """One reported value for one concept, one period, one filing."""

    cik: int
    taxonomy: str = Field(description="e.g. 'us-gaap', 'dei', 'ifrs-full'.")
    concept: str = Field(description="XBRL concept/tag, e.g. 'Revenues'.")
    unit: str = Field(description="e.g. 'USD', 'shares', 'USD/shares'.")
    value: float

    # Period: a duration fact has start+end; an instant fact (balance sheet) has end only.
    period_start: date | None = None
    period_end: date
    is_instant: bool = Field(
        default=False, description="True for point-in-time (balance sheet) facts."
    )

    # Fiscal context as reported by the filer.
    fiscal_year: int | None = None
    fiscal_period: str | None = Field(default=None, description="'FY', 'Q1'..'Q4'.")
    frame: str | None = Field(default=None, description="SEC calendar frame, e.g. 'CY2023Q1'.")

    # Point-in-time + provenance.
    form: str | None = None
    accession: str | None = None
    filed_date: date | None = None
    provenance: Provenance

    @property
    def duration_days(self) -> int | None:
        if self.period_start is None:
            return None
        return (self.period_end - self.period_start).days

    @property
    def is_annual(self) -> bool:
        """Heuristic: a ~year-long duration fact."""
        d = self.duration_days
        return d is not None and 350 <= d <= 380


class FactSeries(BaseModel):
    """All facts for a single (concept, unit) across periods, plus the canonical metric name."""

    metric: str = Field(description="Canonical metric name, e.g. 'revenue'.")
    concept: str
    unit: str
    facts: list[Fact] = Field(default_factory=list)

    def annual(self) -> list[Fact]:
        return sorted((f for f in self.facts if f.is_annual), key=lambda f: f.period_end)

    def latest(self) -> Fact | None:
        return max(self.facts, key=lambda f: f.period_end, default=None)
