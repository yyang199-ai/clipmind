"""Command line interface.

    clipmind                start the server + UI (default: serve)
    clipmind serve          same, explicit
    clipmind search "..."   search from the terminal
    clipmind add "text"     remember something manually
    clipmind status         quick stats
    clipmind extract "..."  show what the extractor thinks
    clipmind demo           seed the DB with a vivid sample history
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import __version__
from .config import CONFIG_PATH, Config, load_config
from .extractor import extract
from .store import ClipStore


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clipmind",
        description="Your clipboard, with a brain — local-first clipboard history.",
    )
    p.add_argument("--version", action="version", version=f"clipmind {__version__}")
    p.add_argument(
        "--db",
        default=None,
        help="override database path (default ~/.clipmind/clipmind.db)",
    )
    sub = p.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the server and web UI (default)")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--no-hotkey", action="store_true")
    serve.add_argument("-v", "--verbose", action="store_true")

    q = sub.add_parser("search", help="search clipboard history")
    q.add_argument("query", nargs="*", help="search terms")
    q.add_argument("--mode", default="auto", choices=["auto", "semantic", "keyword"])
    q.add_argument("--limit", type=int, default=15)

    a = sub.add_parser("add", help="remember text manually")
    a.add_argument("text", nargs="*")

    sub.add_parser("status", help="show stats")

    e = sub.add_parser("extract", help="classify a piece of text")
    e.add_argument("text", nargs="*")

    sub.add_parser("demo", help="seed sample history (great for first run)")

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # default action = serve
    if not argv or (argv[0] not in ("serve", "search", "add", "status",
                                    "extract", "demo", "--version", "-h", "--help")):
        argv = ["serve"] + argv

    parser = build_parser()
    args = parser.parse_args(argv)

    cfg: Config = load_config()
    if args.db:
        cfg.db_path = args.db
        cfg.ensure_dirs()

    rc = 0
    if args.command == "serve":
        rc = _cmd_serve(cfg, args)
    elif args.command == "search":
        rc = _cmd_search(cfg, args)
    elif args.command == "add":
        rc = _cmd_add(cfg, args)
    elif args.command == "status":
        rc = _cmd_status(cfg)
    elif args.command == "extract":
        rc = _cmd_extract(args)
    elif args.command == "demo":
        rc = _cmd_demo(cfg)
    return rc


# ---------------------------------------------------------------------------
def _cmd_serve(cfg: Config, args) -> int:
    import uvicorn

    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.no_browser:
        cfg.open_browser = False
    if args.no_hotkey:
        cfg.enable_hotkey = False

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from .server import create_app

    app = create_app(cfg)

    url = f"http://{cfg.host}:{cfg.port}"
    print(_banner(cfg, url))

    if cfg.open_browser:
        _open_browser_later(url)

    if cfg.enable_hotkey:
        _register_hotkey(cfg, url)

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")
    return 0


def _banner(cfg: Config, url: str) -> str:
    from .embeddings import create_backend

    try:
        backend = create_backend(cfg)
        semantic = (
            "ON" if backend.available else "off (pip install 'clipmind[semantic]')"
        )
    except Exception:
        semantic = "off"
    lines = [
        "",
        "  ╭──────────────────────────────────────────╮",
        "  │  ⧉  ClipMind — your clipboard, with a    │",
        "  │     brain. Everything stays local.       │",
        "  ╰──────────────────────────────────────────╯",
        f"   UI      {url}",
        f"   data    {cfg.db_path}",
        f"   search  semantic: {semantic}",
    ]
    if cfg.enable_hotkey:
        lines.append(f"   hotkey  {cfg.hotkey}  (opens the UI)")
    lines.append(f"   config  {CONFIG_PATH} (optional)")
    lines.append("")
    return "\n".join(lines)


def _open_browser_later(url: str, delay: float = 1.2) -> None:
    import threading
    import webbrowser

    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open, daemon=True).start()


def _register_hotkey(cfg: Config, url: str) -> None:
    try:
        import keyboard
    except Exception:
        print("[clipmind] hotkey unavailable (keyboard module not usable here)")
        return

    def _open():
        import webbrowser

        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        keyboard.add_hotkey(cfg.hotkey, _open, suppress=False)
    except Exception as exc:
        print(f"[clipmind] hotkey registration failed: {exc}")


# ---------------------------------------------------------------------------
def _cmd_search(cfg: Config, args) -> int:
    from .embeddings import create_backend
    from .search import SearchEngine

    query = " ".join(args.query).strip()
    if not query:
        print("usage: clipmind search <query>")
        return 2
    store = ClipStore(cfg.db_path)
    backend = create_backend(cfg)
    engine = SearchEngine(store, backend, cfg.semantic_weight)
    t0 = time.perf_counter()
    hits = engine.search(query, limit=args.limit, mode=args.mode)
    took = (time.perf_counter() - t0) * 1000
    if not hits:
        print(f"no matches for {query!r} ({took:.0f} ms)")
        return 1
    for i, hit in enumerate(hits, 1):
        preview = hit.clip.content.replace("\n", " ")[:76]
        badge = hit.clip.kind
        score = f"{hit.score:5.2f}"
        print(f"{i:2d}. [{badge:9s}] {score} {preview}")
    print(f"— {len(hits)} hits in {took:.0f} ms "
          f"(mode={args.mode}, semantic={backend.available})")
    return 0


def _cmd_add(cfg: Config, args) -> int:
    from .watcher import ClipboardWatcher

    text = " ".join(args.text)
    watcher = ClipboardWatcher(ClipStore(cfg.db_path), cfg)
    clip = watcher.add_manual(text)
    if clip:
        print(f"stored #{clip.id} [{clip.kind}]")
        return 0
    print("nothing to store")
    return 1


def _cmd_status(cfg: Config) -> int:
    store = ClipStore(cfg.db_path)
    s = store.stats()
    print(f"clipmind  v{__version__}")
    print(f"database  {cfg.db_path}")
    print(f"clips     {s['total']} ({s['pinned']} pinned, {s['embedded']} embedded)")
    if s["total"]:
        age_days = (time.time() - (s["oldest"] or time.time())) / 86400
        print(f"span      {age_days:.1f} days of history")
    kinds = ", ".join(f"{k}×{n}" for k, n in list(s["kinds"].items())[:8])
    if kinds:
        print(f"kinds     {kinds}")
    print(f"fts       {s['fts_tokenizer']} tokenizer")
    return 0


def _cmd_extract(args) -> int:
    text = " ".join(args.text)
    if not text:
        print("usage: clipmind extract <text>")
        return 2
    ex = extract(text)
    print(f"kind:     {ex.kind}  ({ex.label})")
    for key, val in ex.meta.items():
        if val:
            print(f"{key + ':':9s} {val}")
    return 0


# ---------------------------------------------------------------------------
DEMO_CLIPS = [
    "https://github.com/anthropics/anthropic-sdk-python",
    "您的验证码是 483920，10分钟内有效。【某银行】",
    "+86 138 0013 8000",
    "sales@example.com",
    "#3B82F6",
    'git push --force-with-lease origin feature/clip-history',
    '{"model": "gpt-4o-mini", "temperature": 0.7, "max_tokens": 1024}',
    "ssh -i ~/.ssh/id_ed25519 deploy@203.0.113.10",
    "C:\\Users\\you\\Documents\\合同_v3.docx",
    "会议纪要：\n- 下周三发布 v2\n- 剪贴板工具持续投入\n- 联系 UI 组要设计稿",
    "pip install fastembed",
    "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "长文：transformer 的注意力机制本质上是一种可学习的、内容寻址的键值存储……",
    "docker run -it --rm -v $PWD:/app python:3.12-slim bash",
    "	SELECT id, name, created_at FROM orders WHERE status = 'paid';",
]


def _cmd_demo(cfg: Config) -> int:
    from .extractor import extract
    from .watcher import ClipboardWatcher

    store = ClipStore(cfg.db_path)
    watcher = ClipboardWatcher(store, cfg)
    n = 0
    base = time.time() - len(DEMO_CLIPS) * 3600  # fake hourly history
    for i, text in enumerate(DEMO_CLIPS):
        clip, created = store.add(text, source="demo", extraction=extract(text))
        if clip and created:
            n += 1
    print(f"seeded {n} sample clips into {cfg.db_path}")
    print("run `clipmind` and try searching: 验证码 / github / 颜色 / 命令")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
