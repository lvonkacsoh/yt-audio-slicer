"""Join audio files back into one file (e.g. a song that was split in two), keeping their tags.

Inputs are files, glob patterns ('Album/0[2-3]*.mp3', quoted so the shell leaves them alone) or folders (all audio
files in it). Files from a pattern or folder are joined in natural filename order; files named one by one are joined
in the order given. Tags are inherited from the sources: a tag that differs between them is reported, and the value
of the first source is used (most common value for album-wide tags)."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TRCK

from .library import AUDIO_EXT, natural_key
from .ui import console, fmt_time

# frames that are naturally different for every track: the first source's value is used, no warning
PER_TRACK = {
    "TIT2",
    "TRCK",
    "TLEN",
    "TSRC",
    "UFID",
    "TXXX:MusicBrainz Release Track Id",
    "TXXX:Acoustid Id",
    "TXXX:MusicBrainz Track Id",
    "WOAS",
    "COMM::eng",
}
DROP = {"TLEN"}  # wrong after joining
# output extension -> the codec a stream copy needs, otherwise re-encode
OUT_CODECS = {".mp3": "mp3", ".flac": "flac", ".opus": "opus", ".ogg": "vorbis", ".m4a": "aac"}


def resolve_inputs(specs: list[str]) -> list[Path]:
    out: list[Path] = []
    for s in specs:
        p = Path(s)
        if p.is_dir():
            files = sorted((f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXT and f.is_file()), key=natural_key)
            if not files:
                raise SystemExit(f"error: no audio files in folder {p}")
        elif glob.has_magic(s):
            files = sorted((Path(f) for f in glob.glob(s) if Path(f).suffix.lower() in AUDIO_EXT), key=natural_key)
            if not files:
                raise SystemExit(f"error: pattern matched no audio files: {s}")
        elif p.is_file():
            files = [p]
        else:
            raise SystemExit(f"error: no such file or folder: {s}")
        out += [f for f in files if f not in out]
    return out


def probe(path: Path) -> dict:
    p = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        d = json.loads(p.stdout)
        s = d["streams"][0]
        return {
            "codec": s.get("codec_name"),
            "rate": s.get("sample_rate"),
            "channels": s.get("channels"),
            "duration": float(d.get("format", {}).get("duration") or 0),
        }
    except (ValueError, KeyError, IndexError):
        raise SystemExit(f"error: can't read audio stream of {path}") from None


def concat(inputs: list[Path], output: Path, reencode: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lst = Path(tmp) / "list.txt"
        lst.write_text(
            "".join("file '{}'\n".format(str(p.resolve()).replace("'", "'\\''")) for p in inputs), encoding="utf-8"
        )
        codec = (
            (["-c:a", "libmp3lame", "-q:a", "0"] if output.suffix.lower() == ".mp3" else [])
            if reencode
            else ["-c", "copy"]
        )
        tmp_out = output.with_name(f".{output.stem}.joining{output.suffix}")
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-map",
            "0:a",
            "-map_metadata",
            "-1",
            *codec,
            str(tmp_out),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            tmp_out.unlink(missing_ok=True)
            raise SystemExit(f"error: ffmpeg failed:\n{p.stderr.strip()}")
        tmp_out.replace(output)


def _value(frame) -> str:
    if frame.FrameID == "APIC":
        return f"<image {len(frame.data)} bytes, sha1 {hashlib.sha1(frame.data).hexdigest()[:8]}>"
    if hasattr(frame, "url"):
        return frame.url
    return str(frame)


def merge_tags(inputs: list[Path]) -> tuple[ID3 | None, list[str], list[str]]:
    """(merged tags, warnings, notes). Album-wide frames that differ -> most common value + warning;
    per-track frames (title, track number, ...) -> first source's value + note."""
    sources: list[ID3 | None] = []
    for p in inputs:
        try:
            sources.append(ID3(p))
        except Exception:  # no / broken ID3 header
            sources.append(None)
    tagged = [(p, t) for p, t in zip(inputs, sources, strict=True) if t is not None]
    if not tagged:
        return None, ["no source file has ID3 tags - the output stays untagged"], []
    warnings, notes = [], []
    keys = list(dict.fromkeys(k for _, t in tagged for k in t))
    merged = ID3()
    for k in keys:
        if k.split(":")[0] in DROP:
            continue
        frames = [(p, t[k]) for p, t in tagged if k in t]
        values = [_value(f) for _, f in frames]
        distinct = list(dict.fromkeys(values))
        missing = [p.name for p, t in tagged if k not in t]
        if len(distinct) == 1:
            merged.add(frames[0][1])
            if missing:
                notes.append(f"{k}: '{distinct[0]}' (missing in {len(missing)} source(s))")
            continue
        per_track = k in PER_TRACK or k.split(":")[0] in PER_TRACK
        if per_track:
            pick = 0
        else:
            best = Counter(values).most_common(1)[0][0]
            pick = values.index(best)
        merged.add(frames[pick][1])
        detail = "; ".join(f"{p.name}: '{v}'" for (p, _), v in zip(frames, values, strict=True))
        msg = f"{k}: sources differ, using '{values[pick]}' - {detail}"
        (notes if per_track else warnings).append(msg)
    return merged, warnings, notes


