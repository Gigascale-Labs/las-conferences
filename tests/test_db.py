from tracker import db
from tracker.discover import Candidate
from tracker.verify import BLOCKED, VERIFIED, VerificationResult


def _result(name="Test Workshop", url="https://example.org/event", status=VERIFIED) -> VerificationResult:
    candidate = Candidate(
        query="q", name=name, url=url, event_type="workshop", dates="2027-01-01", location="Online",
        organizer="Test Org", description="d", relevance_rationale="r", reputability_rationale="rep",
    )
    return VerificationResult(candidate, status, "ok")


def test_connect_creates_file_and_schema(tmp_path):
    path = tmp_path / "discoveries.db"
    assert not path.exists()
    conn = db.connect(path)
    conn.close()
    assert path.exists()


def test_insert_and_fetch_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [_result()])
    events = db.fetch_all_events(conn)
    conn.close()

    assert len(events) == 1
    assert events[0]["name"] == "Test Workshop"
    assert events[0]["verification_status"] == VERIFIED
    assert events[0]["date_scraped"]


def test_each_row_gets_a_distinct_uuid(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [_result(name="A", url="https://example.org/a"), _result(name="B", url="https://example.org/b")])
    events = db.fetch_all_events(conn)
    conn.close()

    ids = {e["id"] for e in events}
    assert len(ids) == 2
    for event_id in ids:
        assert len(event_id) == 36  # standard uuid4 string length


def test_insert_events_is_additive_across_calls(tmp_path):
    path = tmp_path / "discoveries.db"
    conn = db.connect(path)
    db.insert_events(conn, [_result(name="First", url="https://example.org/first")])
    conn.close()

    conn = db.connect(path)
    db.insert_events(conn, [_result(name="Second", url="https://example.org/second", status=BLOCKED)])
    events = db.fetch_all_events(conn)
    conn.close()

    assert {e["name"] for e in events} == {"First", "Second"}


def test_empty_kept_list_is_a_no_op(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [])
    events = db.fetch_all_events(conn)
    conn.close()

    assert events == []
