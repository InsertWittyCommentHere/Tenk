"""Models for SEC filings and the submissions index."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Filing(BaseModel):
    """A single SEC filing (one row of the submissions index)."""

    cik: int
    accession: str = Field(description="Accession number, dashed form e.g. '0001467373-24-000123'.")
    form: str = Field(description="Form type, e.g. '10-K', '10-Q', '8-K', '4', 'DEF 14A'.")
    filed_date: date
    report_date: date | None = Field(
        default=None, description="Period of report (period_of_report)."
    )
    primary_document: str | None = Field(
        default=None, description="Primary document filename within the filing."
    )
    primary_doc_description: str | None = None
    is_xbrl: bool = False
    size: int | None = None

    @property
    def accession_nodash(self) -> str:
        return self.accession.replace("-", "")

    @property
    def index_url(self) -> str:
        return (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={self.cik}&type={self.form}"
        )

    @property
    def primary_document_url(self) -> str | None:
        if not self.primary_document:
            return None
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/"
            f"{self.accession_nodash}/{self.primary_document}"
        )


class SubmissionsIndex(BaseModel):
    """Parsed `submissions/CIK##########.json` — company identity + recent filings."""

    cik: int
    name: str
    tickers: list[str] = Field(default_factory=list)
    exchanges: list[str] = Field(default_factory=list)
    sic: str | None = None
    sic_description: str | None = None
    fiscal_year_end: str | None = Field(default=None, description="MMDD, e.g. '0831' for Accenture.")
    filings: list[Filing] = Field(default_factory=list)
