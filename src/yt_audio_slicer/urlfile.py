"""URL files: reading them, and writing the "remaining" copy that lets an interrupted run be resumed.

A URL file has one URL per line (several are allowed, separated by spaces); blank lines and lines starting with '#'
are skipped. Two kinds of comment lines are written back by the downloader:

  # continue: skip=ID1,ID2  (2 of 12 videos done)     above a partly done playlist, applies to the next URL
  # failed: HTTP 403 Forbidden - [12/26] Title         above a failed video, informational only
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.panel import Panel

from .ui import console
from .urls import url_key, video_in_playlist_url


@dataclass
class UrlFile:
    path: Path  # Path("-") for stdin
    lines: list[str]  # raw lines, kept to write the "remaining" copy


def read_url_file(path: Path) -> UrlFile:
    text = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8-sig")
    return UrlFile(path, text.splitlines())


def urls_in_line(line: str) -> list[str]:
    """URLs on one line of a URL file; blank lines and lines starting with '#' have none."""
    line = line.strip()
    return [] if not line or line.startswith("#") else line.split()


# "# continue: skip=ID1,ID2  (2 of 12 videos done)" -- written above a partly done playlist, applies to the next URL
CONTINUE_RE = re.compile(r"^\s*#\s*continue:\s*skip=([\w,-]*)")
# "# failed: HTTP 403 Forbidden - [12/26] Title" -- written above a failed video, informational only
FAILED_RE = re.compile(r"^\s*#\s*failed:")


def parse_url_file(f: UrlFile) -> list[tuple[str, set[str]]]:
    """(url, video IDs to skip) for every URL in the file, in order."""
    out, skip = [], set()
    for line in f.lines:
        if m := CONTINUE_RE.match(line):
            skip = {v for v in m.group(1).split(",") if v}
            continue
        urls = urls_in_line(line)
        for u in urls:
            out.append((u, skip))
        if urls:
            skip = set()
    return out


def remaining_path(path: Path) -> Path:
    """urls.txt -> urls.remaining.txt; urls.remaining.txt stays urls.remaining.txt (overwritten on resume)."""
    stem = path.stem.removesuffix(".remaining")
    return path.with_name(f"{stem}.remaining{path.suffix or '.txt'}")


@dataclass
class UrlProgress:
    url: str
    done: set[str] = field(default_factory=set)  # video IDs finished (or skipped as done)
    total: int = 0  # videos to process for this URL, 0 = not resolved yet
    playlist_id: str | None = None
    finished: bool = False  # every video was attempted (no interruption)
    failed: dict[str, str] = field(default_factory=dict)  # video ID -> "reason - [i/n] title"


def continue_line(prog: UrlProgress) -> str:
    return f"# continue: skip={','.join(sorted(prog.done))}  ({len(prog.done)} of {prog.total} videos done)"


def remaining_lines(u: str, prog: UrlProgress) -> list[str]:
    """What replaces URL u in the remaining file."""
    if prog.finished and prog.failed and prog.playlist_id:
        # the playlist ran to the end; only some videos failed -> list exactly those videos. The links keep the
        # playlist (watch?v=ID&list=…), so a retry still knows the album, the track number and the cover.
        out = []
        for vid, note in prog.failed.items():
            out += [f"# failed: {note}", video_in_playlist_url(vid, prog.playlist_id)]
        return out
    if prog.done:
        return [continue_line(prog), u]
    return [u]


def write_remaining(files: list[UrlFile], remaining: dict[str, UrlProgress]) -> None:
    """Copy each URL file without the URLs that are already done, so the run can be resumed with -f. Comments and
    blank lines are kept. A partly done playlist gets a '# continue:' line listing the videos that are finished; a
    playlist where single videos failed (e.g. HTTP 403) is replaced by links to just those videos.
    URLs from the command line / stdin are printed instead."""
    lines_out = []
    for f in files:
        if str(f.path) == "-":
            continue
        kept, n = [], 0
        for line in f.lines:
            if CONTINUE_RE.match(line) or FAILED_RE.match(line):  # old progress lines, rewritten below if needed
                continue
            urls = urls_in_line(line)
            if not urls:
                kept.append(line)
                continue
            left = [u for u in urls if url_key(u) in remaining]
            if len(left) == len(urls) and not any(
                remaining[url_key(u)].done or remaining[url_key(u)].failed for u in left
            ):
                kept.append(line)
            else:
                for u in left:  # one URL per line so each can carry its own progress
                    kept += remaining_lines(u, remaining[url_key(u)])
            n += len(left)
        if not n:
            continue
        out = remaining_path(f.path)
        out.write_text("\n".join(kept) + "\n", encoding="utf-8")
        lines_out.append(
            f"[bold]{n}[/] remaining URL(s) from {f.path} written to [bold]{out}[/]\n"
            f"  resume with: [bold]yt-audio-slicer -f {out}[/]"
        )
    from_file = {url_key(u) for f in files if str(f.path) != "-" for line in f.lines for u in urls_in_line(line)}
    loose = [prog for k, prog in remaining.items() if k not in from_file]
    if loose:
        rows = []
        for prog in loose:
            for line in remaining_lines(prog.url, prog):
                rows.append(f"  [dim]{line}[/]" if line.startswith("#") else f"  {line}")
        lines_out.append("Remaining URLs from the command line / stdin:\n" + "\n".join(rows))
    if lines_out:
        console.print(Panel("\n\n".join(lines_out), title="Not processed yet", border_style="yellow"))
