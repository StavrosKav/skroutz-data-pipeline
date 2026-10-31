"""Daily generated files must not be tracked on main (2026-10-31: the run left
charts/*.png and dashboard_latest.html modified every day)."""
import shutil
import subprocess
import unittest

from core.paths import ROOT

GENERATED = [
    "charts/price_trend_phone.png", "charts/price_trend_laptop.png",
    "charts/price_trend_smartwatch.png", "charts/price_trend_tablet.png",
    "dashboard/dashboard_latest.html",
]


def _git(*a):
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True)


@unittest.skipUnless(shutil.which("git") and _git("rev-parse").returncode == 0, "needs git repo")
class GeneratedFilesUntracked(unittest.TestCase):
    def test_generated_files_are_gitignored(self):
        for p in GENERATED:
            self.assertEqual(_git("check-ignore", "-q", "--no-index", p).returncode, 0, p)

    def test_generated_files_not_in_index(self):
        tracked = _git("ls-files", "--", *GENERATED).stdout.split()
        self.assertEqual(tracked, [])


if __name__ == "__main__":
    unittest.main()
