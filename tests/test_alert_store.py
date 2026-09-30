"""Tests for alert persistence + change detection."""

from datetime import date

from equity_research.alerts import Alert
from equity_research.storage import AlertStore
from equity_research.storage.alert_store import fingerprint


def _alert(kind="margin_compression", sev="warn", msg="margin fell to 14%"):
    return Alert(kind=kind, severity=sev, message=msg, as_of=date(2025, 8, 31), sources=["a"])


def test_fingerprint_is_stable_for_same_state():
    a, b = _alert(), _alert()
    assert fingerprint(1, a) == fingerprint(1, b)


def test_fingerprint_differs_on_message_change():
    assert fingerprint(1, _alert(msg="x")) != fingerprint(1, _alert(msg="y"))


def test_record_returns_only_new(tmp_path):
    store = AlertStore(tmp_path / "a.db")
    alerts = [_alert(msg="m1"), _alert(kind="leverage_increase", msg="m2")]
    new = store.record(1, "ACN", alerts)
    assert len(new) == 2
    # Re-recording the same state yields nothing new (the core change-detection guarantee).
    again = store.record(1, "ACN", alerts)
    assert again == []
    # A genuinely new alert is detected.
    third = store.record(1, "ACN", alerts + [_alert(kind="new_filing", msg="New 8-K filed")])
    assert len(third) == 1
    assert third[0].kind == "new_filing"


def test_feed_and_unread_count(tmp_path):
    store = AlertStore(tmp_path / "a.db")
    store.record(1, "ACN", [_alert(msg="m1"), _alert(kind="new_filing", sev="high", msg="m2")])
    feed = store.feed()
    assert len(feed) == 2
    # Sorted by severity: high first.
    assert feed[0].severity == "high"
    assert store.unread_count() == 2
    assert store.unread_count("ACN") == 2


def test_acknowledge_removes_from_unread(tmp_path):
    store = AlertStore(tmp_path / "a.db")
    new = store.record(1, "ACN", [_alert(msg="m1")])
    fp = fingerprint(1, new[0])
    assert store.acknowledge(fp) is True
    assert store.unread_count() == 0
    # Acknowledging again is a no-op (returns False).
    assert store.acknowledge(fp) is False
    # Still present in the full feed, just marked acknowledged.
    assert store.feed()[0].acknowledged is True
    assert store.feed(only_unread=True) == []
