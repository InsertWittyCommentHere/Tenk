"""API tests using FastAPI's TestClient (no live server, no network).

Dashboard endpoints are exercised against the live ACN DB (skipped if absent).
The chat endpoint is checked for graceful 503 when no API key is configured.
"""

import os

import pytest
from fastapi.testclient import TestClient

from equity_research.api.app import create_app

ACN_CIK = 1467373


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.fixture(scope="module")
def have_acn():
    from equity_research.config import settings
    from equity_research.storage import Database

    if not settings.db_path.exists():
        return False
    with Database(settings.db_path) as db:
        return db.count_facts(ACN_CIK) > 0


def test_root_serves_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    # Markers from the expanded multi-view dashboard (home, charts, reverse-DCF).
    for token in ("Equity Research", "Watchlist", "implied FCF growth", "Alert feed", "barLine"):
        assert token in r.text


def test_companies_includes_phase6_set(client):
    r = client.get("/api/companies")
    tickers = {c["ticker"] for c in r.json()["companies"]}
    # Phase 6 onboarded CTSH and IBM alongside ACN — proof the set is config-driven.
    assert {"ACN", "CTSH", "IBM"} <= tickers


def test_overview_has_market_key(client, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    body = client.get("/api/companies/ACN/overview").json()
    # "market" is present (a dict when the price feed succeeds, None when it can't).
    assert "market" in body


def test_watchlist_endpoint(client, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    r = client.get("/api/watchlist")
    assert r.status_code == 200
    cos = r.json()["companies"]
    acn = next((c for c in cos if c["ticker"] == "ACN"), None)
    assert acn is not None
    # Card carries the home-grid fields.
    for k in ("revenue", "dcf_base", "verdict", "unread_alerts"):
        assert k in acn


def test_trends_endpoint(client, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    r = client.get("/api/companies/ACN/trends")
    assert r.status_code == 200
    body = r.json()
    assert body["years"] and len(body["revenue"]) == len(body["years"])
    assert len(body["operating_margin"]) == len(body["years"])


def test_companies_lists_acn(client):
    r = client.get("/api/companies")
    assert r.status_code == 200
    tickers = {c["ticker"] for c in r.json()["companies"]}
    assert "ACN" in tickers


def test_overview_shape(client, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    r = client.get("/api/companies/ACN/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "ACN"
    assert body["latest_fiscal_year"] >= 2024
    # Headline revenue present and cited.
    assert "revenue" in body["headline"]
    assert body["headline"]["revenue"]["sources"]
    # Valuation range present.
    assert body["valuation"]["per_share"]["base"] is not None
    assert isinstance(body["alerts"], list)


def test_statements_shape(client, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    r = client.get("/api/companies/ACN/statements")
    assert r.status_code == 200
    stmts = r.json()["statements"]
    assert "income_statement" in stmts
    rev = stmts["income_statement"]["revenue"]
    assert rev and all("accession" in row for row in rev)


def test_filings_endpoint(client, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    r = client.get("/api/companies/ACN/filings?form=10-K&limit=3")
    assert r.status_code == 200
    filings = r.json()["filings"]
    assert filings and all(f["form"] == "10-K" for f in filings)
    assert filings[0]["document_url"]


def test_refresh_feed_ack_flow(client, have_acn, monkeypatch, tmp_path):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    from equity_research import config as cfg

    # Isolate the alert DB for this test.
    monkeypatch.setattr(cfg.settings, "alerts_db_path", tmp_path / "alerts.db")

    # Refresh records the new alerts.
    r = client.post("/api/refresh?ticker=ACN")
    assert r.status_code == 200
    assert r.json()["total_new"] >= 1

    # Feed shows them, all unread.
    f = client.get("/api/feed").json()
    assert f["unread"] >= 1
    assert f["alerts"]
    fp = f["alerts"][0]["fingerprint"]

    # Acknowledge one; unread count drops.
    a = client.post(f"/api/alerts/{fp}/ack")
    assert a.status_code == 200
    f2 = client.get("/api/feed").json()
    assert f2["unread"] == f["unread"] - 1

    # Acking again 404s (already acknowledged).
    assert client.post(f"/api/alerts/{fp}/ack").status_code == 404


def test_unknown_ticker_404(client):
    r = client.get("/api/companies/ZZZZ/overview")
    assert r.status_code == 404


def test_chat_requires_api_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/companies/ACN/chat", json={"question": "hi"})
    # 503 when no key (or 404 if ACN not ingested — both are graceful, not 500).
    assert r.status_code in (503, 404)


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY for live chat"
)
def test_chat_live(client, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    r = client.post("/api/companies/ACN/chat", json={"question": "What was FY2025 revenue?"})
    assert r.status_code == 200
    assert "answer" in r.json()
