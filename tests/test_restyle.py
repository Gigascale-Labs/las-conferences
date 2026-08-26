import hashlib
import json
from unittest.mock import Mock, patch

import pytest

from tracker import db, restyle
from tracker.discover import Candidate
from tracker.verify import VERIFIED, VerificationResult


def _result(name: str, description: str) -> VerificationResult:
    candidate = Candidate(
        query="q", name=name, url=f"https://example.org/{name.lower()}", event_type="workshop",
        dates="2027-01-01", location="Online", organizer="Test Org", description=description,
        relevance_rationale="r", reputability_rationale="rep",
    )
    return VerificationResult(candidate, VERIFIED, "ok")


def _seed(tmp_path, descriptions: dict[str, str]):
    """Build a db with one row per name -> description, return (path, {name: id})."""
    path = tmp_path / "data" / "discoveries.db"
    conn = db.connect(path)
    db.insert_events(conn, [_result(name, text) for name, text in descriptions.items()])
    ids = {event["name"]: event["id"] for event in db.fetch_all_events(conn)}
    conn.close()
    return path, ids


def _descriptions(path) -> dict[str, str]:
    conn = db.connect(path)
    stored = {event["name"]: event["description"] for event in db.fetch_all_events(conn)}
    conn.close()
    return stored


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _echo_client(mapping: dict[str, str]):
    """Stub model client: returns `mapping` restricted to the ids it was asked
    about, so a test only has to say what it wants changed."""

    def call(rows):
        return {row["id"]: mapping[row["id"]] for row in rows if row["id"] in mapping}

    return call


def test_batch_rewrites_the_right_rows(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a", "B": "old b", "C": "old c"})
    client = _echo_client({ids["A"]: "new a", ids["C"]: "new c"})

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client, batch_size=3)

    assert _descriptions(path) == {"A": "new a", "B": "old b", "C": "new c"}
    assert {change.id for change in report.changed} == {ids["A"], ids["C"]}
    assert report.unchanged == []
    assert report.omitted == [ids["B"]]
    assert report.failures == []


def test_identical_rewrite_is_reported_unchanged_not_as_a_change(tmp_path):
    path, ids = _seed(tmp_path, {"A": "already in style"})
    client = _echo_client({ids["A"]: "already in style"})

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client)

    assert report.unchanged == [ids["A"]]
    assert report.changed == []
    assert report.wrote is False


def test_invented_id_is_dropped(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a"})

    def client(rows):
        return {rows[0]["id"]: "new a", "id-the-model-made-up": "text for a row that does not exist"}

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client)

    assert report.invented == ["id-the-model-made-up"]
    assert [change.id for change in report.changed] == [ids["A"]]
    assert _descriptions(path) == {"A": "new a"}


def test_omitted_id_is_reported_and_its_row_left_unchanged(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a", "B": "old b"})
    client = _echo_client({ids["A"]: "new a"})  # B asked for, never returned

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client, batch_size=2)

    assert report.omitted == [ids["B"]]
    assert _descriptions(path)["B"] == "old b"


def test_failed_call_leaves_the_original_description_intact(tmp_path):
    path, _ = _seed(tmp_path, {"A": "old a"})

    def client(rows):
        raise restyle.RestyleError("503 upstream unavailable")

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client)

    assert _descriptions(path) == {"A": "old a"}
    assert report.changed == []
    assert len(report.failures) == 1
    assert "503 upstream unavailable" in report.failures[0]
    assert report.batches_failed == report.batches_attempted == 1


def test_one_failed_batch_does_not_stop_the_others(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a", "B": "old b"})

    def client(rows):
        if rows[0]["id"] == ids["A"]:
            raise restyle.RestyleError("timeout")
        return {rows[0]["id"]: "new b"}

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client, batch_size=1)

    assert _descriptions(path) == {"A": "old a", "B": "new b"}
    assert report.batches_attempted == 2
    assert report.batches_failed == 1


