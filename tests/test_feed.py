import json

from tracker import db, feed
from tracker.discover import Candidate
from tracker.verify import VERIFIED, VerificationResult


def _result(name="Test Workshop", url="https://example.org/event") -> VerificationResult:
    candidate = Candidate(
        query="q", name=name, url=url, event_type="workshop", dates="2027-01-01", location="Online",
        organizer="Test Org", description="d", relevance_rationale="r", reputability_rationale="rep",
    )
    return VerificationResult(candidate, VERIFIED, "ok")


def test_writes_valid_json_with_expected_shape(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [_result()])

    feed_path = tmp_path / "docs" / "events.json"
    feed.write_json_feed(conn, feed_path)
    conn.close()

    payload = json.loads(feed_path.read_text())
    assert payload["count"] == 1
    assert payload["generated_at"]
    assert payload["events"][0]["name"] == "Test Workshop"


def test_reflects_full_cumulative_table_not_just_latest_insert(tmp_path):
    path = tmp_path / "discoveries.db"
    conn = db.connect(path)
    db.insert_events(conn, [_result(name="First", url="https://example.org/first")])
    conn.close()

    conn = db.connect(path)
    db.insert_events(conn, [_result(name="Second", url="https://example.org/second")])
    feed_path = tmp_path / "docs" / "events.json"
    feed.write_json_feed(conn, feed_path)
    conn.close()

    payload = json.loads(feed_path.read_text())
    assert payload["count"] == 2
    assert {e["name"] for e in payload["events"]} == {"First", "Second"}


def test_empty_db_writes_empty_events_list(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    feed_path = tmp_path / "docs" / "events.json"
    feed.write_json_feed(conn, feed_path)
    conn.close()

    payload = json.loads(feed_path.read_text())
    assert payload["count"] == 0
    assert payload["events"] == []
