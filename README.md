# yt-audio-slicer

[![CI](https://github.com/lvonkacsoh/yt-audio-slicer/actions/workflows/ci.yml/badge.svg)](https://github.com/lvonkacsoh/yt-audio-slicer/actions/workflows/ci.yml)

> **Note:** This tool is a quick hack job to give AI assisted development a shot. I created this  mostly for my own music library and only put this on github because I think it may be handy for a few people. It deserves no praise tho.
> And I have no intention of maintaining it if I don't find something myself. Issues and pull requests will most likely go unanswered. Use it as it is, or fork it.
> The rest of this file has been generated using some of my inputs.

Download a YouTube video or playlist, split it into tracks, and write tagged MP3 files (artist, album, title, track number, year, cover). Library tools fix up the result: merge scattered albums, list missing tracks, join split files and remove empty folders.

It is built for full-album uploads and works with playlists too.

## Requirements

- Python 3.11 or newer
- `ffmpeg` and `ffprobe` on your `PATH`
- [uv](https://docs.astral.sh/uv/) (recommended) or pipx

## Installation

Install the `yt-audio-slicer` command:

```sh
uv tool install git+https://github.com/lvonkacsoh/yt-audio-slicer
# or
pipx install git+https://github.com/lvonkacsoh/yt-audio-slicer
```

Or run it from a clone without installing:

```sh
git clone https://github.com/lvonkacsoh/yt-audio-slicer
cd yt-audio-slicer
uv sync
uv run yt-audio-slicer --help
```

The examples below use `uv run yt-audio-slicer …` (from a clone). With an installed tool, leave out `uv run`.

yt-dlp has to keep up with changes on YouTube's side. If downloads start failing, update it first:
`uv tool upgrade yt-audio-slicer` (installed) or `uv lock --upgrade-package yt-dlp && uv sync` (clone).

## Commands

```
yt-audio-slicer [download] [options] URL ...   download and split (the default command)
yt-audio-slicer tidy     [DIR]                 merge scattered albums, fix folder names, remove empty folders
yt-audio-slicer missing  [DIR] [-u URLFILE]    list missing tracks, find them in playlists
yt-audio-slicer join     INPUT ... -o FILE     join audio files back into one
yt-audio-slicer prune    [DIR]                 remove empty folders
```

`yt-audio-slicer COMMAND --help` lists the options of each command. The commands that move or delete anything (`tidy`, `prune`) only print what they would do until you add `--yes`.

Exit codes: `0` success, `1` some items failed, `2` usage error or YouTube bot check, `130` interrupted.

## Downloading

### A single video

```sh
uv run yt-audio-slicer "https://youtu.be/VIDEO_ID"
```

The tool splits the video into one MP3 per track and writes them to `output/<Artist>/<Year> - <Album> (<Type>)/`.

### Folder names

| Part       | Rule                                                                                                                                                                                                                              |
|------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `<Artist>` | Only the main artist, written in headline case: every word starts with a capital letter, except small words like `of`, `the` or `and` inside the name (`THE BEATLES` becomes `The Beatles`, `sons of the east` becomes `Sons of the East`). See [Artist folder case](#artist-folder-case) for the other styles. |
| `<Year> - `| Left out when the year is unknown. The dash is always a plain `-`; en dashes and em dashes in album names become `-` too.                                                                                                                   |
| `(<Type>)` | `(EP)`, `(Single)` or `(Live)`. Left out for studio albums. `(Live)` is also left out when the name already says so (`… Live at the BBC`).                                                                                    |

Examples:

```
output/The Beatles/1969 - Abbey Road/
output/The Beatles/1967 - Magical Mystery Tour (EP)/
output/The Beatles/1994 - Live at the BBC/
output/The Beatles/The Beatles at the Hollywood Bowl (Live)/
output/The Beatles/1968 - Hey Jude (Single)/
```

### Artist folder case

`--artist-case` (for `download` and `tidy`) sets how artist folder names are written:

| Value                | `sons of the EAST` becomes |
|----------------------|----------------------------|
| `headline` (default) | `Sons of the East`         |
| `title`              | `Sons Of The East`         |
| `sentence`           | `Sons of the east`         |
| `upper`              | `SONS OF THE EAST`         |
| `lower`              | `sons of the east`         |
| `keep`               | `sons of the EAST`         |

In `headline` case the first and the last word always get a capital letter (`The The`). Words written in all capitals or all lowercase are normalised (`EPICA` becomes `Epica`); words in mixed case keep their inner capitals (`McCartney`).

`--small-words` replaces the list of words that `headline` case keeps lowercase, as a comma-separated list: `--small-words "of,the,and,von,und"`. `--small-words ""` keeps no word lowercase. The default list is `a, an, and, as, at, but, by, for, from, in, into, nor, of, on, or, over, per, the, to, upon, via, vs, with`.

Run `tidy --yes` to rename existing artist folders after changing the style.

### Existing album folders

If the library already has a folder for the same artist and album (in any case, with or without a year, or in the old `Album (Year)` naming), new tracks go into that folder, so retries and gap-filling don't create a second folder. `tidy` renames old folders to the current scheme.

The release type comes from MusicBrainz when there is a match. Otherwise it is guessed from the title ("EP", "Single", "(Live)"). A single video that becomes one track counts as a single.

### Featured artists

A track by more than one artist stays under the main artist. The others are appended to the title:

```
artist "The Beatles, Billy Preston", title "Get Back"   ->   output/The Beatles/…/12 - Get Back feat. Billy Preston.mp3
```

The track artist tag is the main artist too. Titles that already credit someone (`… (feat. Billy Preston)`) are left as they are. A list of more than 3 extra names is treated as a band line-up or writer credits (YouTube Music lists those), not as guests, and is dropped.

### A whole playlist

```sh
uv run yt-audio-slicer "https://www.youtube.com/playlist?list=PL…"
```

- **A video with time markers** (for example a full-album upload) is split into tracks, exactly like a single video.
- **A video without time markers** (one song) becomes one track of the playlist's album. Album, album artist and year are worked out **once per playlist**: from the first song's YouTube Music metadata (`album`, `artists`, `release_year`) or else the playlist title, followed by a single MusicBrainz lookup. All songs share the same cover and land in the same folder. The track number is the song's position in the playlist; the title and artist come from YouTube Music's `track` / `artists` fields, and only without them from the video title (`Artist - Song`).

If one video fails, the tool keeps going with the rest and lists the failure at the end.

### One video of a playlist

A link to a video **with** its playlist (`watch?v=ID&list=PL…`) downloads only that video, as part of the playlist: same album folder, track number and cover as if the whole playlist had run. The failed-video links in `*.remaining.txt` and the output of `missing --save` use this form.

Links to a video in a mix (`list=RD…`), an uploads list, *Liked videos* or *Watch later* are downloaded as a plain video.

### Several URLs, URL files

```sh
uv run yt-audio-slicer URL1 URL2 URL3
uv run yt-audio-slicer -f urls.txt
```

A URL file has one URL per line. Blank lines and lines starting with `#` are skipped. `-f` can be repeated and mixed with URLs on the command line, and `-f -` reads stdin.

### Already downloaded? (duplicates)

- **Within a run:** duplicate URLs are skipped, even when written differently (`youtu.be/ID`, `watch?v=ID&t=30`, `music.youtube.com/…`, `/shorts/ID`).
- **Across runs:** before downloading, the tool reads the source-URL tag (`WOAS`) of every MP3 already in the output folder. A video that already has tracks there is skipped, wherever the files are now (also after `tidy` or moving them by hand). For a song in an album playlist, it is only skipped if it's already in **that** album at that position, because one song can be on an album and on a compilation. So re-running a playlist after errors downloads just what's missing.
- `--force` downloads everything again.

Folders starting with `.` or `_` (e.g. `.work`, `_backup`) are ignored by the duplicate check and the library tools.

### Errors, HTTP 403 and resuming

- **HTTP 403** on a download is usually a stale stream link. The tool starts that video over up to `--retries` times (default 2).
- **Failed videos** are collected. After the run, each URL file passed with `-f` gets a copy with only what's still to do, next to the original: `urls.txt` becomes `urls.remaining.txt`. A playlist where only some videos failed is replaced there by links to exactly those videos (with their playlist, see above), each with a comment saying why it failed:

  ```
  # failed: HTTP 403 Forbidden - [7/17] Here Comes the Sun
  https://www.youtube.com/watch?v=…&list=OLAK5uy_…
  ```

  Resume with `uv run yt-audio-slicer -f urls.remaining.txt`. Remaining URLs from the command line are printed instead. A dry run writes no remaining file.

### When YouTube asks you to sign in

After many requests in a short time, YouTube may refuse with "Sign in to confirm you're not a bot" (or HTTP 429). The tool then stops the run (every following video would fail the same way), shows what was done, writes the remaining files and exits with code 2. A playlist interrupted halfway gets a `# continue:` line listing the videos already done:

```
# continue: skip=VIDEO_ID_1,VIDEO_ID_2  (2 of 17 videos done)
https://www.youtube.com/playlist?list=PL…
```

On resume, those videos are skipped (`--no-continue` processes them again). Track numbers and the album folder stay the same.

To get going again, either **wait** (minutes to a few hours) or **pass your YouTube login cookies** with `--cookies-from-browser firefox` or `--cookies cookies.txt`. Downloads are then tied to your account, so don't overdo it. From a sandboxed (Flatpak) editor terminal, the browser profile may not be readable; use an exported `cookies.txt`.

### Dry run

`-n` shows what would be detected (artist, album, track list) and which videos are already in the library, without downloading.

## Library tools

All tools work on `./output` by default. Recommended order after a messy run: `tidy`, then `missing`, then download the missing tracks.

### `tidy`: merge scattered albums

```sh
uv run yt-audio-slicer tidy                    # dry run: lists the planned changes
uv run yt-audio-slicer tidy --yes              # apply
uv run yt-audio-slicer tidy --yes --fix-tags --duplicates park
```

- Albums match when the main artist (`The Beatles, Billy Preston` → `The Beatles`, any case) and the album name (ignoring year, type, `Full Album`, an artist-name prefix) are the same. Their files are moved into one folder, named by the current scheme: `THE BEATLES/Let It Be`, `The Beatles, Billy Preston/Let It Be` → `The Beatles/Let It Be`; `The Beatles - Abbey Road (FULL ALBUM)` + `Abbey Road (1969)` → `1969 - Abbey Road`.
- **Orphans:** an artist folder with a single album that fills gaps in the same-named album of another artist (same track total, no track number taken) is merged into it. This catches mis-read artists like `Something (Remastered 2009/Abbey Road`. These merges are marked in the list; `--no-orphans` turns this off.
- **Duplicates** (same track number and title already in the target) are left in place and listed, or moved to `output/.yt-audio-slicer/duplicates/` with `--duplicates park`.
- `--fix-tags` also rewrites tags. The album artist gets one spelling (`THE BEATLES` → `The Beatles`). Track artists `A, B` / `A feat. B` become `A`, with ` feat. B` added to the title. A name credited on at least half of an album's tracks is a band member or writer credit and is dropped instead.
- Empty folders are removed afterwards (`--no-prune` keeps them).
- Every applied change is logged to `output/.yt-audio-slicer/tidy-<time>.log`, so moves can be traced back.

### `missing`: list missing tracks

```sh
uv run yt-audio-slicer missing                               # albums with gaps in their track numbers
uv run yt-audio-slicer missing -u queue.txt -s missing.txt   # look the gaps up in these playlists, save them
uv run yt-audio-slicer -f missing.txt                        # download exactly the missing tracks
```

A track is missing when its number is absent from 1..total (`3/12` tags). With `-u`, the playlists in that URL file are listed (a quick, flat listing). An album whose tracks came from one of them gets the links of its missing tracks. `-s FILE` writes a URL file the downloader reads. Albums that were cut from one video show that video instead.

### `join`: join files back together

Some uploaders add extra time markers for convenience, for example to skip an intro that is long or annoying. The tool then cuts one song into two files. `join` puts them back together.

```sh
uv run yt-audio-slicer join "Abbey Road/16 - The End*.mp3" -o "Abbey Road/16 - The End.mp3" --title "The End"
uv run yt-audio-slicer join "Abbey Road/" -o abbey-road.mp3 --log join.log
uv run yt-audio-slicer join a.mp3 b.mp3 c.mp3 -o abc.mp3 --delete-sources
```

- **Inputs:** files, glob patterns (quote them so the shell leaves them alone) or a folder. Patterns and folders are joined in natural filename order; single files in the order given.
- **Audio:** copied without re-encoding when all sources share codec, sample rate and channels; otherwise re-encoded (`--reencode` forces it).
- **Tags:** inherited from the sources (MP3 output). An album-wide tag that differs between sources prints a warning and uses the most common value. Per-track tags (title, track number, …) use the first source's value; override them with `--title` and `--track`. `--log FILE` appends all conflicts to a log.
- `-n` previews, `--force` overwrites an existing output, `--delete-sources` removes the parts afterwards.

### `prune`: remove empty folders

```sh
uv run yt-audio-slicer prune          # lists empty folders and trees of empty folders
uv run yt-audio-slicer prune --yes    # removes them (never the given folder itself)
```

## How it works

1. **Time markers** are looked up in this order:
   1. the video's chapters
   2. timestamps in the video description
   3. timestamps in the top comments (the first 100)
   4. silence detection with ffmpeg, only for videos of at least 15 minutes

   If none of these finds anything, the whole file becomes a single track (or, in a playlist, one song of the playlist's album).
2. **Artist / album / year** come from YouTube Music metadata when present, else from the video title (`Artist - Album (Full Album)`) and the description. They are checked against [MusicBrainz](https://musicbrainz.org) for spelling, release year and type. An edition suffix (`(Expanded Edition)`, `(Deluxe)`, …) is retried without it and kept in the name.
3. **Cover**, first match wins (once per playlist album):
   1. the video thumbnail, if it is roughly square and at least 300 px
   2. the album cover from the [Cover Art Archive](https://coverartarchive.org)
   3. an image in `./covers/` whose filename matches the artist or album (or one named `cover`, `folder` or `front`)
   4. the thumbnail cropped to a square

   `--cover FILE` skips all of this and uses your image.
4. **Slicing** uses ffmpeg without re-encoding, so it is fast.
5. **Tags** written: title, artist, album artist, album, track number (`n/total`), year, genre (if known), front cover, source URL (`WOAS`, used by the duplicate check and `missing`).

## Download options

| Option                                     | Description                                                                              |
|--------------------------------------------|------------------------------------------------------------------------------------------|
| `-f, --file FILE`                          | Read URLs from a text file, one per line (can be repeated, `-` for stdin)                |
| `--no-continue`                            | Ignore `# continue:` lines in URL files and process partly done playlists from the start |
| `-o, --output DIR`                         | Output directory (default `./output`)                                                    |
| `--force`                                  | Download videos again even if the library already has tracks from them                   |
| `--artist-case STYLE`                      | How artist folder names are written: `headline` (default), `title`, `sentence`, `upper`, `lower`, `keep` |
| `--small-words WORDS`                      | Words that `headline` case keeps lowercase, comma-separated                              |
| `-n, --dry-run`                            | Only detect metadata and time markers, no download                                       |
| `--retries N`                              | Retry a download that fails with HTTP 403 up to N times (default 2)                      |
| `--cover FILE`                             | Use this image as the front cover                                                        |
| `--covers-dir DIR`                         | Folder searched for a matching cover image (default `./covers`)                          |
| `--no-musicbrainz`                         | Skip the MusicBrainz and Cover Art Archive lookups                                       |
| `--no-comments`                            | Don't search comments for timestamps                                                     |
| `--max-comments N`                         | How many top comments to search (default 100)                                            |
| `--no-silence`                             | Don't fall back to silence detection                                                     |
| `--silence-min-duration S`                 | Minimum video length in seconds for silence detection (default 900)                      |
| `--cookies-from-browser BROWSER[:PROFILE]` | Use your YouTube login cookies from a browser (`firefox`, `chrome`, `chromium`, …)       |
| `--cookies FILE`                           | Use cookies from a Netscape-format `cookies.txt`                                         |
| `--keep-source`                            | Keep the full downloaded MP3 and thumbnail in `OUTPUT/.work/`                            |

## Known limitations

- MP3s are encoded at high quality (VBR V0, about 270 kbps) from YouTube's roughly 128 kbps source. The output is about twice the download size with no gain in sound quality.
- Track titles found by silence detection are just "Track 1", "Track 2", …
- Time markers printed on the cover image are not read (that would need OCR).
- The audio of a music video can differ from the album version (longer intros, sound effects).
- MusicBrainz only matches releases it knows. Very new releases or unusual titles fall back to the guessed metadata.
- `headline` and `title` case change names written in all capitals on purpose (`ABBA` becomes `Abba`, `AC/DC` becomes `Ac/Dc`). Use `--artist-case keep` for those, or rename the folder by hand: new tracks find the existing folder whatever its case.

## Development

```sh
uv sync                     # creates .venv with the dev tools (pytest, ruff)
uv run pytest               # the tests need ffmpeg; the ones that use it are skipped without it
uv run ruff check           # lint
uv run ruff format          # format
```

CI runs the same checks on every push and pull request (`.github/workflows/ci.yml`).

```
src/yt_audio_slicer/
├── __main__.py  # python -m yt_audio_slicer
├── cli.py       # command dispatcher (download is the default command)
├── download.py  # download pipeline, rich output
├── urlfile.py   # URL files, '# continue:' lines, *.remaining.txt
├── tidy.py      # merge scattered albums, rename folders, fix tags
├── missing.py   # missing tracks, playlist lookup
├── join.py      # join audio files, merge tags
├── prune.py     # remove empty folders
├── library.py   # read the library: audio files, tags, source video IDs
├── naming.py    # folder / file naming rules shared by all commands
├── urls.py      # YouTube URL normalisation, video / playlist IDs
├── yt.py        # yt-dlp helpers, cookies, bot-check / 403 detection
├── markers.py   # chapters / description / comment parsing, silence detection
├── meta.py      # artist/album guessing, MusicBrainz, Cover Art Archive
├── cover.py     # cover selection and conversion
├── audio.py     # ffmpeg slicing, ID3 tagging
└── ui.py        # console and formatting helpers
tests/           # pytest
```

## Disclaimer

This tool is meant for content you are allowed to download, for example your own uploads, Creative Commons
material, or where your local law permits private copies. Respect YouTube's Terms of Service and the rights of the
artists. The tool doesn't get around DRM or paywalls.

## License

[MIT](LICENSE)
