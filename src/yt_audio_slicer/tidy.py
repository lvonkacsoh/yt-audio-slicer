"""Merge scattered albums back together and bring folder names in line with the current naming scheme.

  Epica, Roy Khan/Consign To Oblivion   ->  Epica/Consign To Oblivion
  EPICA/Design Your Universe            ->  Epica/Design Your Universe
  Eisbrecher/Eisbrecher - Liebe Macht Monster (FULL ALBUM)  +  Eisbrecher/Liebe Macht Monster (2021)
                                        ->  Eisbrecher/2021 - Liebe Macht Monster
  Falsches Spiel (Run For A Fall/We Will Take You With Us  (track 10/12, a mis-read title)
                                        ->  Epica/We Will Take You With Us   ('orphan', see --no-orphans)

Albums match when the main artist (the first name of 'A, B' / 'A feat. B', any case) and the album name (without
year, release type, 'Full Album' and the like) are the same. A track that is already in the target album (same track
number and title) is a duplicate: it is left where it is and listed, or with --duplicates park moved to
OUTPUT/.yt-audio-slicer/duplicates/ (same relative path) so the album folder can go. Empty folders are removed
afterwards.

Nothing is changed unless --yes is given; without it the planned changes are listed (dry run). With --yes, every
change is logged to OUTPUT/.yt-audio-slicer/tidy-<time>.log."""

from __future__ import annotations

import argparse
import os
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TPE1, TPE2

from .library import AUDIO_EXT, DEFAULT_EXCLUDE, TrackInfo, common, excluded, natural_key, read_track
from .naming import (
    SMALL_WORDS,
    add_artist_case_args,
    add_featured,
    album_folder,
    album_key,
    artist_folder,
    artist_key,
    fold,
    is_all_caps,
    parse_album_folder,
    primary_artist,
    split_artists,
)
from .prune import empty_dirs, remove, tree_roots
from .ui import console, rel


@dataclass
class AlbumDir:
    path: Path
    artist_name: str  # name of the artist folder
    tracks: list[TrackInfo]
    year: str | None
    name: str
    rtype: str | None
    new_format: bool
    orphan_note: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return artist_key(self.artist_name), album_key(self.name)

    @property
    def total(self) -> int | None:
        return common(t.total for t in self.tracks)

    @property
    def numbers(self) -> set[int]:
        return {t.number for t in self.tracks if t.number}


@dataclass
class Move:
    src: Path
    dst: Path


@dataclass
class TagFix:
    path: Path
    changes: dict[str, tuple[str, str]]  # frame -> (old, new)


@dataclass
class Plan:
    target: Path
    members: list[AlbumDir]
    moves: list[Move] = field(default_factory=list)
    duplicates: list[tuple[Path, Path]] = field(default_factory=list)  # (left in place, same track as)
    clashes: list[tuple[Path, Path]] = field(default_factory=list)  # same number, different title
    left: list[tuple[Path, str]] = field(default_factory=list)  # other files not moved, why
    tag_fixes: list[TagFix] = field(default_factory=list)
    display_artist: str = ""


def track_id(t: TrackInfo) -> tuple:
    title = fold(t.title or "")
    if not title:  # untagged: '04 - Teufel.mp3' / '04 Stahlmann - Teufel.mp3' -> 'teufel'
        stem = t.path.stem.split(" - ")[-1]
        title = fold(stem.lstrip("0123456789 .-"))
    return t.disc or 1, t.number, title


# ---------------------------------------------------------------- scan ----


def scan(root: Path, exclude) -> tuple[list[AlbumDir], list[Path]]:
    """Album folders (<root>/<artist>/<album>) and loose audio files directly in an artist folder."""
    albums, loose = [], []
    for artist in sorted(
        (d for d in root.iterdir() if d.is_dir() and not excluded(d.name, exclude)), key=lambda p: natural_key(p.name)
    ):
        loose += [f for f in artist.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXT]
        for album in sorted(
            (d for d in artist.iterdir() if d.is_dir() and not excluded(d.name, exclude)),
            key=lambda p: natural_key(p.name),
        ):
            tracks = [
                read_track(f)
                for f in sorted(album.iterdir(), key=natural_key)
                if f.is_file() and f.suffix.lower() in AUDIO_EXT
            ]
            year, name, rtype, new = parse_album_folder(album.name, artist.name)
            year = year or common(t.year for t in tracks)
            albums.append(AlbumDir(album, artist.name, tracks, year, name, rtype, new))
    return albums, loose


# ---------------------------------------------------------------- plan ----


