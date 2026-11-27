"""Download YouTube videos / playlists and split them into tagged MP3 tracks."""

from __future__ import annotations

import argparse
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.table import Table

from . import markers as mk
from .audio import Track, slice_tracks, write_tags
from .cover import Cover, choose_cover
from .library import DEFAULT_EXCLUDE, TrackInfo, library_index
from .meta import (
    PLAYLIST_PREFIX_RE,
    AlbumMeta,
    album_folder_for,
    guess_album,
    musicbrainz_lookup,
    playlist_album_meta,
    split_title,
    track_from_info,
)
from .naming import (
    add_artist_case_args,
    add_featured,
    album_key,
    artist_folder,
    artist_key,
    fold,
    parse_album_folder,
    split_artists,
    track_filename,
)
from .ui import console, fmt_bytes, fmt_time
from .urlfile import UrlFile, UrlProgress, parse_url_file, read_url_file, write_remaining
from .urls import is_plain_video_with_list, playlist_id, playlist_url, url_key, video_in_playlist, watch_url
from .yt import (
    YDL_AUTH,
    BotCheckError,
    add_auth_args,
    base_opts,
    entry_url,
    is_bot_check,
    is_forbidden,
    list_entries,
    set_auth,
    short_error,
)


@dataclass
class Result:
    url: str
    title: str
    meta: AlbumMeta | None = None
    marker_source: str = ""
    cover_source: str = ""
    cover_notes: list[str] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    download_bytes: int = 0
    duration: float = 0
    error: str | None = None
    out_dir: Path | None = None


@dataclass
class PlaylistContext:
    """Shared by all single-song videos of one playlist: the album metadata (one MusicBrainz lookup) and the cover
    are resolved once, from the first song, and reused for the rest, so every song lands in the same folder."""

    title: str | None
    playlist_id: str | None
    n_entries: int
    meta: AlbumMeta | None = None
    cover: Cover | None = None


# ---------------------------------------------------------------- yt-dlp ----


