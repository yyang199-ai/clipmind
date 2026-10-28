# Configuration

ClipMind runs with **zero configuration**. Every option below has a sane
default; create `~/.clipmind/config.toml` to override any of them.

```toml
[clipmind]
# Storage
db_path = "~/.clipmind/clipmind.db"     # single SQLite file

# Server
host = "127.0.0.1"                      # loopback only, by design
port = 8377
open_browser = true                     # open the panel on start

# Capture
poll_interval = 0.4                     # seconds between clipboard polls
max_content_bytes = 100000              # ignore giant blobs
skip_secret_like = true                 # password/API-key guard

# Hotkey (opens the UI)
hotkey = "ctrl+shift+v"
enable_hotkey = true

# Search
semantic_weight = 0.6                   # 0 = keyword only, 1 = vector only
embedding_backend = "auto"              # auto | local | api | none
local_embedding_model = "BAAI/bge-small-zh-v1.5"

# API embeddings (OpenAI-compatible)
api_embedding_base_url = "https://api.openai.com/v1"
api_embedding_model = "text-embedding-3-small"
# api_key: prefer the CLIPMIND_API_KEY env var instead of the file

# Never remember content matching these (case-insensitive regexes)
blocklist_patterns = [
    "otpauth://",
    "-----BEGIN [A-Z ]*PRIVATE KEY-----",
]
```

## Environment variables

| Variable | Effect |
|---|---|
| `CLIPMIND_DB_PATH` | override database path |
| `CLIPMIND_HOST` / `CLIPMIND_PORT` | override binding |
| `CLIPMIND_API_KEY` | key for the API embedding backend |
| `CLIPMIND_HOTKEY` | override UI hotkey |

Precedence: env vars > config file > defaults.
