# Contributing to ClipMind

Thanks for helping build the clipboard manager you actually want to use.

## Ground rules

- **Privacy is non-negotiable.** No telemetry, no analytics, no "phone home",
  no cloud defaults. Anything that touches the network must be opt-in and
  documented.
- **Graceful degradation.** If an optional dependency is missing, the app
  must still start, still capture, still search. Never crash because a model
  didn't load.
- **Secrets stay out.** New detection heuristics need tests with real-world
  shapes (sanitized). When in doubt, skip storing — a missed clip costs
  nothing; a leaked token costs everything.

## Dev setup

```bash
git clone https://github.com/yyang199-ai/clipmind
cd clipmind
pip install -e '.[dev]'        # + '.[semantic]' if you want embeddings
pytest                         # 58 tests, should be green
clipmind demo && clipmind      # poke at the UI
```

## Where things live

| Path | What |
|---|---|
| `clipmind/extractor.py` | content-type detection + entity mining (pure functions) |
| `clipmind/store.py` | SQLite + FTS5 persistence |
| `clipmind/search.py` | hybrid ranking (FTS + vectors + intent + behaviour) |
| `clipmind/embeddings.py` | local fastembed / OpenAI-compatible backends |
| `clipmind/watcher.py` | clipboard polling + privacy guard + bg indexer |
| `clipmind/server.py` | FastAPI REST + WebSocket + static UI |
| `clipmind/static/` | the web panel (vanilla JS, no build step) |

## PR checklist

- [ ] tests added/updated and passing (`pytest`)
- [ ] no new hard dependency (if unavoidable, justify it in the PR)
- [ ] works with zero optional extras installed
- [ ] README updated if user-facing behaviour changed

## Adding a new content type

1. Add a regex / heuristic in `extractor.py` + a `KIND_*` constant.
2. Register the kind in `KIND_LABELS`, `KIND_ICONS`, `KIND_ORDER`.
3. Add a badge color + label mapping in `static/app.js` (`KIND_LABEL`,
   `.badge.k-<kind>` in CSS).
4. Tests in `tests/test_extractor.py` — happy path **and** near-misses
   (things that must NOT match).
