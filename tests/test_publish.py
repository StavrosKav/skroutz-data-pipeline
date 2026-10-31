"""Publish logic: gh-pages single-commit site + small main commit."""
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import run_pipeline as rp
import pipeline.publish as pub


def _rm_readonly(func, path, _exc):
    # Windows: git object files are read-only; clear the flag and retry.
    os.chmod(path, 0o700)
    func(path)



def _ok(out=""):
    return SimpleNamespace(returncode=0, stdout=out, stderr="")


class FakeGit:
    """Records git calls; `fail` maps a subcommand to a failing result."""

    def __init__(self, staged_before=False, staged_after=True, branch="main", fail=()):
        self.calls, self.fail = [], set(fail)
        self.staged_before, self.staged_after, self.branch = staged_before, staged_after, branch
        self._diffs = 0

    def __call__(self, *args, timeout=120, env=None):
        self.calls.append(args)
        if args[0] in self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        if args[:2] == ("diff", "--cached"):
            self._diffs += 1
            staged = self.staged_before if self._diffs == 1 else self.staged_after
            return SimpleNamespace(returncode=1 if staged else 0, stdout="", stderr="")
        if args[0] == "rev-parse":
            return _ok(self.branch + "\n")
        if args[0] in ("write-tree", "commit-tree", "hash-object"):
            return _ok("abc1234deadbeef\n")
        return _ok()

    def adds(self):
        return [c for c in self.calls if c[0] == "add" and "-f" not in c]

    def pushes(self):
        return [c for c in self.calls if c[0] == "push"]


class TestPublishArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "dashboard"))
        open(os.path.join(self.tmp, "dashboard", "dashboard_latest.html"), "w").close()
        self.base = patch.object(pub, "BASE", self.tmp)
        self.base.start()

    def tearDown(self):
        self.base.stop()
        shutil.rmtree(self.tmp)

    def _run(self, env_val, **kw):
        g = FakeGit(**kw)
        with patch.dict(os.environ, {"PUBLISH_MAIN_ARTIFACTS": env_val}), \
             patch.object(rp._notif, "tg_send"):
            rp.publish_artifacts(git=g)
        return g

    def test_gh_pages_force_pushed_as_parentless_commit(self):
        g = self._run("0")
        ct = [c for c in g.calls if c[0] == "commit-tree"][0]
        self.assertNotIn("-p", ct)
        self.assertIn(("push", "--force", "origin", "abc1234deadbeef:refs/heads/gh-pages"), g.pushes())

    def test_after_switch_main_gets_only_readme(self):
        g = self._run("0")
        self.assertEqual(g.adds(), [("add", "--", "README.md")])
        self.assertIn(("push", "origin", "main"), g.pushes())

    def test_opt_in_still_commits_site_to_main(self):
        # Site files are gitignored on main, so the opt-in path must force-add them.
        g = self._run("1")
        forced = [c for c in g.calls if c[:2] == ("add", "-f") and "README.md" in c]
        self.assertIn("dashboard/dashboard_latest.html", forced[0])

    def test_default_is_gh_pages_only(self):
        # Pages serves from gh-pages since 29 Oct 2026, so main gets only README stats.
        env = {k: v for k, v in os.environ.items() if k != "PUBLISH_MAIN_ARTIFACTS"}
        g = FakeGit()
        with patch.dict(os.environ, env, clear=True), patch.object(rp._notif, "tg_send"):
            rp.publish_artifacts(git=g)
        self.assertEqual(g.adds(), [("add", "--", "README.md")])

    def test_pages_failure_keeps_main_readme_only(self):
        # main no longer serves Pages and the site files are gitignored there,
        # so a gh-pages failure must not re-track them on main.
        g = self._run("0", fail={"write-tree"})
        self.assertEqual(g.adds(), [("add", "--", "README.md")])

    def test_not_on_main_skips_main_commit(self):
        g = self._run("0", branch="feature")
        self.assertEqual(g.adds(), [])
        self.assertNotIn(("push", "origin", "main"), g.pushes())

    def test_preexisting_staged_changes_skip_main_commit(self):
        g = self._run("0", staged_before=True)
        self.assertEqual(g.adds(), [])

    def test_missing_dashboard_skips_pages(self):
        os.remove(os.path.join(self.tmp, "dashboard", "dashboard_latest.html"))
        g = self._run("0")
        self.assertFalse([c for c in g.calls if c[0] == "commit-tree"])


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestPublishPagesRealGit(unittest.TestCase):
    """End-to-end against a throwaway local bare remote."""

    def _g(self, *a, cwd=None):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def test_two_runs_keep_single_commit_and_leave_worktree_alone(self):
        tmp = tempfile.mkdtemp()
        try:
            remote, work = os.path.join(tmp, "r.git"), os.path.join(tmp, "w")
            self._g("init", "--bare", "-q", remote)
            self._g("init", "-q", "-b", "main", work)
            for k, v in (("user.email", "t@t"), ("user.name", "t")):
                self._g("config", k, v, cwd=work)
            self._g("remote", "add", "origin", remote, cwd=work)
            os.makedirs(os.path.join(work, "dashboard"))
            os.makedirs(os.path.join(work, "charts"))
            html = os.path.join(work, "dashboard", "dashboard_latest.html")
            for p, s in ((html, "v1"), (os.path.join(work, "charts", "a.png"), "png"),
                         (os.path.join(work, "other.py"), "x")):
                with open(p, "w") as f:
                    f.write(s)
            with patch.object(pub, "BASE", work):
                self.assertTrue(rp.publish_pages_branch())
                with open(html, "w") as f:
                    f.write("v2")
                self.assertTrue(rp.publish_pages_branch())
            self.assertEqual(self._g("rev-list", "--count", "gh-pages", cwd=remote), "1")
            files = set(self._g("ls-tree", "-r", "--name-only", "gh-pages", cwd=remote).split())
            self.assertEqual(files, {".nojekyll", "charts/a.png", "dashboard/dashboard_latest.html"})
            self.assertEqual(self._g("show", "gh-pages:dashboard/dashboard_latest.html", cwd=remote), "v2")
            self.assertEqual(self._g("status", "--porcelain", cwd=work).count("A "), 0)  # real index untouched
        finally:
            shutil.rmtree(tmp, onexc=_rm_readonly)