def fetch_comments(url: str, max_comments: int) -> list[dict]:
    opts = base_opts() | {
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": [str(max_comments), "all", "0", "0"], "comment_sort": ["top"]}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("comments") or []


def download_audio(url: str, work: Path, res: Result) -> tuple[dict, Path, Path | None]:
    work.mkdir(parents=True, exist_ok=True)
    progress = Progress(
        TextColumn("  [cyan]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )
    task = progress.add_task("downloading", total=None)

    def hook(d):
        if d["status"] == "downloading":
            progress.update(
                task,
                completed=d.get("downloaded_bytes", 0),
                total=d.get("total_bytes") or d.get("total_bytes_estimate"),
            )
        elif d["status"] == "finished":
            res.download_bytes += d.get("total_bytes") or d.get("downloaded_bytes") or 0
            progress.update(task, description="converting to mp3 …")

    opts = base_opts() | {
        "format": "bestaudio/best",
        "outtmpl": str(work / "%(id)s.%(ext)s"),
        "writethumbnail": True,
        "noplaylist": True,
        "progress_hooks": [hook],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
    }
    t0 = time.perf_counter()
    with progress, yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    res.timings["download+convert"] = time.perf_counter() - t0
    audio = work / f"{info['id']}.mp3"
    thumbs = sorted(work.glob(f"{info['id']}.jpg")) or sorted(
        p for p in work.glob(f"{info['id']}.*") if p.suffix.lower() in {".webp", ".png", ".jpeg"}
    )
    return info, audio, (thumbs[0] if thumbs else None)


def download_with_retry(url: str, work: Path, res: Result, retries: int) -> tuple[dict, Path, Path | None]:
    """HTTP 403 is usually a stale stream URL: start over with a fresh extraction a few times before giving up."""
    for attempt in range(retries + 1):
        try:
            return download_audio(url, work, res)
        except yt_dlp.utils.DownloadError as e:
            if not is_forbidden(e) or attempt == retries:
                raise
            wait = 3 * (attempt + 1)
            console.print(f"  [yellow]HTTP 403 - retrying in {wait}s ({attempt + 1}/{retries})[/]")
            shutil.rmtree(work, ignore_errors=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


# ------------------------------------------------------------- pipeline ----


def _short_type(rtype: str | None) -> str | None:
    """EP / Single are separate releases (the single 'Zeit' is not the album 'Zeit'); Live / studio are not."""
    return rtype if rtype in ("EP", "Single") else None


def existing_album_dir(output: Path, meta: AlbumMeta) -> Path | None:
    """An album folder that is already in the library for this artist + album (any case, with or without year,
    old naming scheme), so that a later download - a retry, a missing track - joins it instead of starting a second
    folder. 'yt-audio-slicer tidy' renames it to the current scheme."""
    if not output.is_dir():
        return None
    a_key, b_key = artist_key(meta.artist), album_key(meta.album)
    for artist in sorted(output.iterdir()):
        if not artist.is_dir() or artist.name.startswith((".", "_")) or artist_key(artist.name) != a_key:
            continue
        for album in sorted(artist.iterdir()):
            if not album.is_dir():
                continue
            _, name, rtype, _ = parse_album_folder(album.name, artist.name)
            if album_key(name) == b_key and _short_type(rtype) == _short_type(meta.release_type):
                return album
    return None


def credit_markers(found: mk.MarkerResult, album_artist: str) -> list[str]:
    """Per-track artists for the chapters of one video. Compilations have chapter titles like 'Artist - Song'.
    Only the first artist is kept as track artist; the others go to the title ('Song feat. B, C')."""
    splits = [split_title(m.title) for m in found.markers]
    compilation = all(a for a, _ in splits) and any(a.lower() != album_artist.lower() for a, _ in splits)
    artists = []
    for m, (a, title) in zip(found.markers, splits, strict=True):
        names = split_artists(a) if compilation else [album_artist]
        if compilation:
            m.title = title
        m.title = add_featured(m.title, names[1:])
        artists.append(names[0])
    return artists


def process_entry(entry: dict, ctx: PlaylistContext | None, args) -> Result:
    url = entry_url(entry)
    res = Result(url=url, title=entry.get("title") or url)
    work = args.output / ".work" / entry.get("id", "video")
    try:
        # 1. metadata + markers (no download yet, so --dry-run is cheap)
        t0 = time.perf_counter()
        with yt_dlp.YoutubeDL(base_opts() | {"noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        res.title, res.duration = info.get("title", url), info.get("duration") or 0
        found = mk.find_markers(info)
        if found.source == "none" and args.comments:
            with console.status("  no chapters / description markers - scanning top comments …"):
                found = mk.find_markers(info, fetch_comments(url, args.max_comments))
        res.timings["metadata"] = time.perf_counter() - t0

        # single song inside a playlist -> one track; the album comes from the playlist, resolved once
        single_in_playlist = found.source == "none" and ctx is not None
        if single_in_playlist:
            song, artists = track_from_info(info)
            if ctx.meta is None:
                ctx.meta = playlist_album_meta(info, ctx.title, artists)
                if args.musicbrainz:
                    t0 = time.perf_counter()
                    ctx.meta = musicbrainz_lookup(ctx.meta)
                    res.timings["musicbrainz (album)"] = time.perf_counter() - t0
            meta = ctx.meta
            found = mk.MarkerResult([mk.Marker(0, add_featured(song, artists[1:]), None)], "playlist entry")
            # 'EPICA' on YouTube, 'Epica' on MusicBrainz -> one spelling
            track_artists = [meta.artist if fold(artists[0]) == fold(meta.artist) else artists[0]]
        else:
            meta = guess_album(info)
            if args.musicbrainz:
                t0 = time.perf_counter()
                meta = musicbrainz_lookup(meta)
                res.timings["musicbrainz"] = time.perf_counter() - t0
            track_artists = []
        res.meta, res.marker_source = meta, found.source

        if args.dry_run:
            n = len(found.markers)
            if single_in_playlist:
                idx = entry.get("playlist_index") or 1
                res.tracks = [Track(idx, ctx.n_entries, found.markers[0].title, 0, None, Path("-"), track_artists[0])]
            else:
                credits = credit_markers(found, meta.artist)
                res.tracks = [
                    Track(i + 1, n, m.title, m.start, m.end, Path("-"), a)
                    for i, (m, a) in enumerate(zip(found.markers, credits, strict=True))
                ]
            return res

        # 2. download
        info, audio, thumb = download_with_retry(url, work, res, args.retries)

        # 3. silence fallback for long videos without any markers
        if found.source == "none" and args.silence and res.duration >= args.silence_min_duration:
            t0 = time.perf_counter()
            with console.status("  no time markers anywhere - detecting silences …"):
                ms = mk.detect_silence(str(audio), res.duration)
            res.timings["silence detect"] = time.perf_counter() - t0
            if ms:
                found = mk.MarkerResult(mk.fill_ends(ms, res.duration), "silence")
        if not found.markers:
            found = mk.MarkerResult([mk.Marker(0, meta.album, None)], "none (whole file)")
        res.marker_source = found.source

        # 4. cover (once per playlist album)
        t0 = time.perf_counter()
        if single_in_playlist and ctx.cover and ctx.cover.data:
            cover = ctx.cover
            res.cover_source = f"{cover.source} (album cover, shared)"
        else:
            cover = choose_cover(
                thumb, meta, work, use_caa=args.musicbrainz, explicit=args.cover, covers_dir=args.covers_dir
            )
            res.cover_source, res.cover_notes = cover.source, cover.notes
            if cover.size:
                res.cover_source += f" {cover.size[0]}x{cover.size[1]}"
            if single_in_playlist:
                ctx.cover = cover
        res.timings["cover"] = time.perf_counter() - t0

        # 5. slice + tag
        if meta.release_type is None and ctx is None and len(found.markers) == 1:
            meta.release_type = "Single"  # one video, one track
        artist_dir = artist_folder(meta.artist, args.artist_case, args.small_words)
        out_dir = existing_album_dir(args.output, meta) or args.output / artist_dir / album_folder_for(meta)
        res.out_dir = out_dir
        t0 = time.perf_counter()
        if single_in_playlist:
            idx = entry.get("playlist_index") or 1
            title = found.markers[0].title
            t = Track(
                idx,
                ctx.n_entries,
                title,
                0,
                None,
                out_dir / track_filename(idx, ctx.n_entries, title),
                track_artists[0],
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(audio, t.path)
            res.tracks = [t]
        else:
            credits = credit_markers(found, meta.artist)
            res.tracks = slice_tracks(audio, found.markers, out_dir)
            for t, a in zip(res.tracks, credits, strict=True):
                t.artist = a
        res.timings["slice"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        for t in res.tracks:
            write_tags(
                t,
                album=meta.album,
                album_artist=meta.artist,
                year=meta.year,
                genre=meta.genre,
                cover=cover.data,
                source_url=url,
            )
        res.timings["tag"] = time.perf_counter() - t0
    except Exception as e:
        if is_bot_check(e):  # every following request would fail the same way -> stop the run
            raise BotCheckError(str(e)) from e
        res.error = f"{type(e).__name__}: {e}"  # keep going with the rest of a playlist
    finally:
        if not args.keep_source and not args.dry_run:
            shutil.rmtree(work, ignore_errors=True)
    return res


# --------------------------------------------------------------- output ----


def show_result(res: Result) -> None:
    if res.error:
        console.print(Panel(f"[red]{res.error}", title=res.title, border_style="red"))
        return
    m = res.meta
    head = Table.grid(padding=(0, 2))
    head.add_column(style="bold")
    head.add_column()
    head.add_row("Artist", m.artist)
    head.add_row("Album", m.album + (f" ({m.year})" if m.year else ""))
    head.add_row("Type", m.release_type or "[dim]Album (assumed)")
    if m.genre:
        head.add_row("Genre", m.genre)
    mb = f"matched (score {m.mb_score})" if m.mb_release_group else f"[dim]{m.mb_error or 'disabled'}"
    head.add_row("MusicBrainz", mb)
    head.add_row("Time markers", res.marker_source)
    if res.cover_source:
        notes = f"  [dim]({'; '.join(res.cover_notes)})" if res.cover_notes else ""
        head.add_row("Cover", res.cover_source + notes)
    if res.out_dir:
        head.add_row("Output", str(res.out_dir))

    tbl = Table(show_edge=False, header_style="bold magenta")
    tbl.add_column("#", justify="right")
    tbl.add_column("Title")
    show_artist = any(t.artist and t.artist != m.artist for t in res.tracks)
    if show_artist:
        tbl.add_column("Artist")
    tbl.add_column("Start", justify="right")
    tbl.add_column("Length", justify="right")
    tbl.add_column("Size", justify="right")
    for t in res.tracks:
        length = (t.end - t.start) if t.end is not None else (res.duration - t.start if res.duration else None)
        size = fmt_bytes(t.path.stat().st_size) if t.path.exists() else "-"
        row = [str(t.number), t.title] + ([t.artist] if show_artist else [])
        tbl.add_row(*row, fmt_time(t.start), fmt_time(length), size)

    timing = "  ".join(f"{k} [bold]{v:.1f}s[/]" for k, v in res.timings.items())
    console.print(Panel.fit(head, title=f"[bold]{res.title}", border_style="cyan"))
    console.print(tbl)
    console.print(f"  [dim]{timing}[/]\n")


def show_summary(results: list[Result], wall: float, dry: bool, in_library: int = 0) -> None:
    ok = [r for r in results if not r.error]
    tracks = sum(len(r.tracks) for r in ok)
    out_bytes = sum(t.path.stat().st_size for r in ok for t in r.tracks if t.path.exists())
    audio = sum(r.duration for r in ok)
    s = Table(title="Summary", show_header=False, title_style="bold green")
    s.add_column(style="bold")
    s.add_column(justify="right")
    s.add_row("Videos processed", f"{len(ok)}/{len(results)}")
    if in_library:
        s.add_row("Already in library", str(in_library))
    s.add_row("Tracks" + (" (detected)" if dry else " written"), str(tracks))
    s.add_row("Audio duration", fmt_time(audio))
    if not dry:
        s.add_row("Downloaded", fmt_bytes(sum(r.download_bytes for r in ok)))
        s.add_row("Output size", fmt_bytes(out_bytes))
    s.add_row("Wall time", f"{wall:.1f}s")
    if audio and wall and not dry:
        s.add_row("Speed", f"{audio / wall:.0f}x realtime")
    console.print(s)


# ----------------------------------------------------------------- main ----


def build_parser(prog: str = "yt-audio-slicer") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=prog, description=__doc__, epilog="Library tools: yt-audio-slicer {tidy,missing,join,prune} --help"
    )
    p.add_argument("urls", nargs="*", help="YouTube video or playlist URL(s)")
    p.add_argument(
        "-f",
        "--file",
        dest="url_files",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="read URLs from a text file, one per line ('#' starts a comment line, '-' reads stdin); "
        "can be given more than once",
    )
    p.add_argument(
        "--continue",
        dest="resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="in a URL file, skip the playlist videos that a '# continue:' line marks as done "
        "(default: on; --no-continue processes those playlists from the start)",
    )
    p.add_argument("-o", "--output", type=Path, default=Path("output"), help="output directory (default: ./output)")
    p.add_argument(
        "--force",
        action="store_true",
        help="download videos again even if the library already has tracks made from them",
    )
    add_artist_case_args(p)
    p.add_argument("--cover", type=Path, help="force this image as front cover")
    p.add_argument(
        "--covers-dir",
        type=Path,
        default=Path("covers"),
        help="fallback dir searched for an image matching artist/album (default: ./covers)",
    )
    p.add_argument("-n", "--dry-run", action="store_true", help="only detect metadata & markers, no download")
    p.add_argument(
        "--no-musicbrainz",
        dest="musicbrainz",
        action="store_false",
        help="skip MusicBrainz / Cover Art Archive lookups",
    )
    p.add_argument("--no-comments", dest="comments", action="store_false", help="don't scan comments for timestamps")
    p.add_argument("--max-comments", type=int, default=100)
    p.add_argument("--no-silence", dest="silence", action="store_false", help="don't fall back to silence detection")
    p.add_argument(
        "--silence-min-duration",
        type=float,
        default=900,
        help="only use silence detection for videos at least this long (s, default 900)",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=2,
        metavar="N",
        help="retry a download that fails with HTTP 403 up to N times (default 2)",
    )
    add_auth_args(p)
    p.add_argument(
        "--keep-source", action="store_true", help="keep the full downloaded mp3 + thumbnail in OUTPUT/.work"
    )
    return p


def show_bot_check(err: str, used_cookies: bool) -> None:
    if used_cookies:
        hint = (
            "The cookies were sent but YouTube still refused. They are probably expired or not logged in:\n"
            "  - open youtube.com in that browser, make sure you are signed in, and try again\n"
            "  - or export a fresh cookies.txt and pass it with [bold]--cookies FILE[/]"
        )
    else:
        hint = (
            "YouTube is rate-limiting / bot-checking this machine. Options:\n"
            "  - wait a while (often minutes to a few hours) and run again\n"
            "  - pass your YouTube login cookies:\n"
            "      [bold]--cookies-from-browser firefox[/]   (browser where you are signed in to YouTube)\n"
            "      [bold]--cookies cookies.txt[/]            (Netscape-format export)\n"
            "    Downloads are then tied to your account, so don't overdo it."
        )
    first_line = re.sub(r"^ERROR:\s*(\[\w+\]\s*)?", "", err.splitlines()[0]).split(". Use --cookies")[0]
    console.print(Panel(f"[red]{first_line}[/]\n\n{hint}", title="YouTube wants you to sign in", border_style="red"))


def resolve(url: str) -> tuple[str | None, list[dict], list[dict], str | None]:
    """(playlist title, all entries, entries to process, playlist id) for one input URL."""
    if vp := video_in_playlist(url):
        vid, lst = vp
        title, entries = list_entries(playlist_url(lst))
        todo = [e for e in entries if e.get("id") == vid]
        if title is not None and todo:
            return title, entries, todo, lst
        title, entries = list_entries(watch_url(vid), no_playlist=True)  # not (or no longer) in that playlist
        return None, entries, entries, None
    title, entries = list_entries(url, no_playlist=is_plain_video_with_list(url))
    return title, entries, entries, playlist_id(url) if title is not None else None


def in_library(tracks: list[TrackInfo] | None, playlist_title: str | None, index: int | None) -> list[TrackInfo]:
    """Library tracks that make downloading this video again unnecessary. A video in an album playlist only counts
    when it is already in that album at that position (the same song can be on an album and on a compilation, or
    twice in one playlist)."""
    if not tracks or playlist_title is None:
        return tracks or []
    want = album_key(PLAYLIST_PREFIX_RE.sub("", playlist_title))
    return [
        t
        for t in tracks
        if want in (album_key(t.album or ""), album_key(parse_album_folder(t.path.parent.name)[1]))
        and (t.number is None or index is None or t.number == index)
    ]


def main(argv: list[str] | None = None, prog: str = "yt-audio-slicer") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    files: list[UrlFile] = []
    for f in args.url_files:
        try:
            files.append(read_url_file(f))
        except OSError as e:
            parser.error(f"can't read URL file {f}: {e.strerror or e}")
    all_urls = [(u, set()) for u in args.urls] + [item for f in files for item in parse_url_file(f)]
    if not all_urls:
        parser.error("no URLs given (pass them as arguments or with -f FILE)")

    # drop duplicate URLs, keeping the first occurrence
    urls: list[tuple[str, set[str]]] = []
    seen_urls, dupes = set(), 0
    for u, skip in all_urls:
        if (k := url_key(u)) in seen_urls:
            dupes += 1
        else:
            seen_urls.add(k)
            urls.append((u, skip if args.resume else set()))
    if dupes:
        console.print(f"[dim]skipped {dupes} duplicate URL(s)[/]")

    # videos that already have tracks in the library (found by the source URL tag, wherever the files are now)
    library: dict[str, list[TrackInfo]] = {}
    if not args.force:
        with console.status(f"indexing {args.output} …"):
            library = library_index(args.output, DEFAULT_EXCLUDE)
        if library:
            console.print(
                f"[dim]library: {sum(map(len, library.values()))} tracks from {len(library)} videos "
                f"already in {args.output} (--force to download them again)[/]"
            )

    set_auth(args)
    t_start = time.perf_counter()
    results: list[Result] = []
    n_in_library = 0
    # (video ID, playlist ID) done in this run; a video in two album playlists belongs in both albums
    done_videos: set[tuple[str, str | None]] = set()
    # every URL that is not fully done yet, with the videos of it that are; used for the "remaining" file
    remaining: dict[str, UrlProgress] = {}
    for u, skip in urls:
        remaining[url_key(u)] = UrlProgress(u, set(skip))
    try:
        for url, skip in urls:
            prog = remaining[url_key(url)]
            try:
                with console.status(f"resolving {url} …"):
                    playlist_title, entries, todo, lst = resolve(url)
            except Exception as e:
                if is_bot_check(e):
                    raise BotCheckError(str(e)) from e
                console.print(Panel(f"[red]{type(e).__name__}: {e}", title=url, border_style="red"))
                results.append(Result(url=url, title=url, error=str(e)))
                continue
            prog.total, prog.playlist_id = len(todo), lst
            ctx = PlaylistContext(playlist_title, lst, len(entries)) if playlist_title is not None else None
            if playlist_title:
                extra = f", only #{todo[0].get('playlist_index')}" if len(todo) < len(entries) else ""
                console.rule(f"[bold]Playlist:[/] {playlist_title} ({len(entries)} videos{extra})")
            if skip and (n_skip := sum(e.get("id") in skip for e in todo)):
                console.print(
                    f"[dim]continuing: {n_skip} video(s) already done in an earlier run (--no-continue to redo them)[/]"
                )
            failed = False
            for e in todo:
                i = e.get("playlist_index") or 1
                pos = f"[{i}/{len(entries)}]"
                label = e.get("title") or entry_url(e)
                vid = e.get("id")
                if vid and vid in skip:
                    continue
                if vid and (vid, lst) in done_videos:
                    console.print(f"[dim]▶ {pos} {label} — already handled in this run, skipped[/]")
                    prog.done.add(vid)
                    continue
                if vid and (hits := in_library(library.get(vid), playlist_title, e.get("playlist_index"))):
                    where = hits[0].path.parent.relative_to(args.output)
                    console.print(f"[dim]▶ {pos} {label} — already in library ({where}), skipped[/]")
                    prog.done.add(vid)
                    n_in_library += 1
                    continue
                console.print(f"[bold cyan]▶ {pos}[/] {label}")
                res = process_entry(e, ctx, args)
                show_result(res)
                results.append(res)
                if vid and not res.error:
                    done_videos.add((vid, lst))
                    prog.done.add(vid)
                elif res.error:
                    failed = True
                    if vid:
                        prog.failed[vid] = f"{short_error(res.error)} - {pos} {label}"
            prog.finished = True
            if not failed:
                del remaining[url_key(url)]
    except BotCheckError as e:
        show_bot_check(str(e), used_cookies=bool(YDL_AUTH))
        if args.dry_run:
            console.print("[dim]dry run: no remaining-URL file written[/]")
        else:
            write_remaining(files, remaining)
        if results:
            show_summary(results, time.perf_counter() - t_start, args.dry_run, n_in_library)
        return 2
    show_summary(results, time.perf_counter() - t_start, args.dry_run, n_in_library)
    if remaining and not args.dry_run:
        write_remaining(files, remaining)
    return 0 if all(not r.error for r in results) else 1
