"""YouTube URL helpers: normalisation for duplicate detection, video / playlist IDs."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

YT_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
YT_ID_PATH_RE = re.compile(r"^/(?:shorts|live|embed|v)/([\w-]{11})")
# lists that are not a "release": mixes / radio (RD…), uploads (UU…), liked (LL), watch later (WL), favourites (FL)
NON_ALBUM_LIST_RE = re.compile(r"^(RD|UU|LL$|WL$|FL)")


def _parts(url: str) -> tuple[str | None, str | None] | None:
    """(video id, playlist id) of a YouTube URL, or None for other URLs."""
    u = urlsplit(url.strip())
    host = (u.hostname or "").removeprefix("www.").removeprefix("m.").removeprefix("music.")
    if host not in YT_HOSTS:
        return None
    q = parse_qs(u.query)
    vid = q.get("v", [None])[0]
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0] or None
    elif m := YT_ID_PATH_RE.match(u.path):
        vid = m.group(1)
    return vid, q.get("list", [None])[0]


def video_id(url: str | None) -> str | None:
    if not url:
        return None
    p = _parts(url)
    return p[0] if p else None


def playlist_id(url: str | None) -> str | None:
    if not url:
        return None
    p = _parts(url)
    return p[1] if p else None


def url_key(url: str) -> str:
    """Normalize a URL for duplicate detection: youtu.be/X, watch?v=X&t=30, /shorts/X, music.youtube.com ... all
    map to the same key. Non-YouTube URLs are compared as given."""
    p = _parts(url)
    if not p or p == (None, None):
        return url.strip()
    vid, lst = p
    return f"yt:v={vid or ''}:list={lst or ''}"


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def playlist_url(lst: str) -> str:
    return f"https://www.youtube.com/playlist?list={lst}"


def video_in_playlist_url(vid: str, lst: str) -> str:
    """A link to one video that keeps its playlist (album) context; see `video_in_playlist`."""
    return f"https://www.youtube.com/watch?v={vid}&list={lst}"


def video_in_playlist(url: str) -> tuple[str, str] | None:
    """`watch?v=ID&list=LIST` -> (ID, LIST): process only this video, but as part of the playlist (album name,
    track number, cover). Mixes, uploads, liked videos and watch later are not albums: those URLs are a plain video."""
    p = _parts(url)
    if not p or not p[0] or not p[1] or NON_ALBUM_LIST_RE.match(p[1]):
        return None
    return p[0], p[1]


def is_plain_video_with_list(url: str) -> bool:
    """A video URL whose list is a mix / uploads / watch later: download just the video."""
    p = _parts(url)
    return bool(p and p[0] and p[1] and NON_ALBUM_LIST_RE.match(p[1]))
