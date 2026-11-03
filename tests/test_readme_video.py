"""README demo video: thumbnail and video links must point to real, small files."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
LINK = re.compile(
    r"\[!\[[^\]]*\]\((docs/video/[^)]+\.png)\)\]\((docs/video/[^)]+\.mp4)\)"
)


def _match():
    m = LINK.search(README)
    assert m, "README has no clickable demo video thumbnail"
    return m


def test_thumbnail_exists():
    assert (ROOT / _match().group(1)).is_file()


def test_video_exists_and_small():
    video = ROOT / _match().group(2)
    assert video.is_file()
    assert video.stat().st_size < 15 * 1024 * 1024


def test_video_near_top_of_readme():
    assert README.index("docs/video/") < README.index("## ")
