"""Folder / file naming rules shared by the downloader and the library tools.

Layout:  <Artist>/<year> - <album> (<EP|Single|Live>)/<NN> - <title>.mp3
"""

from __future__ import annotations

import re
import unicodedata

# how artist folder names are written; see artist_case()
ARTIST_CASES = ("headline", "title", "sentence", "upper", "lower", "keep")
DEFAULT_ARTIST_CASE = "headline"
# kept lowercase inside a name in 'headline' case (never as the first or last word)
SMALL_WORDS = frozenset(
    "a an and as at but by for from in into nor of on or over per the to upon via vs with".split()  # noqa: SIM905
)
WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")
RELEASE_TYPES = ("EP", "Single", "Live")

# hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar, minus sign, fullwidth hyphen-minus
DASHES_RE = re.compile(r"[‐-―−﹘﹣－]")
# "A, B", "A feat. B", "A ft. B", "A featuring B" -- '&' and 'and' are left alone ("Simon & Garfunkel")
ARTIST_SPLIT_RE = re.compile(r"\s*,\s*|\s+(?:feat\.?|ft\.?|featuring)\s+", re.I)
FEAT_RE = re.compile(r"(?:^|[\s(\[])(?:feat\.?|ft\.?|featuring)\s", re.I)
EP_SINGLE_SUFFIX_RE = re.compile(r"\s*[-]?\s*[(\[]?\b(EP|Single)\b[)\]]?\s*$", re.I)
LIVE_SUFFIX_RE = re.compile(r"\s*(?:[(\[]\s*Live\s*[)\]]|-\s*Live)\s*$", re.I)
LIVE_IN_NAME_RE = re.compile(r"[(\[]\s*live\b|\blive\s+(?:at|in|from|on)\b|\blive\s*[)\]]|-\s*live\b", re.I)
NEW_FOLDER_RE = re.compile(r"^(?P<year>(?:19|20)\d{2}) - (?P<name>.+?)(?: \((?P<type>EP|Single|Live)\))?$")
OLD_FOLDER_RE = re.compile(r"^(?P<name>.+?)(?: \((?P<year>(?:19|20)\d{2})\))?(?: \((?P<type>EP|Single|Live)\))?$")
FOLDER_NOISE_RE = re.compile(
    r"\s*[(\[]?\b(?:full\s*album|full\s*ep|official\s*(?:audio|album)?|album\s*stream|hq|hd)\b[)\]]?", re.I
)
CONNECTOR_RE = re.compile(r"(?:vs\.?|versus|x|and|&|\+|with|meets|feat\.?|ft\.?|featuring)\s", re.I)
NEW_ALBUM_YEAR_RE = re.compile(r"\s*\bnew\s+album\s+((?:19|20)\d{2})\b", re.I)


def normalize_dashes(s: str) -> str:
    return DASHES_RE.sub("-", s)


