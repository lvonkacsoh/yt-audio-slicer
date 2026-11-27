import pytest

from yt_audio_slicer.markers import Marker, clean_title, fill_ends, find_markers, parse_timestamps

DESCRIPTION = """Tracklist:
00:00 Intro
1. Samadhi 3:12
08:40 - 12:00 : "Resign to Surrender"
1:02:03 Unleashed (bonus) https://example.com
"""


def test_parse_timestamps_formats():
    ms = parse_timestamps(DESCRIPTION)
    assert [(m.start, m.end, m.title) for m in ms] == [
        (0, None, "Intro"),
        (192, None, "Samadhi"),
        (520, 720, "Resign to Surrender"),
        (3723, None, "Unleashed (bonus)"),
    ]


def test_parse_timestamps_rejects_unordered():
    assert parse_timestamps("0:00 One\n7:45 Two\n3:30 Three") == []


@pytest.mark.parametrize("text", ["", "no timestamps here", "5:00 only one"])
def test_parse_timestamps_needs_two_markers(text):
    assert parse_timestamps(text) == []


def test_parse_timestamps_rejects_late_start():
    assert parse_timestamps("2:00 One\n4:00 Two") == []


@pytest.mark.parametrize(
    "raw, out",
    [
        ("01. Samadhi", "Samadhi"),
        ('"Resign to Surrender"', "Resign to Surrender"),
        ("Track 4: Unleashed", "Unleashed"),
        ("Kingdom of Heaven (Part 1", "Kingdom of Heaven (Part 1)"),
    ],
)
def test_clean_title(raw, out):
    assert clean_title(raw) == out


def test_fill_ends():
    ms = fill_ends([Marker(0, "a"), Marker(60, "b", end=500), Marker(120, "c")], 300)
    assert [m.end for m in ms] == [60, 120, 300]


def test_find_markers_prefers_chapters():
    info = {
        "duration": 400,
        "chapters": [
            {"start_time": 0, "end_time": 200, "title": "One"},
            {"start_time": 200, "end_time": 400, "title": "Two"},
        ],
        "description": "0:00 X\n1:00 Y",
    }
    res = find_markers(info)
    assert res.source == "chapters" and [m.title for m in res.markers] == ["One", "Two"]


def test_find_markers_ignores_untitled_chapters():
    info = {
        "duration": 400,
        "chapters": [{"start_time": 0, "title": "<Untitled Chapter 1>"}, {"start_time": 200, "title": "Two"}],
        "description": "0:00 X\n1:00 Y",
    }
    assert find_markers(info).source == "description"
