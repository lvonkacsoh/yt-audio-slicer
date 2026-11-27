import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2, TRCK

from yt_audio_slicer import tidy
from yt_audio_slicer.prune import empty_dirs

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def mp3(path: Path, title: str, number: int, total: int, artist: str = "Epica", album: str = "X") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.2",
            "-q:a",
            "9",
            str(path),
        ],
        check=True,
    )
    t = ID3()
    for frame in (
        TIT2(text=title),
        TPE1(text=artist),
        TPE2(text=artist),
        TALB(text=album),
        TRCK(text=f"{number}/{total}"),
    ):
        t.add(frame)
    t.save(path)


@pytest.fixture
def lib(tmp_path: Path) -> Path:
    r = tmp_path / "lib"
    mp3(r / "Epica/Design Your Universe/01 - Samadhi.mp3", "Samadhi", 1, 3)
    mp3(r / "EPICA/Design Your Universe/02 - Resign.mp3", "Resign", 2, 3, "EPICA")
    mp3(r / "Epica, Roy Khan/Design Your Universe/03 - Unleashed.mp3", "Unleashed", 3, 3, "Epica, Roy Khan")
    mp3(r / "EPICA/Design Your Universe/01 - Samadhi.mp3", "Samadhi", 1, 3, "EPICA")  # duplicate
    mp3(r / "Some Title (Part/Omega/04 - Some Title (Part 2).mp3", "Some Title (Part 2)", 4, 5)  # orphan
    mp3(r / "Epica/2021 - Omega/01 - Alpha.mp3", "Alpha", 1, 5)
    mp3(r / "Epica/2021 - Omega/02 - Abyss.mp3", "Abyss", 2, 5)
    (r / "Death of a Dream/The Divine Conspiracy").mkdir(parents=True)
    (r / "_old/keep").mkdir(parents=True)
    return r


@needs_ffmpeg
def test_tidy_merges_and_prunes(lib: Path):
    assert tidy.main([str(lib), "--yes", "--fix-tags"]) == 0
    dyu = lib / "Epica/Design Your Universe"
    assert sorted(p.name for p in dyu.iterdir()) == ["01 - Samadhi.mp3", "02 - Resign.mp3", "03 - Unleashed.mp3"]
    assert (lib / "EPICA/Design Your Universe/01 - Samadhi.mp3").exists()  # duplicate left in place
    assert (lib / "Epica/2021 - Omega/04 - Some Title (Part 2).mp3").exists()  # orphan merged
    assert not (lib / "Death of a Dream").exists() and not (lib / "Epica, Roy Khan").exists()
    assert (lib / "_old/keep").is_dir()  # excluded
    t = ID3(dyu / "02 - Resign.mp3")
    assert str(t["TPE1"]) == "Epica" and str(t["TPE2"]) == "Epica"
    # a second run has nothing left to do
    albums, _ = tidy.scan(lib, tidy.DEFAULT_EXCLUDE)
    plans = [p for g in tidy.group_albums(albums, True) if (p := tidy.plan_group(lib, g, "headline", False))]
    assert all(not p.moves for p in plans)


@needs_ffmpeg
def test_dry_run_changes_nothing(lib: Path):
    before = sorted(str(p) for p in lib.rglob("*"))
    assert tidy.main([str(lib)]) == 0
    assert sorted(str(p) for p in lib.rglob("*")) == before


def test_empty_dirs_nested(tmp_path: Path):
    (tmp_path / "a/b/c").mkdir(parents=True)
    (tmp_path / "d").mkdir()
    (tmp_path / "d/file").write_text("x")
    assert set(empty_dirs(tmp_path)) == {tmp_path / "a", tmp_path / "a/b", tmp_path / "a/b/c"}