def safe_name(s: str, maxlen: int = 120) -> str:
    """A file / folder name that is valid on Linux, macOS and Windows."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .")
    return s[:maxlen].rstrip(" .") or "untitled"


def _capitalize(word: str) -> str:
    """'EPICA' / 'epica' -> 'Epica'; mixed-case words keep their inner capitals ('McCartney')."""
    rest = word[1:].lower() if word.isupper() or word.islower() else word[1:]
    return word[:1].upper() + rest


def artist_case(name: str, mode: str = DEFAULT_ARTIST_CASE, small_words: frozenset[str] = SMALL_WORDS) -> str:
    """Write an artist name in one of ARTIST_CASES:

    headline  'THE BEATLES' -> 'The Beatles', 'sons of the east' -> 'Sons of the East' (small_words stay lowercase,
              except as the first or last word)
    title     every word capitalised: 'Sons Of The East'
    sentence  only the first letter: 'Sons of the east'
    upper / lower / keep
    """
    if mode in ("headline", "title"):
        words = list(WORD_RE.finditer(name))
        out, pos = [], 0
        for i, m in enumerate(words):
            w = m.group(0)
            small = mode == "headline" and 0 < i < len(words) - 1 and w.casefold() in small_words
            out += [name[pos : m.start()], w.lower() if small else _capitalize(w)]
            pos = m.end()
        return "".join(out) + name[pos:]
    if mode == "sentence":
        return name[:1].upper() + name[1:].lower()
    if mode == "upper":
        return name.upper()
    if mode == "lower":
        return name.lower()
    return name


def parse_small_words(s: str) -> frozenset[str]:
    """'the, of,on' -> {'the', 'of', 'on'} (for --small-words)."""
    return frozenset(w.strip().casefold() for w in s.split(",") if w.strip())


def add_artist_case_args(p) -> None:
    """--artist-case / --small-words, shared by the commands that name artist folders."""
    p.add_argument(
        "--artist-case",
        choices=ARTIST_CASES,
        default=DEFAULT_ARTIST_CASE,
        help="how artist folder names are written: 'headline' = 'Sons of the East' (default), "
        "'title' = 'Sons Of The East', 'sentence' = 'Sons of the east', 'upper', 'lower', 'keep' = as tagged",
    )
    p.add_argument(
        "--small-words",
        type=parse_small_words,
        default=SMALL_WORDS,
        metavar="WORDS",
        help="comma-separated words that 'headline' case keeps lowercase (default: "
        f"{','.join(sorted(SMALL_WORDS))}; '' for none)",
    )


def split_artists(s: str | None) -> list[str]:
    """'Epica, Roy Khan' / 'Stahlmann feat. Teufel' -> ['Epica', 'Roy Khan'] / ['Stahlmann', 'Teufel']."""
    if not s:
        return []
    return [a.strip() for a in ARTIST_SPLIT_RE.split(s) if a and a.strip()]


def primary_artist(s: str | None) -> str:
    parts = split_artists(s)
    return parts[0] if parts else (s or "").strip()


def has_feat(title: str) -> bool:
    return bool(FEAT_RE.search(title))


MAX_FEATURED = 3


def add_featured(title: str, others: list[str]) -> str:
    """'Song' + ['B', 'C'] -> 'Song feat. B, C'. Titles that already credit someone ('(feat. X)') stay as they are.
    More than MAX_FEATURED extra names are a full line-up / writer credits (YouTube Music lists those), not guests."""
    others = [o for o in others if o]
    if not others or has_feat(title) or len(others) > MAX_FEATURED:
        return title
    return f"{title} feat. {', '.join(others)}"


def artist_folder(artist: str, mode: str = DEFAULT_ARTIST_CASE, small_words: frozenset[str] = SMALL_WORDS) -> str:
    return safe_name(artist_case(normalize_dashes(primary_artist(artist)), mode, small_words))


def bare_album(album: str) -> str:
    """'The wilted EP' -> 'The wilted', 'Song - Single' -> 'Song', 'Retrospect (Live)' -> 'Retrospect'."""
    a = normalize_dashes(album).strip()
    a = EP_SINGLE_SUFFIX_RE.sub("", a).strip() or a
    return LIVE_SUFFIX_RE.sub("", a).strip() or a


def is_live(*texts: str | None) -> bool:
    return any(t and LIVE_IN_NAME_RE.search(t) for t in texts)


def album_folder(album: str, year: str | int | None, release_type: str | None) -> str:
    """'<year> - <album> (<EP|Single|Live>)'; the year and the type are left out when unknown / a studio album.
    '(Live)' is left out too when the name already says so ('… (Live in Miskolc)')."""
    name = bare_album(album)
    folder = f"{str(year)[:4]} - {name}" if year else name
    if release_type in ("EP", "Single") or (release_type == "Live" and not is_live(name)):
        folder += f" ({release_type})"
    return safe_name(folder)


def track_filename(number: int, total: int, title: str, ext: str = ".mp3") -> str:
    width = max(2, len(str(total)))
    return f"{number:0{width}d} - {safe_name(title)}{ext}"


def parse_album_folder(folder: str, artist: str | None = None) -> tuple[str | None, str, str | None, bool]:
    """Folder name -> (year, album name, release type, already in the new format).
    Understands '2016 - Name (EP)', 'Name (2014) (EP)' and leftovers like 'Artist - Name (FULL ALBUM)'."""
    f = normalize_dashes(folder).strip()
    if m := NEW_FOLDER_RE.match(f):
        return m["year"], m["name"], m["type"], True
    year = None
    if m := NEW_ALBUM_YEAR_RE.search(f):
        year, f = m.group(1), NEW_ALBUM_YEAR_RE.sub("", f)
    f = re.sub(r"\s{2,}", " ", FOLDER_NOISE_RE.sub("", f)).strip(" -")
    if artist:  # 'Eisbrecher - Liebe Macht Monster', 'Eisbrecher KALTFRONT'
        for a in {artist, primary_artist(artist)}:
            rest = re.sub(rf"^{re.escape(a)}\s*-?\s*", "", f, flags=re.I).strip()
            if rest and rest != f and not CONNECTOR_RE.match(rest):  # keep 'Epica vs. Attack on Titan Songs'
                f = rest
                break
    m = OLD_FOLDER_RE.match(f)
    name, rtype = m["name"], m["type"]
    year = m["year"] or year
    if not rtype and (t := EP_SINGLE_SUFFIX_RE.search(name)):
        rtype = "EP" if t.group(1).upper() == "EP" else "Single"
    if not rtype and LIVE_SUFFIX_RE.search(name):
        rtype = "Live"
    return year, bare_album(name), rtype, False


def fold(s: str) -> str:
    """Comparison key: case, accents, dashes, punctuation and spacing don't matter."""
    s = unicodedata.normalize("NFKD", normalize_dashes(s)).encode("ascii", "ignore").decode()
    s = s.casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def album_key(name: str) -> str:
    return fold(bare_album(name))


def artist_key(name: str) -> str:
    return fold(primary_artist(name))


def is_all_caps(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) > 1 and all(c.isupper() for c in letters)
