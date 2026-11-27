"""Pick a front cover: square thumbnail -> Cover Art Archive -> local file -> cropped thumbnail."""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .meta import AlbumMeta, coverart_front

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class Cover:
    data: bytes | None
    source: str
    notes: list[str] = field(default_factory=list)
    size: tuple[int, int] | None = None


def image_size(path: Path) -> tuple[int, int] | None:
    p = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        s = json.loads(p.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except (ValueError, KeyError, IndexError):
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def to_jpeg(src: Path, dst: Path, square_crop: bool = False, max_side: int = 1200) -> bool:
    vf = []
    if square_crop:
        vf.append("crop='min(iw,ih)':'min(iw,ih)'")
    vf.append(f"scale='min({max_side},iw)':-2")
    p = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", ",".join(vf), "-q:v", "2", str(dst)],
        capture_output=True,
    )
    return p.returncode == 0 and dst.exists()


def thumbnail_problem(size: tuple[int, int] | None) -> str | None:
    if not size:
        return "no thumbnail"
    w, h = size
    if not 0.9 <= w / h <= 1.1:
        return f"thumbnail not square ({w}x{h})"
    if min(w, h) < 300:
        return f"thumbnail too small ({w}x{h})"
    return None


def find_local_cover(meta: AlbumMeta, explicit: Path | None, covers_dir: Path | None) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
    if not covers_dir or not covers_dir.is_dir():
        return None
    images = [p for p in covers_dir.rglob("*") if p.suffix.lower() in IMG_EXT]
    target = _norm(f"{meta.artist} {meta.album}")
    best, best_score = None, 0.0
    for p in images:
        name = _norm(p.stem)
        score = max(
            difflib.SequenceMatcher(None, name, target).ratio(),
            difflib.SequenceMatcher(None, name, _norm(meta.album)).ratio(),
        )
        if score > best_score:
            best, best_score = p, score
    if best and best_score >= 0.6:
        return best
    for generic in ("cover", "folder", "front"):
        for p in images:
            if p.stem.lower() == generic:
                return p
    return None


def choose_cover(
    thumb: Path | None, meta: AlbumMeta, work: Path, *, use_caa: bool, explicit: Path | None, covers_dir: Path | None
) -> Cover:
    notes: list[str] = []
    out = work / "cover.jpg"
    if explicit:  # user override wins
        if to_jpeg(explicit, out):
            return Cover(out.read_bytes(), f"local file ({explicit.name})", notes, image_size(out))
        notes.append(f"could not read {explicit}")

    size = image_size(thumb) if thumb and thumb.exists() else None
    problem = thumbnail_problem(size)
    if not problem and to_jpeg(thumb, out):
        return Cover(out.read_bytes(), "video thumbnail", notes, image_size(out))
    notes.append(problem or "thumbnail conversion failed")

    if use_caa and meta.mb_release_group:
        if data := coverart_front(meta.mb_release_group):
            raw = work / "caa_cover"
            raw.write_bytes(data)
            if to_jpeg(raw, out):
                return Cover(out.read_bytes(), "Cover Art Archive", notes, image_size(out))
        notes.append("no Cover Art Archive image")
    elif use_caa:
        notes.append("no MusicBrainz match for cover lookup")

    if (local := find_local_cover(meta, None, covers_dir)) and to_jpeg(local, out):
        return Cover(out.read_bytes(), f"local file ({local.name})", notes, image_size(out))

    if thumb and size and to_jpeg(thumb, out, square_crop=True):
        return Cover(out.read_bytes(), "thumbnail (center-cropped)", notes, image_size(out))
    return Cover(None, "none", notes)
