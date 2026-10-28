"""Clipboard watching + background embedding indexing.

Two threads:
* ``ClipboardWatcher`` — polls the system clipboard, applies the privacy
  guard, dedupes, persists new clips, and fires ``on_change`` callbacks
  (the web UI listens over WebSocket for a live feed).
* ``BackgroundIndexer`` — drains clips without embeddings and computes
  vectors in batches so search stays fresh without blocking anything.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable, List, Optional

from .config import Config
from .embeddings import EmbeddingBackend, NullBackend
from .store import Clip, ClipStore

log = logging.getLogger("clipmind")

# Callbacks receive (clip, created: bool)
ChangeCallback = Callable[[Clip, bool], None]

# Content we refuse to remember (config-extendable).
_DEFAULT_BLOCK = [re.compile(p, re.IGNORECASE) for p in (
    r"otpauth://",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]


class ClipboardWatcher:
    """Poll the system clipboard and persist changes."""

    def __init__(
        self,
        store: ClipStore,
        cfg: Config,
        on_change: Optional[ChangeCallback] = None,
    ):
        self.store = store
        self.cfg = cfg
        self.on_change = on_change
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_hash: Optional[str] = None
        self._blocklist = list(_DEFAULT_BLOCK)
        for pat in cfg.blocklist_patterns:
            try:
                self._blocklist.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                log.warning("invalid blocklist pattern skipped: %r", pat)
        self.last_error: Optional[str] = None
        self.clipboard_available = _probe_clipboard()

    # ------------------------------------------------------------------
    def start(self) -> bool:
        if not self.clipboard_available:
            self.last_error = (
                "no system clipboard access (headless session?) — "
                "manual add & import still work"
            )
            log.warning(self.last_error)
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="clipmind-watcher", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    def _run(self) -> None:
        import pyperclip

        while not self._stop.is_set():
            try:
                content = pyperclip.paste()
            except Exception as exc:  # pyperclip raises PyperclipException
                self.last_error = str(exc)
                self._stop.wait(5.0)
                continue
            if content and content.strip():
                self._handle(content)
            self._stop.wait(self.cfg.poll_interval)

    def _handle(self, content: str) -> None:
        if len(content.encode("utf-8", "ignore")) > self.cfg.max_content_bytes:
            return
        if self._is_blocked(content):
            return

        from .extractor import extract, looks_like_secret

        if self.cfg.skip_secret_like and looks_like_secret(content):
            log.info("skipped secret-looking clip (%d chars)", len(content))
            return

        chash = Clip.content_hash_of(content)
        if chash == self._last_hash:
            return  # unchanged since last poll

        clip, created = self.store.add(content, source="clipboard")
        if clip is None:
            return
        self._last_hash = chash
        if self.on_change:
            try:
                self.on_change(clip, created)
            except Exception:
                log.exception("on_change callback failed")

    def _is_blocked(self, content: str) -> bool:
        head = content[:2000]
        return any(rx.search(head) for rx in self._blocklist)

    # ------------------------------------------------------------------
    def add_manual(self, content: str) -> Optional[Clip]:
        """Add content programmatically (API / CLI `clipmind add`)."""
        if not content.strip():
            return None
        clip, created = self.store.add(content, source="manual")
        if clip and self.on_change:
            self.on_change(clip, created)
        return clip


class BackgroundIndexer:
    """Keep embeddings fresh in the background (batch, throttled)."""

    def __init__(
        self,
        store: ClipStore,
        backend: EmbeddingBackend,
        poll: float = 2.0,
        on_indexed: Optional[Callable[[List[int]], None]] = None,
    ):
        self.store = store
        self.backend = backend
        self.poll = poll
        self.on_indexed = on_indexed
        self._stop = threading.Event()
        self._wake = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self.indexed_total = 0

    @property
    def active(self) -> bool:
        return bool(self.backend and self.backend.available)

    def start(self) -> bool:
        if not self.active:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="clipmind-indexer", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._wake.put(None)
        if self._thread:
            self._thread.join(timeout=3.0)

    def notify(self) -> None:
        """Kick the indexer (called when new clips land)."""
        if self.active:
            self._wake.put(None)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._wake.get(timeout=self.poll)
            except queue.Empty:
                pass
            if self._stop.is_set():
                return
            try:
                self._index_batch()
            except Exception:
                log.exception("embedding batch failed")
                self._stop.wait(30.0)  # back off, e.g. network backend down

    def _index_batch(self, batch: int = 64) -> List[int]:
        pending = self.store.get_missing_embeddings(limit=batch)
        if not pending:
            return []
        texts = [c.content[:8000] for c in pending]
        try:
            vectors = self.backend.embed(texts)
        except Exception as exc:
            log.warning("embed failed: %s", exc)
            return []
        ids: List[int] = []
        for clip, vec in zip(pending, vectors):
            if vec:
                self.store.set_embedding(clip.id, vec)
                ids.append(clip.id)
        self.indexed_total += len(ids)
        if ids and self.on_indexed:
            try:
                self.on_indexed(ids)
            except Exception:
                pass
        return ids


def _probe_clipboard() -> bool:
    try:
        import pyperclip

        pyperclip.paste()
        return True
    except Exception:
        return False
