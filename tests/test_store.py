"""Store tests: persistence, dedup, FTS search."""

import time

from clipmind.store import ClipStore


def test_add_and_get(store: ClipStore):
    clip, created = store.add("hello world")
    assert created and clip.id is not None
    got = store.get(clip.id)
    assert got.content == "hello world"


def test_dedup_bumps_usage(store: ClipStore):
    c1, created1 = store.add("same text")
    time.sleep(0.01)
    c2, created2 = store.add("same text")
    assert created1 and not created2
    assert c2.id == c1.id
    assert c2.copy_count == 2


def test_empty_rejected(store: ClipStore):
    clip, created = store.add("   ")
    assert clip is None and created is False


def test_recent_ordering(store: ClipStore):
    for text in ["a", "b", "c"]:
        store.add(text)
        time.sleep(0.005)
    recent = store.recent(limit=10)
    assert [c.content for c in recent] == ["c", "b", "a"]


def test_pinned_first(store: ClipStore):
    c_old, _ = store.add("older")
    c_new, _ = store.add("newer")
    time.sleep(0.005)
    store.set_pinned(c_old.id, True)
    recent = store.recent(limit=10)
    assert recent[0].content == "older"
    assert recent[1].content == "newer"


def test_fts_search(store: ClipStore):
    store.add("the docker container is running")
    store.add("git commit history")
    hits = store.search_fts("docker")
    assert len(hits) == 1
    assert "docker" in hits[0].content


def test_fts_search_chinese(store: ClipStore):
    store.add("今天天气很好，适合出去散步")
    store.add("股票今天大涨了")
    hits = store.search_fts("天气")
    assert len(hits) == 1
    assert "散步" in hits[0].content


def test_fts_and_semantics_of_multiword(store: ClipStore):
    store.add("docker container running")
    store.add("docker compose up")
    hits = store.search_fts("docker compose")
    assert hits and "compose" in hits[0].content


def test_delete(store: ClipStore):
    clip, _ = store.add("to be deleted")
    assert store.delete(clip.id)
    assert store.get(clip.id) is None


def test_clear_keeps_pinned(store: ClipStore):
    c1, _ = store.add("keep me")
    store.add("remove me")
    store.set_pinned(c1.id, True)
    n = store.clear(keep_pinned=True)
    assert n == 1
    remaining = store.recent(limit=10)
    assert [c.content for c in remaining] == ["keep me"]


def test_embedding_roundtrip(store: ClipStore):
    clip, _ = store.add("vector holder")
    vec = [0.1, 0.2, 0.3, -0.4]
    store.set_embedding(clip.id, vec)
    got = store.get(clip.id)
    assert got.embedding is not None
    import struct

    unpacked = list(struct.unpack("<4f", got.embedding))
    assert unpacked == pytest_approx(vec)


def pytest_approx(vec):
    class Cmp:
        def __eq__(self, other):
            return all(abs(a - b) < 1e-6 for a, b in zip(vec, other))
    return Cmp()


def test_stats(store: ClipStore):
    store.add("https://example.com")
    store.add("hello there friend")
    s = store.stats()
    assert s["total"] == 2
    assert s["kinds"]["url"] == 1


def test_iter_all(store: ClipStore):
    for i in range(5):
        store.add(f"item {i}")
    assert len(list(store.iter_all())) == 5


def test_bad_config_patterns_ignored(store: ClipStore):
    # pathological inputs must not crash
    store.add('{"broken": json')
    store.add("")
    store.add("\n\n\n")
    store.add("🎉 unicode emoji only 🎉")
