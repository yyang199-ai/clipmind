"""FastAPI application: REST + WebSocket + static UI."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, Set

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Config, load_config
from .embeddings import backend_status, create_backend
from .extractor import extract
from .search import SearchEngine
from .store import ClipStore
from .watcher import BackgroundIndexer, ClipboardWatcher

log = logging.getLogger("clipmind")

STATIC_DIR = Path(__file__).parent / "static"


class SearchResponse(BaseModel):
    query: str
    mode: str
    hits: list
    took_ms: float
    semantic: bool


class BroadcastHub:
    """Fan-out clipboard events to all connected UI clients."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        if not self._clients:
            return
        payload = json.dumps(message, ensure_ascii=False)
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


def create_app(cfg: Optional[Config] = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="ClipMind", version=__import__("clipmind").__version__)
    hub = BroadcastHub()

    store = ClipStore(cfg.db_path, page_size=cfg.page_size)
    backend = create_backend(cfg)
    engine = SearchEngine(store, backend, semantic_weight=cfg.semantic_weight)

    watcher = ClipboardWatcher(
        store,
        cfg,
        on_change=lambda clip, created: _dispatch_change(clip, created),
    )
    indexer = BackgroundIndexer(
        store,
        backend,
        on_indexed=lambda ids: _dispatch_event(
            {"type": "indexed", "count": len(ids)}
        ),
    )

    # ------------------------------------------------------------------
    # Event plumbing: watcher callbacks arrive on a worker thread; we hop
    # onto the event loop before broadcasting to WebSockets.
    def _dispatch_event(message: dict) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(hub.broadcast(message), loop)
        except RuntimeError:
            pass

    def _dispatch_change(clip, created: bool) -> None:
        _dispatch_event(
            {
                "type": "clip.new" if created else "clip.copied",
                "clip": clip.to_dict(),
            }
        )
        indexer.notify()
        engine.invalidate_vectors()

    # ------------------------------------------------------------------
    @app.on_event("startup")
    async def _startup() -> None:
        watcher.start()
        indexer.start()
        loop = asyncio.get_event_loop()
        log.info(
            "ClipMind ready — %d clips, semantic backend: %s",
            store.stats()["total"],
            backend.name,
        )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        watcher.stop()
        indexer.stop()

    # ------------------------------------------------------------------
    # REST API
    # ------------------------------------------------------------------
    @app.get("/api/clips")
    async def list_clips(
        limit: int = Query(default=None, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        kind: Optional[str] = None,
    ):
        clips = store.recent(limit=limit, kind=kind, offset=offset)
        return {"clips": [c.to_dict() for c in clips], "offset": offset}

    @app.get("/api/search")
    async def api_search(
        q: str,
        mode: str = Query(default="auto", pattern="^(auto|semantic|keyword)$"),
        limit: int = Query(default=30, ge=1, le=100),
    ) -> SearchResponse:
        t0 = time.perf_counter()
        hits = engine.search(q, limit=limit, mode=mode)
        took = (time.perf_counter() - t0) * 1000
        return SearchResponse(
            query=q,
            mode=mode,
            hits=[h.to_dict() for h in hits],
            took_ms=round(took, 1),
            semantic=bool(backend.available),
        )

    @app.post("/api/clips")
    async def add_clip(body: dict):
        content = (body or {}).get("content", "")
        if not content.strip():
            raise HTTPException(400, "content required")
        clip = watcher.add_manual(content)
        engine.invalidate_vectors()
        if clip is None:
            raise HTTPException(400, "empty content")
        return {"clip": clip.to_dict()}

    @app.post("/api/clips/{clip_id}/copy")
    async def copy_clip(clip_id: int):
        clip = store.get(clip_id)
        if not clip:
            raise HTTPException(404, "clip not found")
        _copy_to_clipboard(clip.content)
        store.add(clip.content, source="re-copy")  # bump usage signal
        engine.invalidate_vectors()
        return {"ok": True}

    @app.post("/api/clips/{clip_id}/pin")
    async def pin_clip(clip_id: int):
        clip = store.get(clip_id)
        if not clip:
            raise HTTPException(404, "clip not found")
        store.set_pinned(clip_id, not clip.pinned)
        return {"ok": True, "pinned": not clip.pinned}

    @app.delete("/api/clips/{clip_id}")
    async def delete_clip(clip_id: int):
        if not store.delete(clip_id):
            raise HTTPException(404, "clip not found")
        engine.invalidate_vectors()
        return {"ok": True}

    @app.delete("/api/clips")
    async def clear_clips(keep_pinned: bool = True):
        n = store.clear(keep_pinned=keep_pinned)
        engine.invalidate_vectors()
        return {"deleted": n}

    @app.get("/api/stats")
    async def stats():
        s = store.stats()
        s.update({
            "backend": backend_status(cfg, backend),
            "watcher": {
                "clipboard_available": watcher.clipboard_available,
                "last_error": watcher.last_error,
                "poll_interval": cfg.poll_interval,
            },
            "indexer": {
                "active": indexer.active,
                "indexed_total": indexer.indexed_total,
            },
            "hotkey": cfg.hotkey if cfg.enable_hotkey else None,
            "version": __import__("clipmind").__version__,
        })
        return s

    @app.post("/api/extract")
    async def api_extract(body: dict):
        """Live demo of the extractor: classify arbitrary text."""
        text = (body or {}).get("text", "")
        if not text:
            raise HTTPException(400, "text required")
        ex = extract(text)
        return {"kind": ex.kind, "label": ex.label, "icon": ex.icon,
                "entities": ex.entities, "meta": ex.meta}

    # ------------------------------------------------------------------
    # WebSocket: live feed
    # ------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await hub.connect(ws)
        try:
            while True:
                # clients may ping; anything received is ignored
                await ws.receive_text()
        except WebSocketDisconnect:
            await hub.disconnect(ws)
        except Exception:
            await hub.disconnect(ws)

    # ------------------------------------------------------------------
    # Static UI — mounted LAST so /api and /ws routes above take priority.
    # ------------------------------------------------------------------
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    app.state.cfg = cfg
    app.state.store = store
    app.state.engine = engine
    app.state.watcher = watcher
    app.state.indexer = indexer
    app.state.hub = hub
    return app


def _copy_to_clipboard(content: str) -> None:
    try:
        import pyperclip

        pyperclip.copy(content)
    except Exception as exc:
        log.warning("system clipboard copy failed: %s", exc)