def group_albums(albums: list[AlbumDir], orphans: bool) -> list[list[AlbumDir]]:
    groups: dict[tuple[str, str], list[AlbumDir]] = {}
    for a in albums:
        groups.setdefault(a.key, []).append(a)
    if orphans:
        merge_orphans(groups, albums)
    return list(groups.values())


def merge_orphans(groups: dict[tuple[str, str], list[AlbumDir]], albums: list[AlbumDir]) -> None:
    """An artist folder with a single album whose tracks fit into the gaps of the same-named album of another artist
    (same track total, no track number taken) is most likely a mis-read artist ('Falsches Spiel (Run For A Fall')."""
    per_artist: dict[str, int] = {}
    for a in albums:
        if a.tracks:
            per_artist[a.key[0]] = per_artist.get(a.key[0], 0) + 1
    for key in list(groups):
        g = groups.get(key)
        if not g or per_artist.get(key[0]) != 1:
            continue
        mine = [a for a in g if a.tracks]
        if len(mine) != 1 or not mine[0].total:
            continue
        a = mine[0]
        hosts = []
        for other_key, other in groups.items():
            if other_key == key or other_key[1] != key[1]:
                continue
            tracks = [t for o in other for t in o.tracks]
            total = common(t.total for t in tracks)
            nums = {t.number for t in tracks if t.number}
            if total == a.total and len(tracks) > len(a.tracks) and not (nums & a.numbers):
                hosts.append(other_key)
        if len(hosts) == 1:
            a.orphan_note = (
                f"orphan: same album and track total ({a.total}), fills track(s) "
                f"{', '.join(map(str, sorted(a.numbers)))}"
            )
            groups[hosts[0]] += groups.pop(key)


def pick_artist(main: list[AlbumDir]) -> str:
    """The artist name variant to use: not ALL CAPS if possible, else the one with most tracks."""
    weights: dict[str, int] = {}
    for a in main:
        names = [primary_artist(a.artist_name)] + [primary_artist(t.album_artist) for t in a.tracks if t.album_artist]
        for n in names:
            weights[n] = weights.get(n, 0) + max(1, len(a.tracks))
    return sorted(weights, key=lambda n: (is_all_caps(n), -weights[n], len(n)))[0]


def pick_album(main: list[AlbumDir]) -> tuple[str | None, str, str | None]:
    year = next((a.year for a in main if a.new_format and a.year), None) or next((a.year for a in main if a.year), None)
    best = sorted(main, key=lambda a: (not a.new_format, is_all_caps(a.name), -len(a.tracks), len(a.name)))[0]
    rtype = best.rtype or next((a.rtype for a in main if a.rtype), None)
    return year, best.name, rtype


def plan_group(
    root: Path, members: list[AlbumDir], case: str, fix_tags: bool, small_words: frozenset[str] = SMALL_WORDS
) -> Plan | None:
    main = [a for a in members if not a.orphan_note]
    display = pick_artist(main)
    year, name, rtype = pick_album(main)
    target = root / artist_folder(display, case, small_words) / album_folder(name, year, rtype)
    plan = Plan(target, members, display_artist=display)

    # files already in the target folder stay; everything else is moved in, biggest folder first
    order = sorted(members, key=lambda a: (a.path != target, a.orphan_note is not None, -len(a.tracks)))
    taken: dict[str, Path] = {}  # file names (casefolded) in the target
    have: dict[tuple, Path] = {}  # track ids in the target
    numbers: dict[tuple, Path] = {}  # (disc, number) in the target
    if target.is_dir():
        for f in target.iterdir():
            taken[f.name.casefold()] = f
    for a in order:
        tracks = {t.path: t for t in a.tracks}
        here = a.path == target or (target.is_dir() and os.path.samefile(a.path, target))
        for f in sorted(a.path.iterdir(), key=natural_key):
            t = tracks.get(f)
            if t is not None and t.number is not None:
                tid = track_id(t)
                if tid in have and not here:
                    plan.duplicates.append((f, have[tid]))
                    continue
                if (tid[0], tid[1]) in numbers and not here:
                    plan.clashes.append((f, numbers[(tid[0], tid[1])]))
                have.setdefault(tid, f if here else target / f.name)
                numbers.setdefault((tid[0], tid[1]), f if here else target / f.name)
            if here:
                continue
            dst = target / f.name
            if f.name.casefold() in taken:
                if t is None:
                    plan.left.append((f, "a file with that name is already in the target"))
                    continue
                n = 2
                while (dst := target / f"{f.stem} ({n}){f.suffix}").name.casefold() in taken:
                    n += 1
            taken[dst.name.casefold()] = dst
            plan.moves.append(Move(f, dst))
            if t is not None:
                have[track_id(t)] = dst

    if fix_tags:
        moved = {m.src: m.dst for m in plan.moves}
        dup_src = {d for d, _ in plan.duplicates}
        tracks = [t for a in members for t in a.tracks if t.path not in dup_src]
        # a name credited on at least half of the tracks is a band member / writer credit, not a featured artist
        counts = Counter(fold(o) for t in tracks for o in set(split_artists(t.artist)[1:]))
        credits = {k for k, c in counts.items() if len(tracks) >= 3 and c * 2 >= len(tracks)}
        for t in tracks:
            if t.path.suffix.lower() == ".mp3" and (fix := tag_fix(t, display, credits)) is not None:
                fix.path = moved.get(t.path, t.path)
                plan.tag_fixes.append(fix)

    stays = all(a.path == target or not any(a.path.iterdir()) for a in members)
    if stays and not plan.tag_fixes and not plan.duplicates:
        return None
    return plan


