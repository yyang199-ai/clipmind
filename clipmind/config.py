"""Configuration for ClipMind.

Resolution order (later wins):
1. Built-in defaults below.
2. TOML config file at ``~/.clipmind/config.toml`` (if readable).
3. Environment variables prefixed with ``CLIPMIND_``.

The config file is entirely optional — ClipMind runs with zero setup.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Python 3.9/3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

APP_DIR = Path.home() / ".clipmind"
DB_PATH_DEFAULT = APP_DIR / "clipmind.db"
CONFIG_PATH = APP_DIR / "config.toml"

# Hotkey that focuses the web UI (when the serve command is running).
HOTKEY_DEFAULT = "ctrl+shift+v"

# How often (seconds) the clipboard is polled.
POLL_INTERVAL_DEFAULT = 0.4

# Clips larger than this many bytes are ignored (protects against copying
# huge files by accident).
MAX_CONTENT_BYTES = 100_000

SERVER_HOST_DEFAULT = "127.0.0.1"
SERVER_PORT_DEFAULT = 8377

# Number of recent clips the /api and UI fetch by default.
PAGE_SIZE_DEFAULT = 80

# Semantics -----------------------------------------------------------------

# Embedding backend: "auto" | "local" | "api" | "none"
#   auto → use local model if fastembed is installed, else API if configured,
#          else fall back to keyword search only.
EMBEDDING_BACKEND_DEFAULT = "auto"

# fastembed model identifier (see https://qdrant.github.io/fastembed/).
# BAAI/bge-small-zh-v1.5 is a tiny multilingual-friendly model that also
# performs reasonably on English. Swap freely — any fastembed-supported
# dense model works.
LOCAL_EMBEDDING_MODEL_DEFAULT = "BAAI/bge-small-zh-v1.5"

# OpenAI-compatible endpoint for embeddings.
API_EMBEDDING_BASE_URL_DEFAULT = "https://api.openai.com/v1"
API_EMBEDDING_MODEL_DEFAULT = "text-embedding-3-small"

# Weight of semantic similarity vs. keyword (BM25) score when both are
# available. 0 → pure keyword, 1 → pure vector.
SEMANTIC_WEIGHT_DEFAULT = 0.6

# Privacy -------------------------------------------------------------------

# Skip capturing content that looks like a password / secret token.
SKIP_SECRET_LIKE_DEFAULT = True

# Never store clips whose content matches any of these regexes (case
# insensitive). Extend via config file to cover your password manager.
BLOCKLIST_PATTERNS_DEFAULT: list = [
    r"otpauth://",            # TOTP seeds
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"^ghp_[A-Za-z0-9]{36,}$",     # GitHub PAT
    r"^sk-[A-Za-z0-9]{20,}$",      # OpenAI-style API key
    r"^xox[bp]-",                  # Slack tokens
]

# Content matching these patterns is *skipped* even when short (heuristics
# for password-manager clipboard markers).
SECRETS_EXTRA_HINTS = [
    "otpauth",
    "private key",
]


@dataclass
class Config:
    """Runtime configuration with sane, privacy-first defaults."""

    db_path: Path = DB_PATH_DEFAULT
    poll_interval: float = POLL_INTERVAL_DEFAULT
    max_content_bytes: int = MAX_CONTENT_BYTES

    host: str = SERVER_HOST_DEFAULT
    port: int = SERVER_PORT_DEFAULT
    open_browser: bool = True

    hotkey: str = HOTKEY_DEFAULT
    enable_hotkey: bool = True

    page_size: int = PAGE_SIZE_DEFAULT

    # Search semantics
    embedding_backend: str = EMBEDDING_BACKEND_DEFAULT
    local_embedding_model: str = LOCAL_EMBEDDING_MODEL_DEFAULT
    api_embedding_base_url: str = API_EMBEDDING_BASE_URL_DEFAULT
    api_embedding_model: str = API_EMBEDDING_MODEL_DEFAULT
    api_key: str = ""  # read from env CLIPMIND_API_KEY in production
    semantic_weight: float = SEMANTIC_WEIGHT_DEFAULT

    # Privacy
    skip_secret_like: bool = SKIP_SECRET_LIKE_DEFAULT
    blocklist_patterns: list = field(
        default_factory=lambda: list(BLOCKLIST_PATTERNS_DEFAULT)
    )

    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def resolved_api_key(self) -> str:
        return self.api_key or os.environ.get("CLIPMIND_API_KEY", "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "db_path": str(self.db_path),
            "poll_interval": self.poll_interval,
            "hotkey": self.hotkey,
            "host": self.host,
            "port": self.port,
            "embedding_backend": self.embedding_backend,
            "local_embedding_model": self.local_embedding_model,
            "semantic_weight": self.semantic_weight,
            "skip_secret_like": self.skip_secret_like,
        }


# ---------------------------------------------------------------------------
def _coerce(typ: type, value: Any, fallback: Any) -> Any:
    try:
        return typ(value)
    except (TypeError, ValueError):
        return fallback


def load_config(config_path: Path = CONFIG_PATH) -> Config:
    """Merge defaults + config file + environment into a Config."""
    cfg = Config()

    file_values: Dict[str, Any] = {}
    if config_path.exists() and tomllib is not None:
        try:
            with open(config_path, "rb") as fh:
                file_values = tomllib.load(fh).get("clipmind", {})
        except Exception:
            # A broken config file must never stop the app from starting.
            print(f"[clipmind] warning: could not parse {config_path}", file=sys.stderr)

    def apply(key: str, typ: type, attr: str | None = None) -> None:
        attr = attr or key
        if key in file_values:
            setattr(cfg, attr, _coerce(typ, file_values[key], getattr(cfg, attr)))

    apply("db_path", str)
    apply("poll_interval", float)
    apply("max_content_bytes", int)
    apply("host", str)
    apply("port", int)
    apply("hotkey", str)
    apply("page_size", int)
    apply("embedding_backend", str)
    apply("local_embedding_model", str)
    apply("api_embedding_base_url", str)
    apply("api_embedding_model", str)
    apply("api_key", str)
    apply("semantic_weight", float)
    apply("skip_secret_like", bool)

    if "blocklist_patterns" in file_values and isinstance(
        file_values["blocklist_patterns"], list
    ):
        cfg.blocklist_patterns = file_values["blocklist_patterns"]

    # Environment overrides
    env = os.environ
    if env.get("CLIPMIND_DB_PATH"):
        cfg.db_path = Path(env["CLIPMIND_DB_PATH"])
    if env.get("CLIPMIND_HOST"):
        cfg.host = env["CLIPMIND_HOST"]
    if env.get("CLIPMIND_PORT"):
        cfg.port = _coerce(int, env["CLIPMIND_PORT"], cfg.port)
    if env.get("CLIPMIND_API_KEY"):
        cfg.api_key = env["CLIPMIND_API_KEY"]
    if env.get("CLIPMIND_HOTKEY"):
        cfg.hotkey = env["CLIPMIND_HOTKEY"]

    if str(cfg.db_path):
        cfg.db_path = Path(cfg.db_path).expanduser()

    cfg.ensure_dirs()
    return cfg
