"""SQLite persistence for clipboard history.

Design notes
------------
* Single file, WAL mode → safe concurrent reads while the watcher writes.
* FTS5 virtual table with the ``trigram`` tokenizer gives substring search
  that actually works for CJK text; falls back to ``unicode61`` when the
  runtime SQLite lacks trigram support.
* Re-copying the same content bumps ``copy_count`` and refreshes
  ``created_at`` — usage is a first-class ranking signal.
* Embeddings live as BLOBs next to the text so the whole history is
  portable in one file (no extra vector DB).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .extractor import Extraction, extract

_SCHEMA_VERSION = 1

_CREATE_CLIPS = """
CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'text',
    entities    TEXT NOT NULL DEFAULT '[]',
    meta        TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    copy_count  INTEGER NOT NULL DEFAULT 1,
    pinned      INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'clipboard',
    embedding   BLOB
)
"""

_CREATE_FTS_TRIGRAM = """
CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
    content,
    content='clips',
    content_rowid='id',
    tokenize='trigram'
)
"""

_CREATE_FTS_UNICODE = """
CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
    content,
    content='clips',
    content_rowid='id',
    tokenize='unicode61'
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_clips_created ON clips(created_at DESC)
"""


def _db_supports_trigram(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.__probe_t USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE temp.__probe_t")
        return True
    except sqlite3.OperationalError:
        return False


@dataclass
class Clip:
    id: Optional[int]
    content: str
    kind: str
    entities: List[str]
    meta: dict
    content_hash: str
    created_at: float
    updated_at: float
    copy_count: int
    pinned: bool
    source: str
    embedding: Optional[bytes] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "content": self.content,
            "kind": self.kind,
            "entities": self.entities,
            "meta": self.meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "copy_count": self.copy_count,
            "pinned": self.pinned,
            "source": self.source,
            "has_embedding": self.embedding is not None,
        }
        return d

    @staticmethod
    def content_hash_of(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()


class ClipStore:
    """Thread-safe clipboard history store (one SQLite file)."""

    def __init__(self, db_path: Path | str, page_size: int = 80):
        self.db_path = Path(db_path)
        self.page_size = page_size
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._init_schema()

    # -- connection ---------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute(_CREATE_CLIPS)
            use_trigram = _db_supports_trigram(conn)
            conn.execute(_CREATE_FTS_TRIGRAM if use_trigram else _CREATE_FTS_UNICODE)
            conn.execute(_CREATE_INDEX)
            # Legacy DBs created before FTS existed: backfill index.
            fts_count = conn.execute("SELECT count(*) FROM clips_fts").fetchone()[0]
            clip_count = conn.execute("SELECT count(*) FROM clips").fetchone()[0]
            if fts_count < clip_count:
                conn.execute(
                    "INSERT INTO clips_fts(clips_fts) VALUES('rebuild')"
                )
            conn.commit()
            self.fts_tokenizer = "trigram" if use_trigram else "unicode61"

    # -- writes -------------------------------------------------------------
    def add(
        self,
        content: str,
        source: str = "clipboard",
        extraction: Optional[Extraction] = None,
        embedding: Optional[Sequence[float]] = None,
    ) -> Tuple[Optional[Clip], bool]:
        """Insert a clip. Returns (clip, created?). Re-copies bump usage.

        ``created`` is False when the content already existed (the existing
        row was touched instead: copy_count +1, updated_at refreshed).
        """
        content = content.strip("\r\n")
        if not content.strip():
            return None, False
        ex = extraction or extract(content)
        chash = Clip.content_hash_of(content)
        now = time.time()
        emb_blob = _pack_embedding(embedding) if embedding is not None else None

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO clips
                        (content, kind, entities, meta, content_hash,
                         created_at, updated_at, copy_count, pinned,
                         source, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                    """,
                    (
                        content,
                        ex.kind,
                        json.dumps(ex.entities, ensure_ascii=False),
                        json.dumps(ex.meta, ensure_ascii=False),
                        chash,
                        now,
                        now,
                        source,
                        emb_blob,
                    ),
                )
                new_id = cur.lastrowid
                _fts_sync_insert(conn, new_id, content)
                conn.commit()
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM clips WHERE content_hash = ?", (chash,)
                ).fetchone()
                if row is None:
                    return None, False
                conn.execute(
                    """
                    UPDATE clips
                       SET copy_count = copy_count + 1,
                           updated_at = ?,
                           source = ?,
                           embedding = COALESCE(?, embedding)
                     WHERE id = ?
                    """,
                    (now, source, emb_blob, row["id"]),
                )
                conn.commit()
                return self.get(row["id"]), False
        return self.get(new_id), True

    def set_embedding(self, clip_id: int, vector: Sequence[float]) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE clips SET embedding = ? WHERE id = ?",
                (_pack_embedding(vector), clip_id),
            )
            conn.commit()

    def set_pinned(self, clip_id: int, pinned: bool) -> None:
        with self._lock:
            self._conn().execute(
                "UPDATE clips SET pinned = ? WHERE id = ?",
                (1 if pinned else 0, clip_id),
            )
            self._conn().commit()

    def delete(self, clip_id: int) -> bool:
        with self._lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT content FROM clips WHERE id = ?", (clip_id,)
            ).fetchone()
            if row is None:
                return False
            # External-content FTS5: the 'delete' command must echo the
            # original content, otherwise the index corrupts.
            _fts_sync_delete(conn, clip_id, row["content"])
            conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            conn.commit()
            return True

    def clear(self, keep_pinned: bool = True) -> int:
        with self._lock:
            conn = self._conn()
            if keep_pinned:
                cur = conn.execute("DELETE FROM clips WHERE pinned = 0")
            else:
                cur = conn.execute("DELETE FROM clips")
            conn.execute("INSERT INTO clips_fts(clips_fts) VALUES('rebuild')")
            conn.commit()
            return cur.rowcount

    # -- reads --------------------------------------------------------------
    def get(self, clip_id: int) -> Optional[Clip]:
        row = self._conn().execute(
            "SELECT * FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        return _row_to_clip(row) if row else None

    def recent(
        self,
        limit: Optional[int] = None,
        kind: Optional[str] = None,
        offset: int = 0,
    ) -> List[Clip]:
        limit = limit or self.page_size
        sql = "SELECT * FROM clips"
        args: list = []
        if kind:
            sql += " WHERE kind = ?"
            args.append(kind)
        sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [_row_to_clip(r) for r in self._conn().execute(sql, args)]

    def get_missing_embeddings(self, limit: int = 512) -> List[Clip]:
        rows = self._conn().execute(
            "SELECT * FROM clips WHERE embedding IS NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_clip(r) for r in rows]

    def stats(self) -> dict:
        conn = self._conn()
        total = conn.execute("SELECT count(*) FROM clips").fetchone()[0]
        pinned = conn.execute("SELECT count(*) FROM clips WHERE pinned=1").fetchone()[0]
        embedded = conn.execute(
            "SELECT count(*) FROM clips WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        kinds = {
            row["kind"]: row["n"]
            for row in conn.execute(
                "SELECT kind, count(*) AS n FROM clips GROUP BY kind ORDER BY n DESC"
            )
        }
        oldest = conn.execute("SELECT MIN(created_at) FROM clips").fetchone()[0]
        return {
            "total": total,
            "pinned": pinned,
            "embedded": embedded,
            "kinds": kinds,
            "fts_tokenizer": self.fts_tokenizer,
            "oldest": oldest,
        }

    # -- keyword search (FTS) -----------------------------------------------
    def search_fts(self, query: str, limit: int = 50) -> List[Clip]:
        """FTS keyword search with BM25 ranking. Best-effort: short queries
        on trigram DBs degrade to a LIKE scan."""
        q = query.strip()
        if not q:
            return []
        conn = self._conn()
        clips: List[Clip] = []
        try:
            if self.fts_tokenizer == "trigram" and len(q) < 3:
                like = f"%{q}%"
                rows = conn.execute(
                    "SELECT c.* FROM clips c WHERE c.content LIKE ? "
                    "ORDER BY c.updated_at DESC LIMIT ?",
                    (like, limit),
                )
                clips = [_row_to_clip(r) for r in rows]
            else:
                fts_query = _fts_sanitize(q, self.fts_tokenizer)
                rows = conn.execute(
                    """
                    SELECT c.*, f.rank AS fts_rank
                      FROM clips_fts f
                      JOIN clips c ON c.id = f.rowid
                     WHERE clips_fts MATCH ?
                     ORDER BY rank
                     LIMIT ?
                    """,
                    (fts_query, limit),
                )
                for r in rows:
                    c = _row_to_clip(r)
                    c.fts_rank = r["fts_rank"]  # negative BM25, smaller=better
                    clips.append(c)
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT * FROM clips WHERE content LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"%{q}%", limit),
            )
            clips = [_row_to_clip(r) for r in rows]
        return clips

    def iter_all(self, chunk: int = 500) -> Iterable[Clip]:
        """Yield every clip (for batch embedding backfill)."""
        last_id = 0
        while True:
            rows = self._conn().execute(
                "SELECT * FROM clips WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, chunk),
            ).fetchall()
            if not rows:
                return
            for r in rows:
                last_id = r["id"]
                yield _row_to_clip(r)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pack_embedding(vec: Optional[Sequence[float]]) -> Optional[bytes]:
    import struct

    if vec is None:
        return None
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_embedding(blob: Optional[bytes]) -> Optional[List[float]]:
    import struct

    if not blob:
        return None
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _row_to_clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        content=row["content"],
        kind=row["kind"],
        entities=json.loads(row["entities"] or "[]"),
        meta=json.loads(row["meta"] or "{}"),
        content_hash=row["content_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        copy_count=row["copy_count"],
        pinned=bool(row["pinned"]),
        source=row["source"],
        embedding=row["embedding"],
    )


def _fts_sync_insert(conn: sqlite3.Connection, rowid: int, content: str) -> None:
    conn.execute("INSERT INTO clips_fts(rowid, content) VALUES (?, ?)", (rowid, content))


def _fts_sync_delete(conn: sqlite3.Connection, rowid: int, content: str) -> None:
    conn.execute(
        "INSERT INTO clips_fts(clips_fts, rowid, content) VALUES('delete', ?, ?)",
        (rowid, content),
    )


_FTS_TOKEN_RE = re.compile(r'["\'*()\s:|&^]')


def _fts_sanitize(query: str, tokenizer: str) -> str:
    """Turn a raw user query into a safe FTS5 MATCH expression.

    We AND the individual tokens (quoted) so multi-word queries narrow
    instead of OR-ing everything.
    """
    tokens = [t for t in _FTS_TOKEN_RE.split(query) if t.strip()]
    if not tokens:
        return f'"{query.strip()}"'
    return " ".join(f'"{t}"' for t in tokens)
