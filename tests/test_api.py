"""End-to-end API tests with FastAPI's TestClient."""

import pytest

from clipmind.config import Config
from clipmind.embeddings import NullBackend
from clipmind.server import create_app


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    cfg = Config(
        db_path=tmp_path / "api.db",
        open_browser=False,
        enable_hotkey=False,
    )
    app = create_app(cfg)
    # deterministic: force keyword-only backend for tests
    app.state.engine.backend = NullBackend()
    with TestClient(app) as client:
        yield client


def test_add_and_list(client):
    r = client.post("/api/clips", json={"content": "hello clipmind"})
    assert r.status_code == 200
    clip_id = r.json()["clip"]["id"]

    r = client.get("/api/clips")
    clips = r.json()["clips"]
    assert any(c["id"] == clip_id for c in clips)


def test_search_endpoint(client):
    client.post("/api/clips", json={"content": "您的验证码是 998877"})
    client.post("/api/clips", json={"content": "https://example.com/page"})
    r = client.get("/api/search", params={"q": "验证码"})
    assert r.status_code == 200
    body = r.json()
    assert body["hits"]
    assert body["hits"][0]["kind"] == "otp"


def test_search_took_ms_present(client):
    client.post("/api/clips", json={"content": "abc"})
    r = client.get("/api/search", params={"q": "abc"})
    assert "took_ms" in r.json()


def test_extract_endpoint(client):
    r = client.post("/api/extract", json={"text": "您的验证码是 123456"})
    assert r.status_code == 200
    assert r.json()["kind"] == "otp"
    assert r.json()["meta"]["otp"] == "123456"


def test_extract_rejects_empty(client):
    r = client.post("/api/extract", json={"text": ""})
    assert r.status_code == 400


def test_pin_and_delete(client):
    r = client.post("/api/clips", json={"content": "pin me"})
    cid = r.json()["clip"]["id"]

    r = client.post(f"/api/clips/{cid}/pin")
    assert r.json()["pinned"] is True

    r = client.delete(f"/api/clips/{cid}")
    assert r.status_code == 200

    r = client.get(f"/api/clips/{cid}")
    assert r.status_code in (404, 405)  # not found; GET single not defined


def test_stats_endpoint(client):
    client.post("/api/clips", json={"content": "stats probe"})
    r = client.get("/api/stats")
    body = r.json()
    assert body["total"] >= 1
    assert "backend" in body
    assert "hotkey" in body


def test_missing_clip_404(client):
    assert client.delete("/api/clips/99999").status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "ClipMind" in r.text


def test_static_assets(client):
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_websocket_feed(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/api/clips", json={"content": "ws probe content"})
        msg = ws.receive_json()
        assert msg["type"] == "clip.new"
        assert "ws probe content" in msg["clip"]["content"]


def test_clear_endpoint(client):
    client.post("/api/clips", json={"content": "to be cleared"})
    r = client.delete("/api/clips")
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1
