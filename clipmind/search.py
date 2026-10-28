"""Hybrid search engine.

Blends, when available:
* FTS5 keyword score (BM25, works with CJK via trigram tokenizer)
* semantic cosine similarity (when an embedding backend is configured)
* behavioural signals: pinned, recency, re-copy count

Plus a small piece of "intent magic": queries that mention a content type
(链接/验证码/邮箱/命令/颜色...) boost clips of that kind — this is what
makes "那个 github 链接" land exactly right even without embeddings.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .embeddings import EmbeddingBackend, NullBackend
from .store import Clip, ClipStore

# query word → kind boost
INTENT_KEYWORDS = {
    "url": ("链接", "网址", "网站", "link", "website", "url", "http", "github", "repo"),
    "otp": ("验证码", "校验码", "动态码", "otp", "code", "pin", "验证"),
    "email": ("邮箱", "邮件", "email", "mail", "地址"),
    "phone": ("电话", "手机", "号码", "phone", "mobile", "tel"),
    "command": ("命令", "终端", "cmd", "terminal", "shell", "bash", "docker", "git"),
    "color": ("颜色", "色值", "color", "colour", "hex"),
    "json": ("json", "json配置", "对象"),
    "path": ("路径", "文件", "目录", "path", "folder", "file"),
    "code": ("代码", "函数", "code", "function", "snippet"),
    "ip": ("ip", "ip地址", "服务器地址"),
    "uuid": ("uuid", "id", "标识"),
}

HALF_LIFE_DAYS = 10.0


@dataclass
class SearchHit:
    clip: Clip
    score: float
    matched_by: str  # "semantic" | "keyword" | "intent" | "recency" blend tag

    def to_dict(self) -> dict:
        d = self.clip.to_dict()
        d["score"] = round(self.score, 4)
        d["matched_by"] = self.matched_by
        return d


class SearchEngine:
    def __init__(
        self,
        store: ClipStore,
        backend: Optional[EmbeddingBackend] = None,
        semantic_weight: float = 0.6,
    ):
        self.store = store
        self.backend = backend or NullBackend()
        self.semantic_weight = semantic_weight
        self._matrix_ids: List[int] = []
        self._matrix = None  # numpy array (N, dim), lazily built
        self._matrix_stale = True

    # ------------------------------------------------------------------
    def invalidate_vectors(self) -> None:
        self._matrix_stale = True

    def _ensure_matrix(self):
        if not self._matrix_stale and self._matrix is not None:
            return
        import numpy as np

        ids: List[int] = []
        vecs: List[List[float]] = []
        for clip in self.store.iter_all():
            emb = _unpack(clip.embedding)
            if emb and len(emb) == self.backend.dim:
                ids.append(clip.id)
                vecs.append(emb)
        self._matrix_ids = ids
        self._matrix = np.asarray(vecs, dtype="float32") if vecs else None
        self._matrix_stale = False

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        limit: int = 30,
        mode: str = "auto",
    ) -> List[SearchHit]:
        """mode: auto | semantic | keyword."""
        q = query.strip()
        if not q:
            return [
                SearchHit(c, 0.0, "recent")
                for c in self.store.recent(limit=limit)
            ]

        want_semantic = (
            mode != "keyword"
            and self.backend.available
            and self.semantic_weight > 0
            and (mode == "semantic" or self.semantic_weight > 0)
        )

        kw_hits = self.store.search_fts(q, limit=max(limit * 4, 120))
        kw_scores = _bm25_to_unit(kw_hits)

        sem_scores: dict = {}
        if want_semantic and mode == "semantic":
            # pure semantic: rank the entire corpus by cosine
            sem_scores = self._semantic_scores_all(q, limit=max(limit * 4, 120))
        elif want_semantic:
            # hybrid: rerank keyword hits (cheap) + semantic over all (once N small)
            sem_scores = self._semantic_scores_all(q, limit=max(limit * 4, 120))

        intent_kind = _detect_intent(q)
        w = self.semantic_weight if self.backend.available else 0.0

        candidates: dict[int, Tuple[Clip, float, str]] = {}
        for clip, kw in zip(kw_hits, kw_scores):
            sem = sem_scores.get(clip.id, 0.0)
            base = (1 - w) * kw + w * sem
            candidates[clip.id] = (clip, base, "keyword" if kw >= sem else "semantic")
        for cid, (clip, sem) in sem_scores.items():
            if cid not in candidates:
                w_eff = w
                base = w_eff * sem
                candidates[cid] = (clip, base, "semantic")

        # Intent-only recall: "那个链接" / "颜色" style queries that mention
        # a content type but match no text — surface recent clips of that kind.
        if intent_kind and len(candidates) < limit:
            for clip in self.store.recent(limit=limit, kind=intent_kind):
                if clip.id in candidates:
                    continue
                base = 0.12 if candidates else 0.2
                if clip.id not in candidates:
                    candidates[clip.id] = (clip, base, "intent")

        # behavioural ranking signals multiply the base score
        now = time.time()
        ranked: List[SearchHit] = []
        for cid, (clip, base, by) in candidates.items():
            if base <= 0.001 and not intent_kind and by != "semantic":
                continue
            age_days = max(0.0, (now - clip.created_at) / 86400.0)
            recency = math.exp(-age_days / HALF_LIFE_DAYS)          # 0..1
            usage = 1.0 + 0.15 * math.log1p(clip.copy_count)        # ≥1
            pinned = 1.5 if clip.pinned else 1.0
            kind_boost = 1.0
            if intent_kind and clip.kind == intent_kind:
                kind_boost = 1.8
                by = "intent+kind"
            score = (base + 0.05) * usage * pinned * (0.6 + 0.4 * recency) * kind_boost
            ranked.append(SearchHit(clip, score, by))

        ranked.sort(key=lambda h: h.score, reverse=True)
        return ranked[:limit]

    def _semantic_scores_all(self, query: str, limit: int) -> dict:
        if not self.backend.available:
            return {}
        self._ensure_matrix()
        if self._matrix is None or not self._matrix_ids:
            return {}
        import numpy as np

        qv = np.asarray(self.backend.embed([query])[0], dtype="float32")
        if qv.size != self._matrix.shape[1]:
            return {}
        qn = np.linalg.norm(qv) or 1.0
        mat = self._matrix
        sims = (mat @ qv) / (np.linalg.norm(mat, axis=1) * qn + 1e-9)
        # take top-k by cosine
        order = np.argsort(-sims)[: limit * 3]
        out: dict[int, Tuple[Clip, float]] = {}
        ids = self._matrix_ids
        for idx in order:
            cid = ids[int(idx)]
            sim = float(sims[int(idx)])
            if sim < 0.15:  # noise floor for small models
                continue
            clip = self.store.get(cid)
            if clip:
                out[cid] = (clip, max(0.0, sim))
        return out


# ---------------------------------------------------------------------------
def _unpack(blob: Optional[bytes]) -> Optional[List[float]]:
    import struct

    if not blob:
        return None
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _bm25_to_unit(clips: Sequence[Clip]) -> List[float]:
    """SQLite FTS 'rank' is negative BM25 (smaller = better). Map to 0..1
    relative to the best hit so scores stay comparable across queries."""
    ranks = [float(getattr(c, "fts_rank", None) or 0.0) for c in clips]
    if not ranks:
        return [0.4] * len(clips)
    best = min(ranks)  # most negative
    worst = max(ranks)
    if best == worst:
        return [1.0] * len(clips)
    span = best - worst
    return [min(1.0, 0.3 + 0.7 * ((r - worst) / span)) for r in ranks]


def _detect_intent(query: str) -> Optional[str]:
    """Match intent words: CJK by substring, latin by word boundary so
    'otp' doesn't fire inside 'notpresent'."""
    import re as _re

    q = query.lower()
    for kind, words in INTENT_KEYWORDS.items():
        for w in words:
            if not w.isascii():
                if w in q:
                    return kind
            elif _re.search(rf"\b{_re.escape(w)}\b", q):
                return kind
    return None
