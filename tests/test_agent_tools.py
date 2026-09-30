"""Offline tests for the agent tool surface (no API calls).

The tools are the trust-critical part of the agent layer: they must return real,
cited numbers. We exercise the dispatcher directly against the live ACN DB
(skipped if not ingested) plus a couple of pure-logic checks.
"""

import pytest

from equity_research.agents.tools import ResearchContext, ResearchTools, TOOL_SCHEMAS

ACN_CIK = 1467373


@pytest.fixture(scope="module")
def tools():
    from equity_research.config import load_company, settings
    from equity_research.storage import Database

    if not settings.db_path.exists():
        pytest.skip("Live DB not present — run `equity ingest ACN`.")
    db = Database(settings.db_path)
    if db.count_facts(ACN_CIK) == 0:
        pytest.skip("ACN not ingested.")
    ctx = ResearchContext(company=load_company("ACN"), db=db, chunks=None)
    return ResearchTools(ctx)


def test_tool_schemas_wellformed():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {"get_financials", "get_ratios", "run_dcf", "search_filings"} <= names
    for t in TOOL_SCHEMAS:
        assert "description" in t and "input_schema" in t
        assert t["input_schema"]["type"] == "object"


def test_get_financials_returns_cited_numbers(tools):
    out = tools.dispatch("get_financials", {"metrics": ["revenue", "net_income"]})
    assert "revenue FY2025" in out
    assert "69" in out  # ~69.7B FY2025
    assert "0001467373" in out  # an accession citation is present


def test_get_financials_handles_derived_metric(tools):
    out = tools.dispatch("get_financials", {"metrics": ["free_cash_flow"]})
    assert "free_cash_flow FY2025" in out
    assert "operating_cash_flow - capex" in out  # formula cited


def test_run_dcf_returns_range(tools):
    out = tools.dispatch(
        "run_dcf",
        {"discount_rate": 0.09, "base_growth": 0.08, "bear_growth": 0.03, "bull_growth": 0.12},
    )
    assert "bear:" in out and "base:" in out and "bull:" in out
    assert "/sh" in out  # per-share value present


def test_get_ratios_runs(tools):
    out = tools.dispatch("get_ratios", {"last_n": 3})
    assert "net_margin=" in out and "return_on_equity=" in out


def test_unknown_tool_is_graceful(tools):
    assert "unknown tool" in tools.dispatch("nope", {}).lower()


def test_search_without_index_is_graceful(tools):
    # chunks=None in this fixture -> should not raise.
    out = tools.dispatch("search_filings", {"query": "risk"})
    assert "unavailable" in out.lower()