def tag_fix(t: TrackInfo, display: str, credits: set[str] = frozenset()) -> TagFix | None:
    """Album artist -> the chosen name variant; track artist 'A, B' / 'A feat. B' -> 'A', title + ' feat. B'.
    Names in `credits` (on most tracks of the album) are dropped instead of added as 'feat.'."""
    changes: dict[str, tuple[str, str]] = {}
    if t.album_artist and t.album_artist != display and fold(primary_artist(t.album_artist)) == fold(display):
        changes["TPE2"] = (t.album_artist, display)
    if t.artist:
        parts = split_artists(t.artist)
        first = display if fold(parts[0]) == fold(display) else parts[0]
        if first != t.artist:
            changes["TPE1"] = (t.artist, first)
        others = [o for o in parts[1:] if fold(o) not in credits]
        if others and t.title:
            new_title = add_featured(t.title, others)
            if new_title != t.title:
                changes["TIT2"] = (t.title, new_title)
    return TagFix(t.path, changes) if changes else None


# --------------------------------------------------------------- apply ----


def move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not (src.exists() and os.path.samefile(src, dst)):
        raise FileExistsError(f"{dst} exists")
    if src.exists() and dst.exists():  # case-only rename on a case-insensitive file system
        tmp = src.with_name(f".{src.name}.tidy-tmp")
        src.rename(tmp)
        src = tmp
    shutil.move(str(src), str(dst))


def write_tags(fix: TagFix) -> None:
    tags = ID3(fix.path)
    frames = {"TPE1": TPE1, "TPE2": TPE2, "TIT2": TIT2}
    for k, (_, new) in fix.changes.items():
        tags.setall(k, [frames[k](encoding=3, text=new)])
    tags.save(fix.path, v2_version=3)


# -------------------------------------------------------------- output ----


def park_path(root: Path, f: Path) -> Path:
    return root / ".yt-audio-slicer" / "duplicates" / f.relative_to(root)


def show_plan(root: Path, p: Plan, park: bool = False) -> None:
    new = "" if p.target.is_dir() else "  [green](new)[/]"
    console.print(f"[bold cyan]{rel(p.target, root)}[/]{new}")
    for a in p.members:
        n = sum(1 for m in p.moves if m.src.parent == a.path)
        if a.path == p.target or not n:
            continue
        note = f"  [yellow]{a.orphan_note}[/]" if a.orphan_note else ""
        console.print(f"  ← {rel(a.path, root)}  [dim]{n} file(s)[/]{note}")
    for m in p.moves:
        if m.dst.name != m.src.name:
            console.print(f"    [yellow]renamed[/] {m.src.name} -> {m.dst.name}  [dim](name taken)[/]")
    for d, same in p.duplicates:
        what = "duplicate, parked:" if park else "duplicate, left in place:"
        console.print(f"    [magenta]{what}[/] {rel(d, root)}  [dim]= {rel(same, root)}[/]")
    for c, other in p.clashes:
        console.print(f"    [yellow]same track number as {other.name}:[/] {c.name}")
    for f, why in p.left:
        console.print(f"    [dim]left in place: {rel(f, root)} ({why})[/]")
    for fix in p.tag_fixes:
        ch = ", ".join(f"{k} '{a}' -> '{b}'" for k, (a, b) in fix.changes.items())
        console.print(f"    [blue]tags[/] {fix.path.name}: {ch}")


