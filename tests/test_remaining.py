from pathlib import Path

from yt_audio_slicer.urlfile import UrlFile, UrlProgress, parse_url_file, remaining_path, write_remaining
from yt_audio_slicer.urls import url_key

PL = "https://www.youtube.com/playlist?list=OLAK5uy_abc"
SINGLE = "https://youtu.be/bYA3MUO9zKw"


def test_failed_videos_become_video_links(tmp_path: Path):
    f = tmp_path / "queue.txt"
    f.write_text(f"# album\n{PL}\n{SINGLE}\n")
    uf = UrlFile(f, f.read_text().splitlines())
    prog = UrlProgress(
        PL,
        done={"a", "b"},
        total=3,
        playlist_id="OLAK5uy_abc",
        finished=True,
        failed={"c": "HTTP 403 Forbidden - [3/3] Song"},
    )
    write_remaining([uf], {url_key(PL): prog})
    out = remaining_path(f).read_text().splitlines()
    assert out == [
        "# album",
        "# failed: HTTP 403 Forbidden - [3/3] Song",
        "https://www.youtube.com/watch?v=c&list=OLAK5uy_abc",
    ]


def test_interrupted_playlist_gets_continue_line(tmp_path: Path):
    f = tmp_path / "q.remaining.txt"
    f.write_text(f"{PL}\n")
    uf = UrlFile(f, f.read_text().splitlines())
    prog = UrlProgress(PL, done={"a"}, total=3, playlist_id="OLAK5uy_abc")
    write_remaining([uf], {url_key(PL): prog})
    assert remaining_path(f) == f
    lines = f.read_text().splitlines()
    assert lines[0].startswith("# continue: skip=a ")
    assert parse_url_file(UrlFile(f, lines)) == [(PL, {"a"})]
