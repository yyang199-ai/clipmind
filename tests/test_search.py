"""Search engine tests: hybrid ranking, intent boost, graceful degradation."""

from clipmind.embeddings import NullBackend
from clipmind.search import SearchEngine
from clipmind.store import ClipStore


def _seed(store: ClipStore):
    store.add("【星河银行】您的验证码是 483920，10分钟内有效。")
    store.add("https://github.com/anthropics/anthropic-sdk-python")
    store.add("sales@example.com")
    store.add("#3B82F6")
    store.add("git push --force-with-lease origin main")
    store.add("meeting notes: docker review at 3pm")
    return store


def test_keyword_search_finds_exact(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hits = engine.search("github")
    assert hits and "github.com" in hits[0].clip.content


def test_intent_boost_otp(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hits = engine.search("验证码")
    assert hits[0].clip.kind == "otp"
    assert hits[0].clip.meta.get("otp") == "483920"


def test_intent_boost_link(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hits = engine.search("github 链接")
    assert "github.com" in hits[0].clip.content


def test_intent_boost_color(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hits = engine.search("颜色")
    assert hits[0].clip.kind == "color"


def test_intent_boost_command(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hits = engine.search("git 命令")
    assert hits[0].clip.kind == "command"


def test_empty_query_returns_recent(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hits = engine.search("")
    assert len(hits) >= 5


def test_no_hits_is_clean(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    assert engine.search("zzzznotpresent") == []


def test_keyword_mode_only(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hits = engine.search("docker", mode="keyword")
    assert hits and "docker" in hits[0].clip.content


def test_hit_dict_shape(store: ClipStore):
    _seed(store)
    engine = SearchEngine(store, NullBackend())
    hit = engine.search("验证码")[0]
    d = hit.to_dict()
    assert {"id", "content", "kind", "score", "matched_by"} <= set(d)
