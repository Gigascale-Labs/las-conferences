from tracker.state import SeenStore, normalize_url


def test_normalize_url_strips_trailing_slash_and_case():
    assert normalize_url("HTTPS://Example.org/Event/") == normalize_url("https://example.org/Event")


def test_normalize_url_ignores_query_and_fragment():
    assert normalize_url("https://example.org/event?ref=twitter#top") == normalize_url("https://example.org/event")


def test_unseen_url_is_not_seen(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    assert store.is_seen("https://example.org/event") is False


def test_marked_url_is_seen(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen("https://example.org/event")
    assert store.is_seen("https://example.org/event/") is True


def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.mark_seen("https://example.org/event")
    store.save()

    reloaded = SeenStore(path)
    assert reloaded.is_seen("https://example.org/event") is True
