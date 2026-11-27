"""Find track time markers: chapters -> description -> top comments -> silence detection."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")
# leading "01.", "1)", "#3 -", "Track 4:" etc.
TRACKNUM_RE = re.compile(r"^\s*(?:track\s*)?#?\d{1,3}\s*[.):\-–—]\s+", re.I)
SEP_CHARS = " \t-–—:|•·.,"


@dataclass
class Marker:
    start: float
    title: str
    end: float | None = None


@dataclass
class MarkerResult:
    markers: list[Marker]
    source: str  # chapters | description | comment | silence | none


def to_seconds(h: str | None, m: str, s: str) -> int:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def clean_title(title: str) -> str:
    t = re.sub(r"[\(\[]\s*[\)\]]", " ", title)  # leftovers of "[08:00]"
    t = TRACKNUM_RE.sub("", t.strip(SEP_CHARS)).strip(SEP_CHARS)
    if len(t) > 2 and t[0] in "\"\u201c'" and t[-1] in "\"\u201d'" and t.count(t[0]) <= 2:
        t = t[1:-1]
    # drop unbalanced quotes, e.g. 'Reverence" (Living in the Heart)'
    for q in ('"', "\u201c", "\u201d"):
        if t.count(q) % 2:
            t = t.replace(q, "")
    t = re.sub(r"\s{2,}", " ", t).strip()
    if t.count("(") > t.count(")"):
        t += ")"
    return t


def parse_timestamps(text: str) -> list[Marker]:
    """Parse lines like '02:12 : Title', '3. Title 1:02:03', '00:00 - 02:12 Title'."""
    markers: list[Marker] = []
    for line in text.splitlines():
        found = list(TS_RE.finditer(line))
        if not found:
            continue
        start = to_seconds(*found[0].groups())
        end = to_seconds(*found[1].groups()) if len(found) > 1 else None
        title = TS_RE.sub(" ", line)
        title = re.sub(r"https?://\S+", "", title)
        title = clean_title(title)
        if not title:
            continue
        markers.append(Marker(start, title, end if end and end > start else None))
    return markers if is_plausible(markers) else []


def is_plausible(markers: list[Marker], duration: float | None = None) -> bool:
    if len(markers) < 2:
        return False
    starts = [m.start for m in markers]
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        return False
    if starts[0] > 60:
        return False
    return not (duration and starts[-1] >= duration)


def fill_ends(markers: list[Marker], duration: float | None) -> list[Marker]:
    for i, m in enumerate(markers):
        nxt = markers[i + 1].start if i + 1 < len(markers) else duration
        if m.end is None or (nxt is not None and m.end > nxt):
            m.end = nxt
    return markers


def from_chapters(info: dict) -> list[Marker]:
    chapters = info.get("chapters") or []
    ms = [Marker(c["start_time"], clean_title(c.get("title") or ""), c.get("end_time")) for c in chapters]
    # YouTube auto-chapters sometimes come back as "<Untitled Chapter 1>"
    if any(not m.title or m.title.startswith("<Untitled") for m in ms):
        return []
    return ms if is_plausible(ms, info.get("duration")) else []


def best_comment(comments: list[dict], duration: float | None) -> list[Marker]:
    best: list[Marker] = []
    for c in comments:
        ms = parse_timestamps(c.get("text") or "")
        if ms and is_plausible(ms, duration) and len(ms) > len(best):
            best = ms
    return best


def detect_silence(
    audio_path: str, duration: float, min_silence: float = 1.5, noise_db: int = -40, min_track: float = 60
) -> list[Marker]:
    """Last-resort: split on silences (ffmpeg silencedetect)."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            audio_path,
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    cuts = []
    s_start = None
    for line in proc.stderr.splitlines():
        if m := re.search(r"silence_start: ([\d.]+)", line):
            s_start = float(m.group(1))
        elif (m := re.search(r"silence_end: ([\d.]+)", line)) and s_start is not None:
            cuts.append((s_start + float(m.group(1))) / 2)
            s_start = None
    starts = [0.0]
    for c in cuts:
        if c - starts[-1] >= min_track and duration - c >= min_track:
            starts.append(c)
    if len(starts) < 2:
        return []
    return [Marker(s, f"Track {i + 1}") for i, s in enumerate(starts)]


def find_markers(info: dict, comments: list[dict] | None = None) -> MarkerResult:
    duration = info.get("duration")
    if ms := from_chapters(info):
        return MarkerResult(fill_ends(ms, duration), "chapters")
    if (ms := parse_timestamps(info.get("description") or "")) and is_plausible(ms, duration):
        return MarkerResult(fill_ends(ms, duration), "description")
    if comments and (ms := best_comment(comments, duration)):
        return MarkerResult(fill_ends(ms, duration), "comment")
    return MarkerResult([], "none")
