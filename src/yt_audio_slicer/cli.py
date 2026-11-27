"""yt-audio-slicer: download YouTube videos / playlists as tagged MP3 tracks, and keep the library tidy."""

from __future__ import annotations

import sys
from importlib import import_module

from . import __version__

PROG = "yt-audio-slicer"
# command -> (module, one-line help)
COMMANDS = {
    "download": ("download", "download videos / playlists and split them into tracks (the default command)"),
    "tidy": ("tidy", "merge scattered albums, fix folder names, remove empty folders (dry run by default)"),
    "missing": ("missing", "list tracks missing from albums, optionally find them in playlists"),
    "join": ("join", "join audio files back into one, keeping their tags"),
    "prune": ("prune", "remove empty folders (dry run by default)"),
}

HELP = (
    f"""usage: {PROG} [download] [options] URL ...
       {PROG} COMMAND [options]

Download YouTube videos or playlists, split them into tracks and write tagged MP3 files.

commands:
"""
    + "\n".join(f"  {name:<10} {text}" for name, (_, text) in COMMANDS.items())
    + f"""

Without a command, '{PROG} URL ...' runs 'download'.
'{PROG} COMMAND --help' shows the options of a command.
"""
)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in ("-h", "--help", "help") and len(argv) == 1:
        print(HELP)
        return 0
    if argv and argv[0] == "--version":
        print(f"{PROG} {__version__}")
        return 0
    explicit = bool(argv) and argv[0] in COMMANDS
    name = argv.pop(0) if explicit else "download"
    module = import_module(f".{COMMANDS[name][0]}", __package__)
    prog = f"{PROG} {name}" if explicit else PROG
    try:
        return module.main(argv, prog=prog)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
