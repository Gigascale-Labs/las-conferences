import difflib
from pathlib import Path
from unittest.mock import patch

from tracker.net import FetchResult, RobotsCache
from tracker.state import SnapshotStore
from tracker.tier_c_pagediff import check_page, normalise

ADDED_LINE = "Call for papers: Special Track on Agent-Based Computational Economics, deadline November 2026"


def read_fixture(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / name).read_text()


def test_normalisation_strips_script_style_nav_footer(fixtures_dir):
    text = normalise(read_fixture(fixtures_dir, "page_v1.html"))
    assert "trackingId" not in text
    assert "Home | About | Events | Contact" not in text
    assert "Copyright ESSA" not in text


def test_normalisation_is_stable_same_page_twice(fixtures_dir):
    html = read_fixture(fixtures_dir, "page_v1.html")
    assert normalise(html) == normalise(html)


def test_normalisation_ignores_unrelated_script_changes(fixtures_dir):
    # v1 and v2 differ in their <script> tracking id as well as the real content
    # change; normalise() must strip scripts entirely so only real content differs.
    v1 = normalise(read_fixture(fixtures_dir, "page_v1.html"))
    v2 = normalise(read_fixture(fixtures_dir, "page_v2.html"))
    assert v1 != v2


def test_added_cfp_line_is_the_only_diff_output(fixtures_dir):
    v1_text = normalise(read_fixture(fixtures_dir, "page_v1.html"))
    v2_text = normalise(read_fixture(fixtures_dir, "page_v2.html"))

    diff = difflib.unified_diff(v1_text.splitlines(), v2_text.splitlines(), lineterm="")
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]

    assert added == [ADDED_LINE]


def test_first_run_seeds_silently_and_returns_none(fixtures_dir, tmp_path):
    snapshots = SnapshotStore(tmp_path / "snapshots")
    robots = RobotsCache("test-agent")
    html = read_fixture(fixtures_dir, "page_v1.html")

    with patch("tracker.tier_c_pagediff.fetch", return_value=FetchResult(200, html, None, None, False)):
        event = check_page(
            "essa", "https://example.com/events", user_agent="test-agent", robots=robots, snapshots=snapshots
        )

    assert event is None
    assert snapshots.load("essa") is not None


def test_second_run_detects_added_line(fixtures_dir, tmp_path):
    snapshots = SnapshotStore(tmp_path / "snapshots")
    robots = RobotsCache("test-agent")
    html_v1 = read_fixture(fixtures_dir, "page_v1.html")
    html_v2 = read_fixture(fixtures_dir, "page_v2.html")

    with patch("tracker.tier_c_pagediff.fetch", return_value=FetchResult(200, html_v1, None, None, False)):
        check_page("essa", "https://example.com/events", user_agent="test-agent", robots=robots, snapshots=snapshots)

    with patch("tracker.tier_c_pagediff.fetch", return_value=FetchResult(200, html_v2, None, None, False)):
        event = check_page(
            "essa", "https://example.com/events", user_agent="test-agent", robots=robots, snapshots=snapshots
        )

    assert event is not None
    assert event.added_lines == [ADDED_LINE]


def test_no_change_second_run_returns_none(fixtures_dir, tmp_path):
    snapshots = SnapshotStore(tmp_path / "snapshots")
    robots = RobotsCache("test-agent")
    html = read_fixture(fixtures_dir, "page_v1.html")

    with patch("tracker.tier_c_pagediff.fetch", return_value=FetchResult(200, html, None, None, False)):
        check_page("essa", "https://example.com/events", user_agent="test-agent", robots=robots, snapshots=snapshots)
        event = check_page(
            "essa", "https://example.com/events", user_agent="test-agent", robots=robots, snapshots=snapshots
        )

    assert event is None
