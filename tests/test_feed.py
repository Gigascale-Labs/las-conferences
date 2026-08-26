import html
import json
import xml.etree.ElementTree as ET

from tracker import db, feed
from tracker.discover import Candidate
from tracker.verify import VERIFIED, VerificationResult

ATOM = "{http://www.w3.org/2005/Atom}"


def _result(
    name="Test Workshop", url="https://example.org/event", description="d"
) -> VerificationResult:
    candidate = Candidate(
        query="q", name=name, url=url, event_type="workshop", dates="2027-01-01", location="Online",
        organizer="Test Org", description=description, relevance_rationale="r",
        reputability_rationale="rep",
    )
    return VerificationResult(candidate, VERIFIED, "ok")


def _atom(tmp_path, results, filename="events.xml"):
    """Write one Atom feed from a fresh db seeded with `results`, return
    (parsed root, raw text)."""
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, results)
    path = tmp_path / "docs" / filename
    feed.write_atom_feed(conn, path)
    conn.close()
    text = path.read_text(encoding="utf-8")
    return ET.fromstring(text), text


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


# --------------------------------------------------------------------------
# Atom feed (docs/events.xml)
# --------------------------------------------------------------------------


def test_atom_feed_parses_and_carries_the_feed_level_metadata(tmp_path):
    root, _ = _atom(tmp_path, [_result()])

    assert root.tag == f"{ATOM}feed"
    assert root.findtext(f"{ATOM}title")
    assert root.findtext(f"{ATOM}id") == "https://largeagentsystems.org/events"
    links = {link.get("rel"): link.get("href") for link in root.findall(f"{ATOM}link")}
    assert links["self"] == "https://largeagentsystems.org/events/feed.xml"
    assert links["alternate"] == "https://largeagentsystems.org/events"


def test_atom_entry_count_matches_the_row_count(tmp_path):
    results = [_result(name=f"Event {i}", url=f"https://example.org/{i}") for i in range(5)]
    root, _ = _atom(tmp_path, results)

    assert len(root.findall(f"{ATOM}entry")) == 5


def test_atom_entry_carries_title_stable_id_link_and_content(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [_result(description="Covers agent-population dynamics.")])
    row = db.fetch_all_events(conn)[0]
    path = tmp_path / "docs" / "events.xml"
    feed.write_atom_feed(conn, path)
    conn.close()

    entry = ET.fromstring(path.read_text(encoding="utf-8")).find(f"{ATOM}entry")
    assert entry.findtext(f"{ATOM}title") == "Test Workshop"
    # Built from the row's uuid, not from the event url — see feed.py.
    assert entry.findtext(f"{ATOM}id") == f"tag:largeagentsystems.org,2026:event/{row['id']}"
    assert entry.find(f"{ATOM}link").get("href") == "https://example.org/event"
    assert entry.findtext(f"{ATOM}updated") == f"{row['date_scraped']}T00:00:00Z"

    content = entry.find(f"{ATOM}content")
    assert content.get("type") == "html"
    assert "Covers agent-population dynamics." in content.text
    assert "Test Org" in content.text  # organizer
    assert "Online" in content.text  # location
    assert "2027-01-01" in content.text  # dates
    assert "workshop" in content.text  # type


def test_atom_entries_are_newest_date_scraped_first(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [_result(name="Older", url="https://example.org/old")])
    db.insert_events(conn, [_result(name="Newer", url="https://example.org/new")])
    conn.execute("UPDATE events SET date_scraped = '2026-01-01' WHERE name = 'Older'")
    conn.execute("UPDATE events SET date_scraped = '2026-08-26' WHERE name = 'Newer'")
    conn.commit()
    path = tmp_path / "docs" / "events.xml"
    feed.write_atom_feed(conn, path)
    conn.close()

    root = ET.fromstring(path.read_text(encoding="utf-8"))
    titles = [entry.findtext(f"{ATOM}title") for entry in root.findall(f"{ATOM}entry")]
    assert titles == ["Newer", "Older"]
    assert root.findtext(f"{ATOM}updated") == "2026-08-26T00:00:00Z"


