"""Offline tests for EDGAR payload parsing (no network)."""

from __future__ import annotations

from datetime import date

from equity_research.providers.edgar import EdgarProvider


def test_parse_recent_filings_column_oriented():
    recent = {
        "accessionNumber": ["0001467373-24-000001", "0001467373-24-000002"],
        "form": ["10-K", "8-K"],
        "filingDate": ["2024-10-10", "2024-09-26"],
        "reportDate": ["2024-08-31", ""],
        "primaryDocument": ["acn-20240831.htm", "d8k.htm"],
        "primaryDocDescription": ["10-K", "8-K"],
        "isXBRL": [1, 0],
        "size": [1000, 500],
    }
    filings = EdgarProvider._parse_recent_filings(1467373, recent)
    assert len(filings) == 2
    tenk = filings[0]
    assert tenk.form == "10-K"
    assert tenk.filed_date == date(2024, 10, 10)
    assert tenk.report_date == date(2024, 8, 31)
    assert tenk.accession_nodash == "000146737324000001"
    assert tenk.primary_document_url.endswith("acn-20240831.htm")


def test_row_to_fact_duration_and_instant():
    duration_row = {
        "val": 64896525000,
        "start": "2023-09-01",
        "end": "2024-08-31",
        "fy": 2024,
        "fp": "FY",
        "form": "10-K",
        "accn": "0001467373-24-000001",
        "filed": "2024-10-10",
        "frame": "CY2024",
    }
    f = EdgarProvider._row_to_fact(
        1467373, "us-gaap", "Revenues", "USD", duration_row, source_url="http://x"
    )
    assert f.value == 64896525000
    assert f.is_instant is False
    assert f.is_annual is True
    assert f.provenance.accession == "0001467373-24-000001"

    instant_row = {"val": 5000.0, "end": "2024-08-31", "fy": 2024, "fp": "FY", "form": "10-K"}
    g = EdgarProvider._row_to_fact(
        1467373, "us-gaap", "Assets", "USD", instant_row, source_url="http://x"
    )
    assert g.is_instant is True
    assert g.period_start is None


def test_row_to_fact_skips_incomplete():
    assert (
        EdgarProvider._row_to_fact(1, "us-gaap", "X", "USD", {"end": "2024-08-31"}, source_url="u")
        is None
    )
    assert (
        EdgarProvider._row_to_fact(1, "us-gaap", "X", "USD", {"val": 1.0}, source_url="u") is None
    )
