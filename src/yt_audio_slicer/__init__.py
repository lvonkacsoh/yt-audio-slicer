"""Download YouTube videos / playlists as tagged MP3 tracks, and keep the music library tidy."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yt-audio-slicer")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0+unknown"

PROJECT_URL = "https://github.com/<your-github-user>/yt-audio-slicer"
