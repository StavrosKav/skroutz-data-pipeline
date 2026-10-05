"""
Smoke tests for pipeline wiring.

Guards against the 2026-07-09 incident class: a stage module that cannot even
be compiled or imported must fail the test suite, not the 10:00 scheduled run.
Also pins the observer-stage contract: a failing observer never aborts the
pipeline, a failing core stage always does.
"""

import importlib
import py_compile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import run_pipeline
from core.paths import ROOT


class TestStageScriptsCompile(unittest.TestCase):
    """Every module wired into STAGES must at least compile/import."""

    def test_all_stage_modules_importable(self):
        self.assertTrue(run_pipeline.STAGES, "STAGES is empty")
        for label, mod, _fatal in run_pipeline.STAGES:
            with self.subTest(stage=label):
                path = ROOT / Path(*mod.split(".")).with_suffix(".py")
                self.assertTrue(path.is_file(), f"missing module file: {path}")
                py_compile.compile(str(path), doraise=True)
                importlib.import_module(mod)

    def test_observer_modules_always_compile(self):
        for mod in ("ops.run_scraper_health", "ops.run_data_quality"):
            with self.subTest(module=mod):
                path = ROOT / Path(*mod.split(".")).with_suffix(".py")
                py_compile.compile(str(path), doraise=True)

    def test_agents_package_imports(self):
        import agents
        importlib.reload(agents)


class TestObserverStageContract(unittest.TestCase):
    """Observer stages warn and continue; fatal stages abort."""

    def _failing_run(self, *a, **kw):
        return MagicMock(returncode=1)

    def test_observer_failure_does_not_abort(self):
        with patch.object(run_pipeline.subprocess, "run", self._failing_run), \
             patch.object(run_pipeline._notif, "tg_send") as tg, \
             patch.object(run_pipeline, "send_failure_alert") as alert:
            run_pipeline.run_stage("Observer", "whatever.mod", fatal=False)
            tg.assert_called_once()
            alert.assert_not_called()

    def test_fatal_failure_aborts(self):
        with patch.object(run_pipeline.subprocess, "run", self._failing_run), \
             patch.object(run_pipeline, "send_failure_alert") as alert:
            with self.assertRaises(SystemExit):
                run_pipeline.run_stage("Core", "whatever.mod", fatal=True)
            alert.assert_called_once()

    def test_stage_table_marks_observers_non_fatal(self):
        fatality = {label: fatal for label, _script, fatal in run_pipeline.STAGES}
        for label, fatal in fatality.items():
            if "Monitor" in label or "Quality" in label:
                self.assertFalse(fatal, f"{label} must be an observer (fatal=False)")
            else:
                self.assertTrue(fatal, f"{label} must be fatal")


if __name__ == "__main__":
    unittest.main()
