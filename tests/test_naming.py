import pytest

from yt_audio_slicer.naming import (
    add_featured,
    album_folder,
    album_key,
    artist_case,
    artist_folder,
    bare_album,
    parse_album_folder,
    parse_small_words,
    primary_artist,
    split_artists,
)


@pytest.mark.parametrize(
    "name, mode, out",
    [
        ("THE BEATLES", "headline", "The Beatles"),
        ("the beatles", "headline", "The Beatles"),
        ("EPICA", "headline", "Epica"),
        ("sons of the east", "headline", "Sons of the East"),
        ("Florence and the Machine", "headline", "Florence and the Machine"),
        ("The The", "headline", "The The"),  # first and last word are always capitalised
        ("Paul McCartney", "headline", "Paul McCartney"),  # mixed case is kept
        ("Guns N' Roses", "headline", "Guns N' Roses"),
        ("sons of the east", "title", "Sons Of The East"),
        ("The Beatles", "sentence", "The beatles"),
        ("The Beatles", "upper", "THE BEATLES"),
        ("The Beatles", "lower", "the beatles"),
        ("AC/DC", "keep", "AC/DC"),
    ],
)
def test_artist_case(name, mode, out):
    assert artist_case(name, mode) == out


def test_artist_case_custom_small_words():
    assert artist_case("sons of the east", "headline", parse_small_words("of")) == "Sons of The East"
    assert artist_case("sons of the east", "headline", parse_small_words("")) == "Sons Of The East"


def test_artist_folder_uses_first_artist_only():
    assert artist_folder("Epica, Roy Khan") == "Epica"
    assert artist_folder("Stahlmann feat. Teufel") == "Stahlmann"
    assert artist_folder("Simon & Garfunkel", "keep") == "Simon & Garfunkel"


def test_split_artists():
    assert split_artists("Epica, Phil Lanzon, Tommy Karevik") == ["Epica", "Phil Lanzon", "Tommy Karevik"]
    assert split_artists("A ft. B") == ["A", "B"]
    assert primary_artist("") == ""


def test_add_featured():
    assert add_featured("Song", ["B", "C"]) == "Song feat. B, C"
    assert add_featured("Trois Vierges (feat. Roy Khan)", ["Roy Khan"]) == "Trois Vierges (feat. Roy Khan)"
    assert add_featured("Song", []) == "Song"
    assert add_featured("Song", list("ABCD")) == "Song"  # a line-up, not guests


@pytest.mark.parametrize(
    "album, year, rtype, out",
    [
        ("The Quantum Enigma", "2014", None, "2014 - The Quantum Enigma"),
        ("The wilted EP", "2026", "EP", "2026 - The wilted (EP)"),
        ("Song", None, "Single", "Song (Single)"),
        ("Retrospect – 10th Anniversary (Live)", None, "Live", "Retrospect - 10th Anniversary (Live)"),
        (
            "The Classical Conspiracy (Live in Miskolc)",
            "2009",
            "Live",
            "2009 - The Classical Conspiracy (Live in Miskolc)",
        ),
        ("Long Live", None, None, "Long Live"),
    ],
)
def test_album_folder(album, year, rtype, out):
    assert album_folder(album, year, rtype) == out


def test_bare_album():
    assert bare_album("Song - Single") == "Song"
    assert bare_album("Single") == "Single"


@pytest.mark.parametrize(
    "folder, artist, out",
    [
        ("2016 - The Holographic Principle", "Epica", ("2016", "The Holographic Principle", None, True)),
        ("Aspiral (2025)", "Epica", ("2025", "Aspiral", None, False)),
        ("The wilted (2026) (EP)", "Paleface Swiss", ("2026", "The wilted", "EP", False)),
        ("Eisbrecher - Liebe Macht Monster (FULL ALBUM)", "Eisbrecher", (None, "Liebe Macht Monster", None, False)),
        ("Eisbrecher KALTFRONT Full Album", "Eisbrecher", (None, "KALTFRONT", None, False)),
        ("Epica vs. Attack on Titan Songs", "EPICA", (None, "Epica vs. Attack on Titan Songs", None, False)),
        (
            "Powerwolf - Wake Up the Wicked (Full Album) New Album 2024",
            "Powerwolf, Matthew Greywolf",
            ("2024", "Wake Up the Wicked", None, False),
        ),
    ],
)
def test_parse_album_folder(folder, artist, out):
    assert parse_album_folder(folder, artist) == out


def test_album_key_ignores_case_dashes_and_type():
    assert album_key("Retrospect – 10th Anniversary") == album_key("RETROSPECT - 10th anniversary (Live)")
