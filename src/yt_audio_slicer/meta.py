"""Guess artist / album / year from yt-dlp info and optionally canonicalise via MusicBrainz."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import PROJECT_URL, __version__
from .naming import album_folder, bare_album, is_live, primary_artist, split_artists

# MusicBrainz asks for an application name, version and contact URL: https://musicbrainz.org/doc/MusicBrainz_API
USER_AGENT = f"yt-audio-slicer/{__version__} ( {PROJECT_URL} )"
NOISE_RE = re.compile(
    r"\s*[\(\[][^)\]]*(full\s*album|official|stream|lyric|audio|video|hd|hq|4k|remaster|visuali[sz]er)[^)\]]*[\)\]]",
    re.I,
)
TITLE_SPLIT_RE = re.compile(r"\s+[-–—|]\s+")
# '(Expanded Edition)', '[Deluxe Version]', '(2015 Remaster)' ... MusicBrainz files these under the plain title
EDITION_RE = re.compile(
    r"\s*[(\[][^)\]]*\b(edition|version|remaster(ed)?|deluxe|anniversary|reissue|bonus)\b[^)\]]*[)\]]", re.I
)
PLAYLIST_PREFIX_RE = re.compile(r"^(album|ep|single)\s*[-–—:]\s*", re.I)


def guess_release_type(*texts: str | None) -> str | None:
    """'EP' / 'Single' / 'Live' from a title, None for a (studio) album."""
    for t in texts:
        if t and (m := re.search(r"\b(EP|single)\b", t, re.I)):
            return "EP" if m.group(1).upper() == "EP" else "Single"
    return "Live" if is_live(*texts) else None


def album_folder_for(meta: AlbumMeta) -> str:
    return album_folder(meta.album, meta.year, meta.release_type)


@dataclass
class AlbumMeta:
    artist: str
    album: str
    year: str | None = None
    genre: str | None = None
    mb_release_group: str | None = None
    mb_score: int | None = None
    mb_error: str | None = None
    release_type: str | None = None  # "Album" | "EP" | "Single" | "Live"


def strip_noise(s: str) -> str:
    s = NOISE_RE.sub("", s)
    s = re.sub(r"\b(full\s*album|official\s*(audio|video)?)\b", "", s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).strip(" -–—|")


def channel_artist(info: dict) -> str:
    ch = info.get("artist") or info.get("creator") or info.get("channel") or info.get("uploader") or "Unknown Artist"
    ch = ch.split(",")[0]
    return re.sub(r"\s*(- Topic|VEVO|Official)$", "", ch, flags=re.I).strip()


def split_title(title: str) -> tuple[str | None, str]:
    """'Artist - Thing (Official ...)' -> ('Artist', 'Thing')."""
    clean = strip_noise(title)
    parts = TITLE_SPLIT_RE.split(clean, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, clean


def guess_year(info: dict) -> str | None:
    if info.get("release_year"):
        return str(info["release_year"])
    for text in (info.get("title") or "", info.get("description") or ""):
        if m := re.search(r"(?:released|out)\s+(?:in|on)?\s*(?:\w+\s+)?((?:19|20)\d{2})", text, re.I):
            return m.group(1)
        if m := re.search(r"[\(\[]((?:19|20)\d{2})[\)\]]", text):
            return m.group(1)
    return None


def guess_album(info: dict) -> AlbumMeta:
    t_artist, t_rest = split_title(info.get("title") or "")
    artist = info.get("artist") or t_artist or channel_artist(info)
    album = info.get("album") or t_rest
    genre = (info.get("genres") or [None])[0] if info.get("genres") else None
    return AlbumMeta(
        artist=primary_artist(artist),
        album=album,
        year=guess_year(info),
        genre=genre,
        release_type=guess_release_type(info.get("album"), info.get("title")),
    )


def track_from_info(info: dict) -> tuple[str, list[str]]:
    """(title, [primary artist, featured ...]) of a single song video.

    YouTube Music / '- Topic' uploads carry 'track' and 'artists'; those win over splitting the video title, which
    goes wrong for titles like 'Death of a Dream - The Embrace That Smothers, Pt. 7'."""
    artists = [a for a in info.get("artists") or [] if a] or split_artists(info.get("artist"))
    title = info.get("track")
    if not title:
        t_artist, song = split_title(info.get("title") or "")
        if t_artist and (
            not artists
            or t_artist.casefold() == ", ".join(artists).casefold()
            or primary_artist(t_artist).casefold() == artists[0].casefold()
        ):
            artists = artists or split_artists(t_artist)
            title = song
        else:  # the part before ' - ' is not the artist we know of -> it belongs to the title
            title = strip_noise(info.get("title") or "") or song
    if not artists:
        artists = [channel_artist(info)]
    return title or "untitled", artists


def playlist_album_meta(info: dict, playlist_title: str | None, track_artists: list[str]) -> AlbumMeta:
    """Album metadata for a playlist of single songs, taken once from its first song (+ the playlist title)."""
    album_artist = (info.get("album_artists") or [None])[0] or info.get("album_artist") or track_artists[0]
    plist = PLAYLIST_PREFIX_RE.sub("", playlist_title or "").strip()
    album = info.get("album") or plist or "Unknown Album"
    genre = (info.get("genres") or [None])[0] if info.get("genres") else None
    year = str(info["release_year"]) if info.get("release_year") else None
    return AlbumMeta(
        artist=primary_artist(album_artist),
        album=album,
        year=year,
        genre=genre,
        release_type=guess_release_type(album, playlist_title),
    )


def _get_json(url: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _mb_search(album: str, artist: str) -> tuple[dict | None, str | None]:
    q = f'releasegroup:"{album}" AND artist:"{artist}"'
    params = urllib.parse.urlencode({"query": q, "fmt": "json", "limit": 5})
    url = f"https://musicbrainz.org/ws/2/release-group/?{params}"
    err = None
    for attempt in range(3):  # MusicBrainz allows ~1 req/s, answers 503 otherwise
        try:
            return _get_json(url), None
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    return None, err


def musicbrainz_lookup(meta: AlbumMeta, min_score: int = 90) -> AlbumMeta:
    """Find the release group; take the canonical artist / album names, the first release year and the type.
    An album with an edition suffix ('… (Expanded Edition)') is looked up without it and keeps its own name."""
    names = [bare_album(meta.album)]
    if (plain := EDITION_RE.sub("", names[0]).strip()) and plain != names[0]:
        names.append(plain)
    groups, err = [], None
    for i, name in enumerate(names):
        if i:
            time.sleep(1)
        data, err = _mb_search(name, primary_artist(meta.artist))
        if data is None:
            break
        groups = [g for g in data.get("release-groups", []) if int(g.get("score", 0)) >= min_score]
        if groups:
            break
    if not groups:
        meta.mb_error = err or "no confident match"
        return meta
    # prefer albums over singles/compilations
    groups.sort(key=lambda g: (g.get("primary-type") != "Album", "secondary-types" in g, -int(g.get("score", 0))))
    g = groups[0]
    credit = g.get("artist-credit") or []
    artist = (credit[0].get("name") if credit else None) or meta.artist  # first credited artist only
    date = g.get("first-release-date") or ""
    rtype = g.get("primary-type")
    if rtype not in ("EP", "Single"):
        live = "Live" in (g.get("secondary-types") or []) or meta.release_type == "Live"
        rtype = "Live" if live else None
    edition_kept = i > 0  # matched without the edition suffix -> keep '… (Expanded Edition)', the track list differs
    return AlbumMeta(
        artist=artist,
        album=meta.album if edition_kept else (g.get("title") or meta.album),
        year=date[:4] or meta.year,
        genre=meta.genre,
        mb_release_group=g["id"],
        mb_score=int(g.get("score", 0)),
        release_type=rtype,
    )


def coverart_front(release_group_id: str, size: int = 1200) -> bytes | None:
    url = f"https://coverartarchive.org/release-group/{release_group_id}/front-{size}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception:
            time.sleep(1)
    return None
