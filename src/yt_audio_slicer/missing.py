"""List tracks missing from the albums in the library.

A track is missing when its number is absent from 1..total ('3/12' tags) or, without totals, from 1..highest.
With --urls, the playlists in that file are looked up: an album whose tracks came from one of them gets the
YouTube links of its missing tracks. --save writes those links to a URL file that the downloader reads
(yt-audio-slicer -f FILE), so the gaps are filled in the right album folder with the right track numbers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from rich.table import Table

from .library import DEFAULT_EXCLUDE, TrackInfo, album_dirs, common
from .ui import console, ranges, rel
from .urlfile import read_url_file, urls_in_line
from .urls import playlist_id, video_in_playlist_url, watch_url
from .yt import BotCheckError, add_auth_args, entry_url, is_bot_check, list_entries, set_auth


@dataclass
class Gap:
    album: Path
    total: int | None
    present: list[int]
    missing: list[int]
    duplicates: list[int]
    sources: list[str]  # video IDs the tracks were made from
    found: dict[int, tuple[str, str]] = field(default_factory=dict)  # track no. -> (url, title)
    playlist: str | None = None


def find_gaps(album: Path, tracks: list[TrackInfo]) -> Gap | None:
    nums = [t.number for t in tracks if t.number]
    if not nums:
        return None
    total = common(t.total for t in tracks)
    upto = max(total or 0, max(nums))
    present = sorted(set(nums))
    missing = [n for n in range(1, upto + 1) if n not in present]
    dups = sorted({n for n in nums if nums.count(n) > 1})
    sources = list(dict.fromkeys(t.video_id for t in tracks if t.video_id))
    return Gap(album, total, present, missing, dups, sources)


def match_playlists(gaps: list[Gap], urls: list[str]) -> None:
    """Resolve each playlist (cheap flat listing) and give each gap the links of its missing track numbers."""
    by_video: dict[str, tuple[str | None, str, list[dict]]] = {}
    for u in urls:
        try:
            with console.status(f"resolving {u} …"):
                title, entries = list_entries(u)
        except Exception as e:
            if is_bot_check(e):
                raise BotCheckError(str(e)) from e
            console.print(f"[yellow]can't resolve {u}: {e}[/]")
            continue
        if title is None:
            continue
        lst = playlist_id(u) or ""
        for e in entries:
            if e.get("id"):
                by_video[e["id"]] = (title, lst, entries)
    for g in gaps:
        hit = next((by_video[v] for v in g.sources if v in by_video), None)
        if not hit:
            continue
        title, lst, entries = hit
        g.playlist = title
        by_index = {e.get("playlist_index"): e for e in entries}
        for n in g.missing:
            if e := by_index.get(n):
                url = video_in_playlist_url(e["id"], lst) if lst else watch_url(e["id"])
                g.found[n] = (url, e.get("title") or entry_url(e))


def show(root: Path, gaps: list[Gap]) -> None:
    tbl = Table(show_edge=False, header_style="bold magenta", title="Albums with missing tracks", title_justify="left")
    tbl.add_column("Album")
    tbl.add_column("Have", justify="right")
    tbl.add_column("Missing")
    tbl.add_column("Source")
    for g in gaps:
        have = f"{len(g.present)}/{g.total or max(g.present + g.missing)}"
        miss = ranges(g.missing) + (f"  [dim](twice: {ranges(g.duplicates)})[/]" if g.duplicates else "")
        if g.found:
            src = f"playlist '{g.playlist}': {len(g.found)} of {len(g.missing)} found"
        elif len(g.sources) == 1 and len(g.present) > 1:
            src = f"one video: {watch_url(g.sources[0])}"
        elif g.sources:
            src = f"[dim]{len(g.sources)} videos{', no matching playlist' if g.playlist is None else ''}[/]"
        else:
            src = "[dim]unknown[/]"
        tbl.add_row(rel(g.album, root), have, miss, src)
    console.print(tbl)
    for g in gaps:
        for n, (url, title) in sorted(g.found.items()):
            console.print(f"  [dim]{rel(g.album, root)}[/] #{n} {title}  {url}")


def save(path: Path, root: Path, gaps: list[Gap]) -> int:
    lines, n = [f"# missing tracks in {root} - yt-audio-slicer -f {path}", ""], 0
    for g in gaps:
        lines.append(f"# {rel(g.album, root)} - missing {ranges(g.missing)}")
        for num, (url, title) in sorted(g.found.items()):
            lines.append(f"# #{num} {title}")
            lines.append(url)
            n += 1
        if not g.found and len(g.sources) == 1 and len(g.present) > 1:
            lines.append("# all tracks were cut from one video; re-run it with --force to restore them:")
            lines.append(f"# {watch_url(g.sources[0])}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return n


def build_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "dirs",
        nargs="*",
        type=Path,
        default=[Path("output")],
        metavar="DIR",
        help="library or album folder(s) to check (default: ./output)",
    )
    p.add_argument(
        "-u",
        "--urls",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="URL file (like for -f) whose playlists are searched for the missing tracks; can be repeated",
    )
    p.add_argument(
        "-s",
        "--save",
        type=Path,
        metavar="FILE",
        help="write the result to FILE; found tracks as URLs that 'yt-audio-slicer -f FILE' downloads",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=list(DEFAULT_EXCLUDE),
        metavar="PATTERN",
        help="skip folders matching this name pattern (default: '.*' and '_*')",
    )
    add_auth_args(p)
    return p


def main(argv: list[str] | None = None, prog: str = "yt-audio-slicer missing") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    set_auth(args)
    gaps: list[Gap] = []
    roots = []
    for d in args.dirs:
        if not d.is_dir():
            parser.error(f"not a folder: {d}")
        with console.status(f"reading tags in {d} …"):
            albums = album_dirs(d, args.exclude)
        roots.append(d)
        gaps += [g for a, tracks in albums.items() if (g := find_gaps(a, tracks)) and g.missing]
    root = roots[0] if len(roots) == 1 else Path(".")
    if not gaps:
        console.print("[green]no missing tracks[/]")
        return 0
    if args.urls:
        urls = [u for f in args.urls for line in read_url_file(f).lines for u in urls_in_line(line)]
        try:
            match_playlists(gaps, urls)
        except BotCheckError as e:
            console.print(
                f"[red]YouTube wants you to sign in ({str(e).splitlines()[0]}); try --cookies-from-browser BROWSER[/]"
            )
    show(root, gaps)
    if args.save:
        n = save(args.save, root, gaps)
        console.print(
            f"[green]written {args.save}[/] ({n} downloadable track(s))"
            + (f" - fill the gaps with: [bold]yt-audio-slicer -f {args.save}[/]" if n else "")
        )
    return 0