def build_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", metavar="INPUT", help="audio files, glob patterns or a folder")
    p.add_argument("-o", "--output", type=Path, required=True, help="the joined file to write")
    p.add_argument("--title", help="title tag of the joined file (default: the first source's title)")
    p.add_argument(
        "--track",
        metavar="N[/TOTAL]",
        help="track number of the joined file, e.g. 2 or 2/14 (default: the first source's)",
    )
    p.add_argument(
        "--reencode",
        action="store_true",
        help="re-encode instead of copying the audio stream (automatic when the sources differ in "
        "codec, sample rate or channels, or the output format differs)",
    )
    p.add_argument("--log", type=Path, metavar="FILE", help="also write all tag conflicts and notes to FILE")
    p.add_argument("--delete-sources", action="store_true", help="delete the source files after a successful join")
    p.add_argument("--force", action="store_true", help="overwrite OUTPUT if it exists")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would be joined and the resulting tags")
    return p


def main(argv: list[str] | None = None, prog: str = "yt-audio-slicer join") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    inputs = resolve_inputs(args.inputs)
    out = args.output
    if len(inputs) < 2:
        parser.error(f"need at least two files to join, got {len(inputs)}")
    if any(out.resolve() == p.resolve() for p in inputs):
        parser.error("the output file is one of the inputs")
    if out.exists() and not args.force and not args.dry_run:
        parser.error(f"{out} exists (use --force to overwrite)")
    if out.suffix.lower() not in AUDIO_EXT:
        parser.error(f"unsupported output extension '{out.suffix}' (use one of {', '.join(sorted(AUDIO_EXT))})")

    infos = [probe(p) for p in inputs]
    formats = {(i["codec"], i["rate"], i["channels"]) for i in infos}
    out_codec = OUT_CODECS.get(out.suffix.lower())
    reencode = args.reencode or len(formats) > 1 or (out_codec is not None and next(iter(formats))[0] != out_codec)

    tags, warnings, notes = merge_tags(inputs) if out.suffix.lower() == ".mp3" else (None, [], [])
    if out.suffix.lower() != ".mp3":
        warnings.append("tags are only carried over for .mp3 output")
    if tags is not None:
        if args.title:
            tags.setall("TIT2", [TIT2(encoding=3, text=args.title)])
        if args.track:
            num, _, total = args.track.partition("/")
            if not num.isdigit() or (total and not total.isdigit()):
                parser.error(f"--track: expected N or N/TOTAL, got '{args.track}'")
            if not total and "TRCK" in tags:
                total = str(tags["TRCK"]).partition("/")[2]
            tags.setall("TRCK", [TRCK(encoding=3, text=f"{num}/{total}" if total else num)])

    total = sum(i["duration"] for i in infos)
    console.print(
        f"[bold]Joining {len(inputs)} files[/] -> {out}  "
        f"[dim]({fmt_time(total)}, {'re-encode' if reencode else 'stream copy'})[/]"
    )
    for p, i in zip(inputs, infos, strict=True):
        console.print(f"  {p}  [dim]{fmt_time(i['duration'])}[/]")
    if tags is not None:
        title = str(tags["TIT2"]) if "TIT2" in tags else "-"
        console.print(f"  [bold]title[/] {title}   [bold]track[/] {tags.get('TRCK', '-')}")
    for w in warnings:
        console.print(f"  [yellow]warning:[/] {w}")
    for n in notes:
        console.print(f"  [dim]note: {n}[/]")
    if args.log:
        stamp = datetime.now().isoformat(timespec="seconds")
        lines = [
            f"# {stamp} join -> {out}",
            *(f"input: {p}" for p in inputs),
            *(f"WARNING {w}" for w in warnings),
            *(f"NOTE {n}" for n in notes),
        ]
        with args.log.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
        console.print(f"  [dim]log written to {args.log}[/]")
    if args.dry_run:
        console.print("[dim]dry run - nothing written[/]")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    concat(inputs, out, reencode)
    if tags is not None:
        tags.save(out, v2_version=3)
    console.print(f"[green]written {out}[/]")
    if args.delete_sources:
        for p in inputs:
            p.unlink()
        console.print(f"[dim]deleted {len(inputs)} source file(s)[/]")
    return 0
