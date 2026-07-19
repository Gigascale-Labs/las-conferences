from pathlib import Path

from tracker.filters import classify, load_keywords

KEYWORDS_PATH = Path(__file__).parents[1] / "config" / "keywords.yaml"


def load():
    return load_keywords(KEYWORDS_PATH)


def test_multi_agent_group_matches():
    r = classify("New results on multi-agent coordination", "", load())
    assert "multi_agent" in r.matched_topics
    assert r.relevant


def test_agent_societies_substring_match():
    # "agent societ" must catch both "society" and "societies"
    r = classify("Modeling artificial agent societies", "", load())
    assert "multi_agent" in r.matched_topics


def test_behaviour_substring_match():
    r = classify("", "A study of collective behaviour in online communities", load())
    assert "css" in r.matched_topics


def test_behavior_us_spelling_also_matches():
    r = classify("", "A study of collective behavior in online communities", load())
    assert "css" in r.matched_topics


def test_digital_twin_group():
    r = classify("Digital Twin approaches for smart cities", "", load())
    assert "digital_twin" in r.matched_topics


def test_comp_econ_group():
    r = classify("Algorithmic collusion in market simulation", "", load())
    assert "comp_econ" in r.matched_topics


def test_llm_evals_group():
    r = classify("A new agent benchmark for agent safety", "", load())
    assert "llm_evals" in r.matched_topics


def test_meta_alone_is_not_relevant():
    r = classify("Call for papers", "deadline extended", load())
    assert not r.relevant
    assert r.priority is None


def test_topic_plus_meta_is_high_priority():
    r = classify("Workshop on multi-agent reinforcement learning", "Call for papers, deadline soon", load())
    assert r.relevant
    assert r.priority == "high"


def test_topic_without_meta_is_normal_priority():
    r = classify("New paper on multi-agent reinforcement learning results", "", load())
    assert r.relevant
    assert r.priority == "normal"


def test_irrelevant_item_no_match():
    r = classify("A survey of quantum error correction codes", "", load())
    assert not r.relevant
    assert r.matched_topics == []


def test_case_insensitive():
    r = classify("MULTI-AGENT systems workshop", "", load())
    assert "multi_agent" in r.matched_topics