def test_hostile_event_text_cannot_forge_an_entry_or_break_parsing(tmp_path):
    """Event text is third-party text a model copied. `&`, `<` and a literal
    `</content></entry>` in it must escape, not close an element early."""
    hostile_name = '</content></entry><entry><title>forged</title></entry><x a="b" & \'q\''
    hostile_description = "R&D </content></entry><entry><title>forged too</title></entry>"
    root, text = _atom(
        tmp_path, [_result(name=hostile_name, description=hostile_description)]
    )

    entries = root.findall(f"{ATOM}entry")
    assert len(entries) == 1  # no forged sibling
    assert entries[0].findtext(f"{ATOM}title") == hostile_name  # round-trips exactly
    assert "<entry><title>forged" not in text  # never present unescaped in the raw bytes

    # The double layer: XML parsing peels off one level and leaves the HTML
    # fragment, in which the description is still HTML-escaped. Unescaping
    # that second level returns the original text.
    content = entries[0].find(f"{ATOM}content").text
    assert "R&amp;D &lt;/content&gt;" in content
    assert hostile_description in html.unescape(content)


def test_hostile_url_cannot_break_out_of_the_link_attribute(tmp_path):
    hostile_url = 'https://example.org/"><entry><title>forged</title></entry><x y="'
    root, _ = _atom(tmp_path, [_result(url=hostile_url)])

    entries = root.findall(f"{ATOM}entry")
    assert len(entries) == 1
    assert entries[0].find(f"{ATOM}link").get("href") == hostile_url


def test_atom_output_is_byte_identical_across_two_calls_so_no_clock_is_read(tmp_path):
    """A rebuild must not re-announce every event: <updated> comes from
    date_scraped, never from a clock."""
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [_result()])
    first = tmp_path / "docs" / "first.xml"
    second = tmp_path / "docs" / "second.xml"
    feed.write_atom_feed(conn, first)
    feed.write_atom_feed(conn, second)
    conn.close()

    assert first.read_bytes() == second.read_bytes()


def test_empty_url_entry_stays_in_the_feed_without_an_alternate_link(tmp_path):
    """Decision (feed.py): the row is a known event, so it keeps a full entry
    — minus the link, since an empty href is not a usable IRI."""
    root, _ = _atom(tmp_path, [_result(url="")])

    entry = root.find(f"{ATOM}entry")
    assert entry is not None
    assert entry.findtext(f"{ATOM}title") == "Test Workshop"
    assert entry.findtext(f"{ATOM}id").startswith("tag:largeagentsystems.org,2026:event/")
    assert entry.find(f"{ATOM}link") is None


def test_non_http_url_is_stated_in_the_content_but_never_linked(tmp_path):
    root, _ = _atom(tmp_path, [_result(url="javascript:alert(1)")])

    entry = root.find(f"{ATOM}entry")
    assert entry.find(f"{ATOM}link") is None
    assert "javascript:alert(1)" in entry.find(f"{ATOM}content").text


def test_unverified_row_says_so_in_its_content(tmp_path):
    """SPEC.md section 5: a blocked row must never read as confirmed."""
    result = _result()
    result.status = "blocked"
    result.reason = "could not fetch page: 403"
    root, _ = _atom(tmp_path, [result])

    content = root.find(f"{ATOM}entry").find(f"{ATOM}content").text
    assert "blocked" in content
    assert "403" in content


def test_empty_db_writes_a_valid_feed_with_no_entries(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    path = tmp_path / "docs" / "events.xml"
    feed.write_atom_feed(conn, path)
    conn.close()

    root = ET.fromstring(path.read_text(encoding="utf-8"))
    assert root.findall(f"{ATOM}entry") == []
    assert root.findtext(f"{ATOM}updated") == feed.EMPTY_FEED_UPDATED


def test_unparseable_date_scraped_falls_back_instead_of_emitting_a_clock(tmp_path):
    conn = db.connect(tmp_path / "discoveries.db")
    db.insert_events(conn, [_result()])
    conn.execute("UPDATE events SET date_scraped = 'not-a-date'")
    conn.commit()
    path = tmp_path / "docs" / "events.xml"
    feed.write_atom_feed(conn, path)
    conn.close()

    entry = ET.fromstring(path.read_text(encoding="utf-8")).find(f"{ATOM}entry")
    assert entry.findtext(f"{ATOM}updated") == feed.EMPTY_FEED_UPDATED
