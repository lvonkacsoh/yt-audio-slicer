"""Slice with ffmpeg (stream copy, no re-encode) and write ID3 tags with mutagen."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, WOAS

from .markers import Marker
from .naming import track_filename


@dataclass
class Track:
    number: int
    total: int
    title: str
    start: float
    end: float | None
    path: Path
    artist: str = ""


def cut(src: Path, dst: Path, start: float, end: float | None) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(src), "-map", "0:a", "-map_metadata", "-1", "-c", "copy", str(dst)]
    # -ss/-to before -i are both input-relative with modern ffmpeg
    subprocess.run(cmd, check=True, capture_output=True)


def slice_tracks(src: Path, markers: list[Marker], out_dir: Path, workers: int = 4) -> list[Track]:
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(markers)
    tracks = [
        Track(i + 1, total, m.title, m.start, m.end, out_dir / track_filename(i + 1, total, m.title))
        for i, m in enumerate(markers)
    ]
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda t: cut(src, t.path, t.start, t.end), tracks))
    return tracks


def write_tags(
    t: Track,
    *,
    album: str,
    album_artist: str,
    year: str | None,
    genre: str | None,
    cover: bytes | None,
    source_url: str,
    disc: int | None = None,
) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text=t.title))
    tags.add(TPE1(encoding=3, text=t.artist or album_artist))
    tags.add(TPE2(encoding=3, text=album_artist))
    tags.add(TALB(encoding=3, text=album))
    tags.add(TRCK(encoding=3, text=f"{t.number}/{t.total}"))
    if disc:
        tags.add(TPOS(encoding=3, text=str(disc)))
    if year:
        tags.add(TDRC(encoding=3, text=year))
    if genre:
        tags.add(TCON(encoding=3, text=genre))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    tags.add(WOAS(url=source_url))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=f"Sliced from {source_url}"))
    tags.save(t.path, v2_version=3)
