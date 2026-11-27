"""Remove empty folders, including folder trees that contain nothing but empty folders."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .library import excluded
from .ui import console


def empty_dirs(root: Path, exclude=(), gone=frozenset(), keep=frozenset()) -> list[Path]:
    """Every folder below root (not root itself) without any file in it or below it, deepest first.
    Folders whose name matches an exclude pattern, and everything below them, are left alone; so are `keep`.
    `gone` are paths treated as already moved away (used to preview the result of a plan)."""
    empty: set[Path] = set()
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        d = Path(dirpath)
        if d == root or d in gone or any(excluded(part, exclude) for part in d.relative_to(root).parts):
            continue
        no_files = all((d / f) in gone for f in filenames)
        if no_files and d not in keep and all((d / sub) in empty or (d / sub) in gone for sub in dirnames):
            empty.add(d)
            out.append(d)
    return out


def tree_roots(dirs: list[Path]) -> list[Path]:
    """Only the top folder of each empty tree, for display."""
    s = set(dirs)
    return sorted((d for d in dirs if d.parent not in s), key=lambda p: str(p).casefold())


def remove(dirs: list[Path]) -> int:
    n = 0
    for d in dirs:  # deepest first
        try:
            d.rmdir()
            n += 1
        except OSError as e:
            console.print(f"[yellow]could not remove {d}: {e.strerror or e}[/]")
    return n


def show(root: Path, dirs: list[Path], applied: bool) -> None:
    roots = tree_roots(dirs)
    verb = "Removed" if applied else "Would remove"
    console.print(f"[bold]{verb} {len(dirs)} empty folder(s)[/] in {len(roots)} tree(s):")
    for r in roots:
        inner = sum(1 for d in dirs if d != r and r in d.parents)
        console.print(f"  {r.relative_to(root)}" + (f"  [dim](+{inner} inside)[/]" if inner else ""))


def build_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description=__doc__ + " Lists what would be removed unless --yes is given.")
    p.add_argument(
        "dirs",
        nargs="*",
        type=Path,
        default=[Path("output")],
        metavar="DIR",
        help="folder(s) to clean up (default: ./output); the folders themselves are never removed",
    )
    p.add_argument("-y", "--yes", action="store_true", help="really remove the folders (default: dry run)")
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="leave folders matching this name pattern alone (can be repeated)",
    )
    return p


def main(argv: list[str] | None = None, prog: str = "yt-audio-slicer prune") -> int:
    args = build_parser(prog).parse_args(argv)
    status, found = 0, False
    for root in args.dirs:
        if not root.is_dir():
            console.print(f"[red]not a folder: {root}[/]")
            status = 2
            continue
        dirs = empty_dirs(root, args.exclude)
        if not dirs:
            console.print(f"[green]{root}: no empty folders[/]")
            continue
        found = True
        if args.yes:
            remove(dirs)
        show(root, dirs, args.yes)
    if found and not args.yes:
        console.print("[dim]dry run - nothing removed. Run again with --yes to remove them.[/]")
    return status
