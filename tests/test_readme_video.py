"""README demo video: embedded player at the top, fallback mp4 and subtitles present."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
PLAYER = re.compile(r"^https://github\.com/user-attachments/assets/[0-9a-f-]+$", re.M)


def test_embedded_player_near_top():
    m = PLAYER.search(README)
    assert m, "README has no embedded user-attachments video"
    assert m.start() < README.index("## ")


def test_no_duplicate_thumbnail_link():
    assert "skroutz_demo_thumbnail.png" not in README


def test_fallback_mp4_linked_exists_and_small():
    assert "(docs/video/skroutz_demo.mp4)" in README
    video = ROOT / "docs/video/skroutz_demo.mp4"
    assert video.is_file()
    assert video.stat().st_size < 15 * 1024 * 1024


def test_subtitles_linked_and_exist():
    assert "(docs/video/skroutz_demo.srt)" in README
    assert (ROOT / "docs/video/skroutz_demo.srt").is_file()