def build_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("root", nargs="?", type=Path, default=Path("output"), help="library folder (default: ./output)")
    p.add_argument("-y", "--yes", action="store_true", help="apply the changes (default: only list them)")
    add_artist_case_args(p)
    p.add_argument(
        "--fix-tags",
        action="store_true",
        help="also rewrite tags: album artist to one spelling, 'A, B' track artists to 'A' with "
        "' feat. B' added to the title",
    )
    p.add_argument(
        "--duplicates",
        choices=("leave", "park"),
        default="leave",
        help="tracks the target album already has: 'leave' them where they are (default) or 'park' them "
        "in OUTPUT/.yt-audio-slicer/duplicates/",
    )
    p.add_argument(
        "--no-orphans",
        dest="orphans",
        action="store_false",
        help="don't merge single albums of a different artist name into a matching album",
    )
    p.add_argument("--no-prune", dest="prune", action="store_false", help="keep empty folders")
    p.add_argument(
        "--exclude",
        action="append",
        default=list(DEFAULT_EXCLUDE),
        metavar="PATTERN",
        help="skip artist / album folders matching this name pattern (default: '.*' and '_*')",
    )
    return p


def main(argv: list[str] | None = None, prog: str = "yt-audio-slicer tidy") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    root: Path = args.root
    if not root.is_dir():
        parser.error(f"not a folder: {root}")
    with console.status(f"reading {root} …"):
        albums, loose = scan(root, args.exclude)
    plans = [
        p
        for g in group_albums(albums, args.orphans)
        if (p := plan_group(root, g, args.artist_case, args.fix_tags, args.small_words)) is not None
    ]
    plans.sort(key=lambda p: natural_key(rel(p.target, root)))

    park = args.duplicates == "park"
    for p in plans:
        show_plan(root, p, park)
    for f in loose:
        console.print(f"[yellow]not in an album folder, left alone:[/] {rel(f, root)}")

    moves = [m for p in plans for m in p.moves]
    if park:
        moves += [Move(d, park_path(root, d)) for p in plans for d, _ in p.duplicates]
    gone = frozenset(m.src for m in moves)
    keep = frozenset(d for m in moves for d in m.dst.parents)
    to_prune = empty_dirs(root, args.exclude, gone=gone, keep=keep) if args.prune else []
    if to_prune:
        roots = tree_roots(to_prune)
        console.print(f"[bold]{'Removing' if args.yes else 'Would remove'} {len(to_prune)} empty folder(s):[/]")
        for r in roots:
            console.print(f"  [dim]{rel(r, root)}[/]")

    n_dup = sum(len(p.duplicates) for p in plans)
    n_tags = sum(len(p.tag_fixes) for p in plans)
    n_moves = len(moves) - (n_dup if park else 0)
    summary = (
        f"{len(plans)} album folder(s) to merge / rename, {n_moves} file(s) to move, "
        f"{n_dup} duplicate(s) {'parked' if park else 'left in place'}, {n_tags} file(s) with tag fixes, "
        f"{len(to_prune)} empty folder(s)"
    )
    if not args.yes:
        console.print(f"\n[bold]{summary}[/]")
        if plans or to_prune:
            console.print("[dim]dry run - nothing changed. Run again with --yes to apply.[/]")
        return 0

    log_dir = root / ".yt-audio-slicer"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"tidy-{datetime.now():%Y%m%d-%H%M%S}.log"
    errors = 0
    with log_path.open("w", encoding="utf-8") as log:
        for p in plans:
            dups = [Move(d, park_path(root, d)) for d, _ in p.duplicates] if park else []
            for m in p.moves + dups:
                try:
                    move(m.src, m.dst)
                    log.write(f"MOVE\t{m.src}\t{m.dst}\n")
                except OSError as e:
                    errors += 1
                    console.print(f"[red]could not move {m.src}: {e}[/]")
                    log.write(f"ERROR\t{m.src}\t{e}\n")
            for fix in p.tag_fixes:
                try:
                    write_tags(fix)
                    log.write(f"TAGS\t{fix.path}\t{fix.changes}\n")
                except Exception as e:
                    errors += 1
                    console.print(f"[red]could not write tags of {fix.path}: {e}[/]")
        if args.prune:
            dirs = empty_dirs(root, args.exclude)
            remove(dirs)
            for d in dirs:
                log.write(f"RMDIR\t{d}\n")
    console.print(f"\n[bold green]done:[/] {summary}\n[dim]log: {log_path}[/]")
    return 1 if errors else 0
