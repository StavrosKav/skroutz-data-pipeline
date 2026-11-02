"""Publish the site to gh-pages and README stats to main (moved from run_pipeline.py)."""
import datetime
import html
import logging
import os
import subprocess
import tempfile

import alerts.notifications as _notif
from core.paths import ROOT

logger = logging.getLogger(__name__)
BASE = str(ROOT)


# Files the public GitHub Pages site needs. Published to the `gh-pages` branch
# as ONE parentless commit, force-pushed daily, so the ~2.5 MB dashboard and
# chart PNGs never accumulate in history. Paths are unchanged, so the public
# URL .../dashboard/dashboard_latest.html stays the same.
PAGES_BRANCH = "gh-pages"


PAGES_PATHS = ("index.html", "dashboard/dashboard_latest.html", "charts", "assets")


# GitHub Pages serves from the gh-pages branch (since 29 Oct 2026), so by default
# main only gets the small README stats commit. Set PUBLISH_MAIN_ARTIFACTS=1 to
# also commit dashboard + charts to main (old behaviour). If the gh-pages push
# fails, publish_artifacts still falls back to committing them to main.
def _publish_main_artifacts():
    return os.getenv("PUBLISH_MAIN_ARTIFACTS", "0").strip() == "1"


def _git_run(*args, timeout=120, env=None):
    return subprocess.run(
        ["git", "-C", BASE, *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


def publish_pages_branch(git=None):
    """
    Build a single parentless commit holding only PAGES_PATHS (plus .nojekyll)
    using a throwaway index — the working tree, HEAD and the real index are
    never touched — then force-push it to origin/gh-pages. Returns True on success.
    """
    git = git or _git_run
    present = [p for p in PAGES_PATHS if os.path.exists(os.path.join(BASE, p))]
    if "dashboard/dashboard_latest.html" not in present:
        logger.warning("Publish pages: dashboard_latest.html missing — skipping gh-pages publish.")
        return False
    fd, tmp_index = tempfile.mkstemp(prefix="pages_index_")
    os.close(fd)
    os.remove(tmp_index)  # git wants to create it itself
    env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
    try:
        for args in (("read-tree", "--empty"), ("add", "-f", "--", *present)):
            r = git(*args, env=env)
            if r.returncode != 0:
                logger.warning(f"Publish pages: git {args[0]} failed — {r.stderr.strip()}")
                return False
        # .nojekyll: serve files as-is (no Jekyll build step on Pages)
        fd, empty = tempfile.mkstemp(prefix="nojekyll_")
        os.close(fd)
        try:
            blob = git("hash-object", "-w", empty, env=env)
        finally:
            os.remove(empty)
        if blob.returncode == 0 and blob.stdout.strip():
            git("update-index", "--add", "--cacheinfo", f"100644,{blob.stdout.strip()},.nojekyll", env=env)
        tree = git("write-tree", env=env)
        if tree.returncode != 0:
            logger.warning(f"Publish pages: write-tree failed — {tree.stderr.strip()}")
            return False
        msg = f"site: dashboard + charts from {datetime.date.today()} run"
        commit = git("commit-tree", tree.stdout.strip(), "-m", msg, env=env)
        if commit.returncode != 0:
            logger.warning(f"Publish pages: commit-tree failed — {commit.stderr.strip()}")
            return False
        sha = commit.stdout.strip()
        push = git("push", "--force", "origin", f"{sha}:refs/heads/{PAGES_BRANCH}", timeout=300)
        if push.returncode != 0:
            logger.warning(f"Publish pages: push to {PAGES_BRANCH} failed — {push.stderr.strip()}")
            return False
        logger.info(f"Publish pages: force-pushed {sha[:7]} to origin/{PAGES_BRANCH} (history = 1 commit)")
        return True
    finally:
        if os.path.exists(tmp_index):
            os.remove(tmp_index)


def publish_artifacts(git=None):
    """
    1. Publish site files to the gh-pages branch (single commit, force-pushed).
    2. Commit to main: README.md stats always; dashboard + charts only while
       PUBLISH_MAIN_ARTIFACTS != "0" (transition period). Push origin main.
    Non-fatal — pipeline result is unaffected if this fails. If the index
    already holds unrelated staged changes, the main commit is skipped.
    """
    logger.info("=== Publish artifacts started ===")
    t = datetime.datetime.now()
    _git = git or _git_run

    pages_ok = publish_pages_branch(_git)

    if _git("diff", "--cached", "--quiet").returncode != 0:
        logger.warning("Publish artifacts: index already has staged changes — skipping auto-commit.")
        return
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        logger.warning(f"Publish artifacts: on branch '{branch}', not main — skipping auto-commit.")
        return
    paths = ["README.md"]
    add_args = ("add", "--")
    if not pages_ok:
        # Site files are gitignored on main (31 Oct 2026); main is not served,
        # so there is nothing to fall back to. README stats still go to main.
        _notif.tg_send("⚠️ <b>gh-pages publish failed</b>\nThe public dashboard was not updated today.")
    if _publish_main_artifacts():
        # Legacy opt-in: force-add, because these paths are gitignored.
        paths = ["charts/price_trend_phone.png", "charts/price_trend_laptop.png",
                 "charts/price_trend_smartwatch.png", "charts/price_trend_tablet.png",
                 "dashboard/dashboard_latest.html", "README.md"]
        add_args = ("add", "-f", "--")
    add = _git(*add_args, *paths)
    if add.returncode != 0:
        logger.warning(f"Publish artifacts: git add failed — {add.stderr.strip()}")
        return
    if _git("diff", "--cached", "--quiet").returncode == 0:
        logger.info("Publish artifacts: no main changes to commit.")
        return
    what = "charts, dashboard and README stats" if len(paths) > 1 else "README stats"
    msg = f"chore: refresh {what} from {datetime.date.today()} run"
    commit = _git("commit", "-m", msg)
    if commit.returncode != 0:
        logger.warning(f"Publish artifacts: git commit failed — {commit.stderr.strip()}")
        return
    push = _git("push", "origin", "main", timeout=300)
    elapsed = (datetime.datetime.now() - t).total_seconds()
    if push.returncode != 0:
        logger.warning(f"Publish artifacts: git push failed after {elapsed:.0f}s — {push.stderr.strip()}")
        _notif.tg_send(
            "⚠️ <b>Artifact push failed</b>\n"
            "Committed locally but not pushed to main.\n"
            f"<code>{html.escape(push.stderr.strip()[:300])}</code>"
        )
        return
    logger.info(f"=== Publish artifacts complete in {elapsed:.0f}s — pushed to origin/main ===")
