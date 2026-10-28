# Changelog

All notable changes to ClipMind are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-10-28

First public release.

### Added
- Clipboard watcher with polling capture, dedup, and re-copy usage tracking
- Smart extraction: 14 content types (OTP, URL, email, phone, JSON, color,
  command, path, IP, UUID, code, markdown, table, text) with entity mining
- SQLite storage with FTS5 (trigram tokenizer for CJK substring search)
- Hybrid search: BM25 + semantic cosine + intent recognition + recency/usage
  signals, with graceful degradation when no model is installed
- Embedding backends: local fastembed (optional extra) and any
  OpenAI-compatible API; `auto | local | api | none` selection
- Privacy guard: password/token/JWT/TOTP/private-key heuristics + user
  blocklist applied before storage
- FastAPI server: REST + WebSocket live feed, loopback-only binding
- Web panel: glass-dark UI, search modes, one-tap entity copy, kind filters,
  pin/delete, keyboard navigation (`/` focus, ↑↓, Enter, Esc)
- CLI: `serve`, `search`, `add`, `status`, `extract`, `demo`
- Global hotkey `Ctrl+Shift+V` (keyboard lib, optional)
- 58 tests + GitHub Actions CI (Linux & Windows, Python 3.9–3.13)
