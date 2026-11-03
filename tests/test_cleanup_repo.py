"""README eval badge matches the eval range, and AI assistant files stay out of the repo."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_eval_badge_shows_score_range_from_table():
    scores = [int(s) for s in re.findall(r"^\| \d+ \w+ 2026 \|.*\| (\d+)/23 \|", README, re.M)]
    after_fix = scores[1:]  # first row is before the eval fix
    expected = f"ask_eval-{min(after_fix)}%E2%80%93{max(after_fix)}%2F23"
    assert expected in README


def test_eval_badge_matches_latest_result_sentence():
    assert "runs scored between 21/23 and 23/23" in README
    assert "ask_eval-21%E2%80%9323%2F23" in README


def test_ai_assistant_files_not_in_repo():
    assert not (ROOT / "CLAUDE.md").exists()
    assert not (ROOT / "AGENTS.md").exists()


def test_ai_assistant_files_gitignored():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "CLAUDE.md" in lines and "AGENTS.md" in lines


def test_no_references_to_removed_files():
    for p in ["README.md", "pyproject.toml", "docs/PIPELINE.md"]:
        text = (ROOT / p).read_text(encoding="utf-8")
        assert "AGENTS.md" not in text and "CLAUDE.md" not in text, p
