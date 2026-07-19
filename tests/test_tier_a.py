import json
from pathlib import Path

from tracker import tier_a_feeds
from tracker.filters import classify, load_keywords
from tracker.state import State

KEYWORDS_PATH = Path(__file__).parents[1] / "config" / "keywords.yaml"


def read_fixture(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / name).read_text()


def test_parses_wikicfp_rss_fixture(fixtures_dir, tmp_path):
    state = State(tmp_path / "seen.json")
    items = tier_a_feeds.parse_feed_text("wikicfp-all", read_fixture(fixtures_dir, "wikicfp_sample.xml"), state)

    assert len(items) == 3
    titles = {item.title for item in items}
    assert "AAMAS 2027 : International Conference on Autonomous Agents and Multiagent Systems" in titles
    assert all(item.tier == "A" and item.source_id == "wikicfp-all" for item in items)


def test_parses_arxiv_atom_fixture(fixtures_dir, tmp_path):
    state = State(tmp_path / "seen.json")
    items = tier_a_feeds.parse_feed_text("arxiv-csma", read_fixture(fixtures_dir, "arxiv_sample.xml"), state)

    assert len(items) == 2
    assert items[0].title == "AgentSocietyBench: A Benchmark for Multi-Agent LLM Evaluation"
    assert items[0].url == "https://arxiv.org/abs/2607.00111"


def test_dedupe_across_runs(fixtures_dir, tmp_path):
    state_path = tmp_path / "seen.json"
    state = State(state_path)
    text = read_fixture(fixtures_dir, "wikicfp_sample.xml")

    first_run = tier_a_feeds.parse_feed_text("wikicfp-all", text, state)
    state.save()
    assert len(first_run) == 3

    # Simulate a fresh process picking up the persisted state on the next run.
    reloaded_state = State(state_path)
    second_run = tier_a_feeds.parse_feed_text("wikicfp-all", text, reloaded_state)
    assert second_run == []


def test_state_roundtrip_deleting_one_seen_id_redetects_it(fixtures_dir, tmp_path):
    state_path = tmp_path / "seen.json"
    state = State(state_path)
    text = read_fixture(fixtures_dir, "wikicfp_sample.xml")
    tier_a_feeds.parse_feed_text("wikicfp-all", text, state)
    state.save()

    data = json.loads(state_path.read_text())
    removed_id = data["sources"]["wikicfp-all"]["seen_ids"].pop()
    state_path.write_text(json.dumps(data))

    reloaded = State(state_path)
    items = tier_a_feeds.parse_feed_text("wikicfp-all", text, reloaded)
    assert len(items) == 1
    assert items[0].url == removed_id


def test_keyword_filter_matches_expected_items(fixtures_dir, tmp_path):
    state = State(tmp_path / "seen.json")
    items = tier_a_feeds.parse_feed_text("wikicfp-all", read_fixture(fixtures_dir, "wikicfp_sample.xml"), state)
    keywords = load_keywords(KEYWORDS_PATH)

    relevant_titles = []
    for item in items:
        r = classify(item.title, item.summary, keywords)
        if r.relevant:
            relevant_titles.append(item.title)

    assert any("AAMAS" in t for t in relevant_titles)
    assert any("Digital Twins" in t for t in relevant_titles)
    assert not any("Functional Programming" in t for t in relevant_titles)


def test_parses_csv_index_fixture(fixtures_dir, tmp_path):
    state = State(tmp_path / "seen.json")
    items = tier_a_feeds.parse_csv_index(
        "gh-eval-tools", read_fixture(fixtures_dir, "eval_tools_sample.csv"), state
    )
    assert len(items) == 3
    assert items[0].title == "Gorilla"
    assert items[0].url == "https://github.com/ShishirPatil/gorilla"

    # dedupe: second parse of the same CSV against the same state yields nothing new
    more = tier_a_feeds.parse_csv_index(
        "gh-eval-tools", read_fixture(fixtures_dir, "eval_tools_sample.csv"), state
    )
    assert more == []
