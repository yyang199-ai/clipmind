"""Smart content extraction.

Pure-function heuristics that classify clipboard text and pull structured
entities out of it. No ML required — this is the "instant intelligence"
layer that works out of the box with zero model downloads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Regex library
# ---------------------------------------------------------------------------

RE_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# CN mobile (spaced or not, optional +86) | landline with area code
RE_PHONE = re.compile(
    r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d(?:[\s-]?\d{4}){2}(?!\d)"
    r"|(?<!\d)0\d{2,3}[\s-]?\d{7,8}(?!\d)"
)
RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
RE_COLOR = re.compile(
    r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b"
    r"|\brgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+(?:\s*,\s*[\d.]+)?\s*\)"
    r"|\bhsla?\(\s*\d+\s*,\s*\d+%\s*,\s*\d+%(?:\s*,\s*[\d.]+)?\s*\)",
)
RE_OTP = re.compile(r"(?<!\d)\d{4,8}(?!\d)")
RE_OTP_CONTEXT = re.compile(
    r"(?:验证码|校验码|动态码|授权码|code is|verification code|otp|pin)\D{0,20}(\d{4,8})",
    re.IGNORECASE,
)
RE_WINDOWS_PATH = re.compile(
    r"\b[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*"
)
RE_UNIX_PATH = re.compile(r"(?<![\w~])/[\w.\-/]{2,}|~/?[\w.\-/]*")
RE_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{16,}\b")
RE_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

COMMAND_PREFIXES = (
    "git ", "npm ", "npx ", "yarn ", "pnpm ", "pip ", "pip3 ", "python ",
    "python3 ", "node ", "docker ", "kubectl ", "curl ", "wget ", "ssh ",
    "scp ", "cd ", "ls ", "mv ", "cp ", "rm ", "chmod ", "chown ", "sudo ",
    "apt ", "brew ", "cargo ", "go ", "make ", "gcc ", "java ", "mvn ",
    "gradle ", "dotnet ", "pod ", "conda ", "uv ", "ffmpeg ", "youtube-dl ",
    "yt-dlp ", "tar ", "unzip ", "systemctl ", "ping ", "tracert ",
    "ipconfig ", "ifconfig ", "netstat ", "tasklist ", "dir ", "cls ",
    "ffmpeg", "jq ", "awk ", "sed ", "grep ", "find ", "cat ", "echo ",
)

KIND_COMMAND = "command"
KIND_JSON = "json"
KIND_URL = "url"
KIND_EMAIL = "email"
KIND_PHONE = "phone"
KIND_OTP = "otp"
KIND_COLOR = "color"
KIND_PATH = "path"
KIND_IP = "ip"
KIND_UUID = "uuid"
KIND_CODE = "code"
KIND_MARKDOWN = "markdown"
KIND_TABLE = "table"
KIND_TEXT = "text"

# Human-readable labels (zh + en) used by the UI and CLI.
KIND_LABELS = {
    KIND_COMMAND: "命令 / Command",
    KIND_JSON: "JSON",
    KIND_URL: "链接 / Link",
    KIND_EMAIL: "邮箱 / Email",
    KIND_PHONE: "电话 / Phone",
    KIND_OTP: "验证码 / OTP",
    KIND_COLOR: "颜色 / Color",
    KIND_PATH: "路径 / Path",
    KIND_IP: "IP 地址",
    KIND_UUID: "UUID",
    KIND_CODE: "代码 / Code",
    KIND_MARKDOWN: "Markdown",
    KIND_TABLE: "表格 / Table",
    KIND_TEXT: "文本 / Text",
}

KIND_ORDER = {  # smaller = reported first when a clip matches many kinds
    KIND_OTP: 0,
    KIND_URL: 1,
    KIND_EMAIL: 2,
    KIND_PHONE: 3,
    KIND_COLOR: 4,
    KIND_JSON: 5,
    KIND_COMMAND: 6,
    KIND_UUID: 7,
    KIND_IP: 8,
    KIND_PATH: 9,
    KIND_TABLE: 10,
    KIND_CODE: 11,
    KIND_MARKDOWN: 12,
    KIND_TEXT: 99,
}

# Icons per kind for the UI badge.
KIND_ICONS = {
    KIND_COMMAND: "❯",
    KIND_JSON: "{}",
    KIND_URL: "🔗",
    KIND_EMAIL: "✉",
    KIND_PHONE: "☎",
    KIND_OTP: "🔑",
    KIND_COLOR: "🎨",
    KIND_PATH: "📂",
    KIND_IP: "🌐",
    KIND_UUID: "🆔",
    KIND_CODE: "⌨",
    KIND_MARKDOWN: "📝",
    KIND_TABLE: "▦",
    KIND_TEXT: "¶",
}

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class Extraction:
    """Classification + entities found in one clipboard item."""

    kind: str = KIND_TEXT
    entities: List[str] = field(default_factory=list)
    # Extra facts, e.g. {"otp": "123456", "json_keys": [...]}.
    meta: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def icon(self) -> str:
        return KIND_ICONS.get(self.kind, "¶")


# ---------------------------------------------------------------------------
# Secret detection (privacy guard) — used by the watcher BEFORE storing.
# ---------------------------------------------------------------------------

_SECRET_HINTS = re.compile(r"otpauth://|BEGIN [A-Z ]*PRIVATE KEY", re.IGNORECASE)
_HIGH_ENTROPY = re.compile(r"^[A-Za-z0-9+/\-_!@#$%^&*]{12,}$")

TOKEN_SHAPES = (
    re.compile(r"^ghp_[A-Za-z0-9]{36,}$"),
    re.compile(r"^sk-[A-Za-z0-9]{20,}$"),
    re.compile(r"^sk-ant-[A-Za-z0-9\-]{20,}$"),
    re.compile(r"^xox[bp]-[A-Za-z0-9-]+$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # JWT
)


def looks_like_secret(text: str) -> bool:
    """Heuristic: should we refuse to remember this clip at all?

    Catches: TOTP seeds, private keys, obvious API-token shapes, JWTs, and
    high-entropy blob passwords. Deliberately conservative — ordinary
    words and sentences never match.
    """
    t = text.strip()
    if not t:
        return False
    if _SECRET_HINTS.search(t):
        return True
    for shape in TOKEN_SHAPES:
        if shape.match(t):
            return True
    # A long, space-free mixed-case+digit string is usually a password.
    if " " not in t and len(t) >= 14 and _HIGH_ENTROPY.match(t):
        has_upper = any(c.isupper() for c in t)
        has_lower = any(c.islower() for c in t)
        has_digit = any(c.isdigit() for c in t)
        if has_upper and has_lower and has_digit:
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_json(text: str) -> Optional[dict]:
    t = text.strip()
    if not (t.startswith("{") and t.endswith("}")) and not (
        t.startswith("[") and t.endswith("]")
    ):
        return None
    if "\n" in t and not t.startswith("{"):
        return None
    try:
        return json.loads(t)
    except (json.JSONDecodeError, RecursionError):
        return None


def _looks_like_code(text: str) -> bool:
    if "```" in text:
        return True
    if text.count("\n") >= 2:
        strong = re.compile(
            r"^\s*(async def |def |class |#include |package |func |"
            r"function |export default |public class |private |"
            r"import [a-zA-Z_.]+ |from [a-zA-Z_.]+ import )"
        )
        lines = text.splitlines()
        if any(strong.match(ln) for ln in lines[:50]):
            return True
        code_markers = (
            r"[;{}]\s*$",
            r"^\s*//",
            r"^\s*#",
            r"^\s*/\*",
            r"^\s+[A-Za-z_][\w.]*\(",
            r"^\s*(return|if|for|while|else|elif|try|except|switch|case)\b",
        )
        hits = sum(
            1
            for line in lines[:30]
            if any(re.search(m, line) for m in code_markers)
        )
        return hits >= max(2, len(lines[:30]) // 5)
    return False


def _looks_like_markdown(text: str) -> bool:
    markers = (
        r"^#{1,6}\s",
        r"^\s*[-*+]\s",
        r"^\s*\d+\.\s",
        r"\*\*[^*\n]+\*\*",
        r"`[^`\n]+`",
        r"^\s*>\s",
        r"^\|.*\|$",
    )
    lines = text.splitlines()
    hits = sum(1 for line in lines[:40] if any(re.search(m, line) for m in markers))
    return hits >= 2 and len(lines) >= 1


def _looks_like_table(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    pipe_lines = [ln for ln in lines if ln.count("|") >= 2]
    if len(pipe_lines) >= 2 and len(pipe_lines) >= len(lines) // 2:
        return True
    tab_lines = [ln for ln in lines if ln.count("\t") >= 2]
    return len(tab_lines) >= 2 and len(tab_lines) >= len(lines) // 2


def _looks_like_command(text: str) -> bool:
    t = text.strip()
    if "\n" in t:
        return False
    first = t.split("$", 1)[-1].lstrip()  # tolerate "$ git status"
    lowered = (first + " ").lower()
    return lowered.startswith(COMMAND_PREFIXES)


def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for it in items:
        it = it.strip().rstrip(".,;:!?\"'")
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out[:12]


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def extract(text: str) -> Extraction:
    """Classify clipboard text and mine entities from it.

    Returns the single most useful *kind* plus every structured entity we
    could find (URLs, emails, ...), so the UI can offer one-click copy of
    the important bit instead of the whole blob.
    """
    if not text or not text.strip():
        return Extraction()

    t = text.strip()
    ex = Extraction()

    # --- structured entities (always collected, regardless of kind) ---
    urls = _dedupe(RE_URL.findall(t))
    emails = _dedupe(RE_EMAIL.findall(t))
    phones = _dedupe(RE_PHONE.findall(t))
    colors = _dedupe(RE_COLOR.findall(t))
    otp_ctx = RE_OTP_CONTEXT.search(t)
    otp = otp_ctx.group(1) if otp_ctx else None
    if otp is None and RE_OTP.fullmatch(t):
        otp = t.strip()
    uuids = _dedupe(RE_UUID.findall(t))
    ips = [ip for ip in _dedupe(RE_IPV4.findall(t)) if not ip.endswith(".0")]

    ex.entities = urls or []
    ex.meta["urls"] = urls
    ex.meta["emails"] = emails
    ex.meta["phones"] = phones
    ex.meta["colors"] = colors
    ex.meta["uuids"] = uuids
    ex.meta["ips"] = ips
    if colors:
        ex.meta["color"] = colors[0]
    if otp:
        ex.meta["otp"] = otp

    # --- decide the dominant kind (first match in priority order) ---
    candidates: List[str] = []

    if otp:
        candidates.append(KIND_OTP)
    if urls and len(urls) == 1 and len(t) <= len(urls[0]) + 2:
        candidates.append(KIND_URL)
    if emails and len(emails) == 1 and t.replace(" ", "") == emails[0]:
        candidates.append(KIND_EMAIL)
    if phones and len(phones) == 1 and t.replace(" ", "").replace("-", "") in (
        p.replace(" ", "").replace("-", "") for p in phones
    ):
        candidates.append(KIND_PHONE)
    if colors and t.replace(" ", "") == colors[0].replace(" ", ""):
        candidates.append(KIND_COLOR)

    parsed = _looks_like_json(t)
    if parsed is not None:
        candidates.append(KIND_JSON)
        if isinstance(parsed, dict):
            ex.meta["json_keys"] = list(parsed.keys())[:10]

    if _looks_like_command(t):
        candidates.append(KIND_COMMAND)

    if uuids and len(t) <= len(uuids[0]) + 2:
        candidates.append(KIND_UUID)
    if ips and len(t) <= len(ips[0]) + 2:
        candidates.append(KIND_IP)
    if _looks_like_table(t):
        candidates.append(KIND_TABLE)
    if _looks_like_code(t):
        candidates.append(KIND_CODE)
    elif _looks_like_markdown(t):
        candidates.append(KIND_MARKDOWN)

    if candidates:
        ex.kind = min(candidates, key=lambda k: KIND_ORDER.get(k, 99))
    else:
        # keep paths out of dominant-kind unless standalone
        paths = _dedupe(RE_WINDOWS_PATH.findall(t) + RE_UNIX_PATH.findall(t))
        ex.meta["paths"] = paths
        if paths and len(t) <= len(paths[0]) + 2:
            ex.kind = KIND_PATH

    if ex.kind is KIND_TEXT:
        ex.entities = []
    return ex


def kind_badge(kind: str) -> str:
    """Small badge text for CLI output."""
    return f"{KIND_ICONS.get(kind, '¶')} {KIND_LABELS.get(kind, kind)}"