def test_empty_rewrite_never_blanks_a_description(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a"})
    client = _echo_client({ids["A"]: "   "})

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client)

    assert _descriptions(path) == {"A": "old a"}
    assert report.changed == []
    assert "empty description" in report.failures[0]


def test_dry_run_writes_nothing_and_leaves_the_db_byte_identical(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a", "B": "old b"})
    before = _sha256(path)
    feed_path = tmp_path / "docs" / "events.json"
    client = _echo_client({ids["A"]: "new a", ids["B"]: "new b"})

    report = restyle.restyle(path, feed_path, client=client, dry_run=True)

    assert _sha256(path) == before
    assert not feed_path.exists()
    assert not (path.parent / "backups").exists()
    assert report.wrote is False
    assert len(report.changed) == 2  # it still reports what it would have done


def test_write_backs_up_the_db_first_and_regenerates_the_feed(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a"})
    before = _sha256(path)
    feed_path = tmp_path / "docs" / "events.json"

    report = restyle.restyle(path, feed_path, client=_echo_client({ids["A"]: "new a"}))

    assert report.backup_path is not None and report.backup_path.exists()
    assert _sha256(report.backup_path) == before  # the pre-write state, not the new one
    payload = json.loads(feed_path.read_text())
    assert payload["events"][0]["description"] == "new a"
    assert payload["count"] == 1


def test_limit_only_touches_the_first_n_rows(tmp_path):
    path, ids = _seed(tmp_path, {"A": "old a", "B": "old b", "C": "old c"})
    seen_ids = []

    def client(rows):
        seen_ids.extend(row["id"] for row in rows)
        return {row["id"]: "new " + row["description"] for row in rows}

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client, limit=2)

    assert len(seen_ids) == 2
    assert report.total_rows == 3
    assert report.considered == 2
    assert sum(1 for text in _descriptions(path).values() if text.startswith("new ")) == 2


def test_batches_are_capped_at_batch_size(tmp_path):
    path, _ = _seed(tmp_path, {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"})
    batch_sizes = []

    def client(rows):
        batch_sizes.append(len(rows))
        return {}

    report = restyle.restyle(path, tmp_path / "docs" / "events.json", client=client, batch_size=2)

    assert batch_sizes == [2, 2, 1]
    assert report.batches_attempted == 3


def test_missing_api_key_exits_with_a_message_naming_the_variable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(restyle.API_KEY_ENV, raising=False)
    monkeypatch.setattr(restyle, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(restyle, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr("sys.argv", ["restyle", "--dry-run"])

    with pytest.raises(SystemExit) as exc_info:
        restyle.main()

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert restyle.API_KEY_ENV in stderr
    assert "Traceback" not in stderr


def test_client_prompt_carries_the_no_new_facts_constraint_and_no_web_search():
    """The model must be told to rewrite, not re-derive — and must not be
    handed a search tool that would let it look the event up (see the module
    docstring on why the scraped page is deliberately withheld)."""
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"rewrites": [{"id": "x", "description": "d"}]})}}]
    }
    client = restyle.openrouter_client(api_key="test-key", model="test/model")
    with patch("tracker.restyle.requests.post", return_value=resp) as mock_post:
        assert client([{"id": "x", "name": "X", "description": "old"}]) == {"x": "d"}

    body = mock_post.call_args.kwargs["json"]
    assert "plugins" not in body
    prompt = body["messages"][0]["content"]
    assert "NO NEW FACTS" in prompt
    assert "old" in prompt  # the existing text is what it rewrites
    assert "https://example.org" not in prompt  # not the scraped page, not the url


def test_client_http_failure_raises_restyle_error_not_a_bare_exception():
    client = restyle.openrouter_client(api_key="test-key", model="test/model")
    with patch("tracker.restyle.requests.post", side_effect=ConnectionError("boom")):
        with pytest.raises(restyle.RestyleError):
            client([{"id": "x", "name": "X", "description": "old"}])
