"""Read an existing output library: audio files, their tags, and which YouTube videos they came from."""

from __future__ import annotations

import fnmatch
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.id3 import ID3, ID3NoHeaderError

from .urls import video_id

AUDIO_EXT = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac"}
DEFAULT_EXCLUDE = (".*", "_*")  # hidden folders (.work) and parked ones (_old_Epica)
NAT_RE = re.compile(r"(\d+)")


def natural_key(p: Path | str) -> list:
    """'2 - x' sorts before '10 - x'."""
    return [int(t) if t.isdigit() else t.casefold() for t in NAT_RE.split(str(p))]


def is_audio(p: Path) -> bool:
    return p.suffix.lower() in AUDIO_EXT and p.is_file()


def excluded(name: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


def walk(root: Path, exclude=DEFAULT_EXCLUDE):
    """os.walk over root that skips excluded folder names (never root itself)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted((d for d in dirnames if not excluded(d, exclude)), key=natural_key)
        yield Path(dirpath), dirnames, sorted(filenames, key=natural_key)


def iter_audio(root: Path, exclude=DEFAULT_EXCLUDE):
    for d, _, files in walk(root, exclude):
        for f in files:
            if Path(f).suffix.lower() in AUDIO_EXT:
                yield d / f


def _num(s: str | None) -> tuple[int | None, int | None]:
    """'3/12' -> (3, 12)."""
    if not s:
        return None, None
    a, _, b = str(s).partition("/")
    na = int(a) if a.strip().isdigit() else None
    nb = int(b) if b.strip().isdigit() else None
    return na, nb


@dataclass
class TrackInfo:
    path: Path
    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    number: int | None = None
    total: int | None = None
    disc: int | None = None
    year: str | None = None
    source_url: str | None = None

    @property
    def video_id(self) -> str | None:
        return video_id(self.source_url)


FILENAME_NUM_RE = re.compile(r"^\s*(\d{1,3})(?:\s*[-.]\s*|\s+)")


def read_track(path: Path) -> TrackInfo:
    t = TrackInfo(path)
    try:
        if path.suffix.lower() == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = None
            if tags is not None:

                def g(k: str) -> str | None:
                    return str(tags[k].text[0]) if k in tags and tags[k].text else None

                t.title, t.artist, t.album_artist, t.album = g("TIT2"), g("TPE1"), g("TPE2"), g("TALB")
                t.number, t.total = _num(g("TRCK"))
                t.disc = _num(g("TPOS"))[0]
                t.year = (g("TDRC") or g("TYER") or "")[:4] or None
                woas = tags.getall("WOAS")
                t.source_url = woas[0].url if woas else None
        else:
            f = mutagen.File(path, easy=True)
            if f is not None and f.tags is not None:

                def g(k: str) -> str | None:
                    return (f.tags.get(k) or [None])[0]

                t.title, t.artist, t.album_artist, t.album = g("title"), g("artist"), g("albumartist"), g("album")
                t.number, t.total = _num(g("tracknumber"))
                t.disc = _num(g("discnumber"))[0]
                t.year = (g("date") or "")[:4] or None
                t.source_url = g("website")
    except Exception:  # unreadable / broken tags: fall back to the file name below
        pass
    if t.number is None and (m := FILENAME_NUM_RE.match(path.name)):
        t.number = int(m.group(1))
    return t


def library_index(root: Path, exclude=DEFAULT_EXCLUDE) -> dict[str, list[TrackInfo]]:
    """YouTube video ID -> tracks in the library that were made from it (via the WOAS / source URL tag).
    Files that were moved or renamed by hand are still found."""
    index: dict[str, list[TrackInfo]] = {}
    if not root.is_dir():
        return index
    for p in iter_audio(root, exclude):
        t = read_track(p)
        if vid := t.video_id:
            index.setdefault(vid, []).append(t)
    return index


def album_dirs(root: Path, exclude=DEFAULT_EXCLUDE) -> dict[Path, list[TrackInfo]]:
    """Every folder below root that directly contains audio files -> its tracks."""
    out: dict[Path, list[TrackInfo]] = {}
    for d, _, files in walk(root, exclude):
        tracks = [read_track(d / f) for f in files if Path(f).suffix.lower() in AUDIO_EXT]
        if tracks:
            out[d] = tracks
    return out


def common(values) -> object | None:
    """Most common non-empty value (first seen wins a tie)."""
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else None
