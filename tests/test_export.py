"""Static export: offline, against a small synthetic DB (no EDGAR, no price feed).

Checks the contract the static dashboard relies on: every URL it fetches exists as
`data/<path>.json`, the page is flagged static, and saved research is carried over.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from equity_research import config as cfg
from equity_research.api import service
from equity_research.export import COMPANY_ENDPOINTS, export_site, save_research
from equity_research.models import Fact
from equity_research.models.provenance import Provenance
from equity_research.storage import Database

ACN_CIK = 1467373

# concept -> (unit, is_instant, values for FY2022..FY2024)
SYNTHETIC = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": ("USD", False, [61.6e9, 64.1e9, 64.9e9]),
    "OperatingIncomeLoss": ("USD", False, [9.4e9, 8.8e9, 9.6e9]),
    "NetIncomeLoss": ("USD", False, [6.9e9, 6.9e9, 7.3e9]),
    "NetCashProvidedByUsedInOperatingActivities": ("USD", False, [9.5e9, 9.5e9, 9.1e9]),
    "PaymentsToAcquirePropertyPlantAndEquipment": ("USD", False, [0.7e9, 0.5e9, 0.5e9]),
    "EarningsPerShareDiluted": ("USD/shares", False, [10.71, 10.77, 11.44]),
    "CashAndCashEquivalentsAtCarryingValue": ("USD", True, [7.9e9, 9.0e9, 5.0e9]),
    "LongTermDebtNoncurrent": ("USD", True, [0.05e9, 0.05e9, 5.0e9]),
    "CommonStockSharesOutstanding": ("shares", True, [632e6, 628e6, 626e6]),
}


def _facts() -> list[Fact]:
    out = []
    for i, year in enumerate((2022, 2023, 2024)):
        end, start = date(year, 8, 31), date(year - 1, 9, 1)
        accn = f"0001467373-{year % 100 + 1:02d}-000001"
        filed = date(year, 10, 15)
        for concept, (unit, instant, vals) in SYNTHETIC.items():
            out.append(
                Fact(
                    cik=ACN_CIK,
                    taxonomy="us-gaap",
                    concept=concept,
                    unit=unit,
                    value=vals[i],
                    period_start=None if instant else start,
                    period_end=end,
                    is_instant=instant,
                    fiscal_period="FY",
                    form="10-K",
                    accession=accn,
                    filed_date=filed,
                    provenance=Provenance(source="test", accession=accn, filed_date=filed),
                )
            )
    return out


@pytest.fixture
def synthetic_env(tmp_path, monkeypatch):
    db_path = tmp_path / "eq.db"
    with Database(db_path) as db:
        db.upsert_facts(_facts())
    monkeypatch.setattr(cfg.settings, "db_path", db_path)
    monkeypatch.setattr(cfg.settings, "alerts_db_path", tmp_path / "alerts.db")
    monkeypatch.setattr(cfg.settings, "chunks_db_path", tmp_path / "chunks.db")
    monkeypatch.setattr(service, "_quote", lambda company: None)  # no network
    service._QUOTE_CACHE.clear()
    return tmp_path


def test_export_writes_every_file_the_dashboard_fetches(synthetic_env):
    showcase = synthetic_env / "showcase"
    save_research(
        "ACN",
        {
            "question": "Is growth durable?",
            "answer": "Yes [1].",
            "citation_coverage": 1.0,
            "critique": None,
        },
        showcase_dir=showcase,
    )

    out = synthetic_env / "site"
    s = export_site(out, with_comps=False, showcase_dir=showcase)

    assert s.companies == ["ACN"]  # CTSH/IBM have no data in the synthetic DB
    html = (out / "index.html").read_text()
    assert "window.TENK_STATIC = true" in html and "barLine" in html

    for rel in ("companies", "watchlist", "feed", "meta"):
        assert (out / "data" / f"{rel}.json").exists(), rel
    for ep in COMPANY_ENDPOINTS + ["research"]:
        assert (out / "data" / "companies" / "ACN" / f"{ep}.json").exists(), ep

    overview = json.loads((out / "data/companies/ACN/overview.json").read_text())
    assert overview["latest_fiscal_year"] == 2024
    assert overview["headline"]["revenue"]["value"] == 64.9e9
    assert overview["valuation"]["per_share"]["base"] > 0

    watch = json.loads((out / "data/watchlist.json").read_text())
    assert [c["ticker"] for c in watch["companies"]] == ["ACN"]

    research = json.loads((out / "data/companies/ACN/research.json").read_text())
    assert research["items"][0]["question"] == "Is growth durable?"
    assert json.loads((out / "vercel.json").read_text())["framework"] is None


def test_save_research_replaces_same_question(tmp_path):
    for answer in ("first", "second"):
        save_research("ACN", {"question": "Q?", "answer": answer}, showcase_dir=tmp_path)
    doc = json.loads((tmp_path / "acn.json").read_text())
    assert [i["answer"] for i in doc["items"]] == ["second"]


def test_export_is_idempotent(synthetic_env):
    out = synthetic_env / "site"
    (out / "stale.txt").parent.mkdir(parents=True)
    (out / "stale.txt").write_text("old")
    export_site(out, with_comps=False, showcase_dir=synthetic_env / "none")
    assert not (out / "stale.txt").exists()
