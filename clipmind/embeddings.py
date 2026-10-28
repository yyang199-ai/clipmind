"""Embedding backends.

Three interchangeable providers behind one tiny interface:

* ``local``  — fastembed + an ONNX model, fully offline (optional extra:
               ``pip install clipmind[semantic]``).
* ``api``    — any OpenAI-compatible /embeddings endpoint.
* ``none``   — disabled; search gracefully degrades to keyword-only.

Selection is by config (``embedding_backend``) or "auto": local if
available, else api if a key is configured, else none.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional, Protocol, Sequence

from .config import Config


class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


class NullBackend:
    """No-op backend: semantic search disabled, keyword search still works."""

    name = "none"
    dim = 0

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [[] for _ in texts]

    @property
    def available(self) -> bool:
        return False


class LocalFastembedBackend:
    """On-device embeddings via fastembed (ONNX runtime, CPU-friendly)."""

    name = "local"

    def __init__(self, model: str):
        from fastembed import TextEmbedding  # lazy: optional dependency

        self._model_name = model
        self._embedder = TextEmbedding(model_name=model)
        self.dim = len(next(iter(self._embedder.embed(["probe"]))))
        self._cache_limit = 6  # keep memory flat for bulk backfill

    @property
    def available(self) -> bool:
        return True

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        out: List[List[float]] = []
        batch = 32
        for i in range(0, len(texts), batch):
            chunk = [t[:8000] for t in texts[i : i + batch]]
            out.extend([list(map(float, v)) for v in self._embedder.embed(chunk)])
        return out


class OpenAICompatBackend:
    """OpenAI-compatible /v1/embeddings endpoint (self-hosted friendly)."""

    name = "api"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
    ):
        import httpx  # lazy: optional dependency

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self.dim = len(self.embed(["probe"])[0])

    @property
    def available(self) -> bool:
        return True

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        batch = 64
        for i in range(0, len(texts), batch):
            chunk = [t[:8000] for t in texts[i : i + batch]]
            resp = self._client.post(
                "/embeddings",
                json={"model": self.model, "input": chunk},
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            out.extend([d["embedding"] for d in data])
            if len(texts) > batch:
                time.sleep(0.05)  # be gentle with rate limits
        return out


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_backend(cfg: Config) -> EmbeddingBackend:
    choice = (cfg.embedding_backend or "auto").lower()

    if choice == "none":
        return NullBackend()

    if choice in ("auto", "local"):
        try:
            return LocalFastembedBackend(cfg.local_embedding_model)
        except ImportError:
            if choice == "local":
                return NullBackend()
        except Exception:
            # Model download failed / offline — fall through to api/none.
            if choice == "local":
                return NullBackend()

    if choice in ("auto", "api") and cfg.resolved_api_key:
        try:
            return OpenAICompatBackend(
                base_url=cfg.api_embedding_base_url,
                api_key=cfg.resolved_api_key,
                model=cfg.api_embedding_model,
            )
        except ImportError:
            if choice == "api":
                return NullBackend()
        except Exception:
            return NullBackend()

    return NullBackend()


def backend_status(cfg: Config, backend: EmbeddingBackend) -> dict:
    return {
        "selected": backend.name,
        "dim": getattr(backend, "dim", 0),
        "semantic_search": getattr(backend, "available", False),
        "hint": _hint(cfg, backend),
    }


def _hint(cfg: Config, backend: EmbeddingBackend) -> str:
    if backend.name == "local":
        return f"local model: {cfg.local_embedding_model}"
    if backend.name == "api":
        return f"api model: {cfg.api_embedding_model}"
    return (
        "semantic search off — keyword mode only. "
        "Enable with: pip install 'clipmind[semantic]' (local, offline)"
    )
