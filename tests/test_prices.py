"""Offline tests for the Stooq price provider (CSV parsing, no network)."""

from datetime import date

from equity_research.providers.prices import StooqProvider, YahooProvider


def test_yahoo_parse_valid():
    payload = {"chart": {"result": [{"meta": {
        "regularMarketPrice": 302.5, "regularMarketTime": 1781726403, "currency": "USD"}}]}}
    q = YahooProvider._parse("ACN", payload)
    assert q is not None and q.price == 302.5 and q.ticker == "ACN"
    assert q.source == "Yahoo Finance"


def test_yahoo_parse_empty():
    assert YahooProvider._parse("ACN", {"chart": {"result": []}}) is None
    assert YahooProvider._parse("ACN", {"chart": {"result": [{"meta": {}}]}}) is None


def test_parse_valid_quote():
    csv = "Symbol,Date,Time,Open,High,Low,Close,Volume\nACN.US,2026-06-16,22:00:05,300,305,299,302.5,1000000"
    q = StooqProvider._parse("ACN", csv)
    assert q is not None
    assert q.ticker == "ACN"
    assert q.price == 302.5
    assert q.as_of == date(2026, 6, 16)
    assert q.source == "Stooq"


def test_parse_no_data_returns_none():
    csv = "Symbol,Date,Time,Open,High,Low,Close,Volume\nXYZ.US,N/D,N/D,N/D,N/D,N/D,N/D,N/D"
    assert StooqProvider._parse("XYZ", csv) is None


def test_parse_header_only_returns_none():
    assert StooqProvider._parse("ACN", "Symbol,Date,Time,Close\n") is None


def test_symbol_mapping():
    assert StooqProvider._symbol("ACN") == "acn.us"
    assert StooqProvider._symbol("ctsh") == "ctsh.us"


def test_non_us_exchange_skipped():
    # Foreign venues aren't reliably on Stooq; we return None without a network call.
    with StooqProvider() as p:
        assert p.get_quote("CAP", exchange="Euronext Paris") is None
