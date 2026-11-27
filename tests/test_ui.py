from pathlib import Path

import pytest

from yt_audio_slicer.ui import fmt_bytes, fmt_time, ranges, rel


@pytest.mark.parametrize(
    "nums, out",
    [
        ([1, 2, 3, 5, 7, 8], "1-3, 5, 7-8"),
        ([4], "4"),
        ([], ""),
        ([3, 1, 2, 2], "1-3"),
    ],
)
def test_ranges(nums, out):
    assert ranges(nums) == out


@pytest.mark.parametrize("s, out", [(None, "-"), (59.6, "1:00"), (3725, "1:02:05")])
def test_fmt_time(s, out):
    assert fmt_time(s) == out


def test_fmt_bytes():
    assert fmt_bytes(512) == "512.0 B"
    assert fmt_bytes(1536) == "1.5 KB"


def test_rel():
    assert rel(Path("/a/b/c"), Path("/a")) == "b/c"
    assert rel(Path("/x/y"), Path("/a")) == "/x/y"
