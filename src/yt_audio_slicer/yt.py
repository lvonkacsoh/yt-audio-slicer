"""Thin yt-dlp wrappers shared by the commands."""

from __future__ import annotations

import re

import yt_dlp


class _SilentLogger:
    """yt-dlp prints 'ERROR: …' itself; we report errors ourselves, so swallow its output."""

    def debug(self, msg):
        pass

    info = warning = error = debug


# filled from --cookies / --cookies-from-browser
YDL_AUTH: dict = {}


class BotCheckError(Exception):
    """YouTube refused to serve this machine ('Sign in to confirm you're not a bot' / HTTP 429)."""


BOT_CHECK_RE = re.compile(r"not a bot|HTTP Error 429|Too Many Requests", re.I)
FORBIDDEN_RE = re.compile(r"HTTP Error 403|403: Forbidden", re.I)


def is_bot_check(e: BaseException) -> bool:
    return isinstance(e, yt_dlp.utils.DownloadError) and bool(BOT_CHECK_RE.search(str(e)))


def is_forbidden(e: BaseException | str) -> bool:
    """HTTP 403 while downloading: usually a stale / throttled stream URL, often gone on the next try."""
    return bool(FORBIDDEN_RE.search(str(e)))


def short_error(err: str) -> str:
    if is_forbidden(err):
        return "HTTP 403 Forbidden"
    line = re.sub(r"^(\w+Error:\s*)?ERROR:\s*(\[\w+\]\s*)?", "", err.strip().splitlines()[0] if err.strip() else err)
    return line[:100] + ("…" if len(line) > 100 else "")


def base_opts() -> dict:
    return {"quiet": True, "no_warnings": True, "noprogress": True, "logger": _SilentLogger()} | YDL_AUTH


def add_auth_args(p) -> None:
    p.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="use YouTube login cookies from a browser (firefox, chrome, chromium, brave, ...)",
    )
    p.add_argument("--cookies", metavar="FILE", help="use cookies from a Netscape-format cookies.txt")


def set_auth(args) -> None:
    YDL_AUTH.clear()
    if args.cookies:
        YDL_AUTH["cookiefile"] = str(args.cookies)
    if args.cookies_from_browser:
        browser, _, profile = args.cookies_from_browser.partition(":")
        YDL_AUTH["cookiesfrombrowser"] = (browser, profile or None, None, None)


def list_entries(url: str, no_playlist: bool = False) -> tuple[str | None, list[dict]]:
    """Return (playlist_title, entries). A single video yields one entry and no title."""
    with yt_dlp.YoutubeDL(base_opts() | {"extract_flat": "in_playlist", "noplaylist": no_playlist}) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        for i, e in enumerate(entries, 1):
            if not e.get("playlist_index"):
                e["playlist_index"] = i
        return info.get("title"), entries
    return None, [info]


def entry_url(e: dict) -> str:
    return e.get("webpage_url") or e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
