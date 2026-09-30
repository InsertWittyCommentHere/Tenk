"""End-to-end change-detection test for the watchlist refresh (uses live ACN DB)."""

import pytest

from equity_research.watchlist import refresh_alerts

ACN_CIK = 1467373


@pytest.fixture
def have_acn():
    from equity_research.config import settings
    from equity_research.storage import Database

    if not settings.db_path.exists():
        return False
    with Database(settings.db_path) as db:
        return db.count_facts(ACN_CIK) > 0


def test_refresh_is_idempotent(tmp_path, have_acn):
    if not have_acn:
        pytest.skip("ACN not ingested.")
    from equity_research.config import settings

    alerts_db = tmp_path / "alerts.db"
    first = refresh_alerts(
        db_path=settings.db_path, alerts_db_path=alerts_db, tickers=["ACN"]
    )
    assert "ACN" in first.checked
    # ACN has real alerts (the FY25 leverage jump / insider cluster), so first run finds some.
    assert first.new_by_ticker["ACN"] >= 1

    # Second run on unchanged data records nothing new — the change-detection guarantee.
    second = refresh_alerts(
        db_path=settings.db_path, alerts_db_path=alerts_db, tickers=["ACN"]
    )
    assert second.new_by_ticker["ACN"] == 0
    assert second.total_new == 0


def test_refresh_skips_unknown_ticker(tmp_path):
    summary = refresh_alerts(
        db_path=tmp_path / "empty.db", alerts_db_path=tmp_path / "a.db", tickers=["ZZZZ"]
    )
    assert "ZZZZ" in summary.skipped
