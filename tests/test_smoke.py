"""
Smoke tests for pipeline wiring.

Guards against the 2026-07-09 incident class: a stage script that cannot even
be compiled or imported must fail the test suite, not the 10:00 scheduled run.
Also pins the observer-stage contract: a failing observer never aborts the
pipeline, a failing core stage always does.
"""

import os
import py_compile
import sys
import unittest
from unittest.mock import MagicMock, patch

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import run_pipeline


class TestStageScriptsCompile(unittest.TestCase):
    """Every script wired into STAGES must at least compile."""

    def test_all_stage_scripts_compile(self):
        self.assertTrue(run_pipeline.STAGES, "STAGES is empty")
        for label, script, _fatal in run_pipeline.STAGES:
            with self.subTest(stage=label):
                self.assertTrue(os.path.exists(script), f"missing script: {script}")
                py_compile.compile(script, doraise=True)

    def test_observer_scripts_always_compile(self):
        # SKIP_SCRAPE=1 drops the health monitor from STAGES, so check the
        # observer scripts explicitly regardless of how STAGES was built.
        for name in ("run_scraper_health_monitor.py", "run_data_quality_agent.py"):
            with self.subTest(script=name):
                py_compile.compile(os.path.join(BASE, name), doraise=True)

    def test_agents_package_imports(self):
        # The health monitor died on an agents import chain once; keep it importable.
        import importlib
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
            run_pipeline.run_stage("Observer", "whatever.py", fatal=False)
            tg.assert_called_once()
            alert.assert_not_called()

    def test_fatal_failure_aborts(self):
        with patch.object(run_pipeline.subprocess, "run", self._failing_run), \
             patch.object(run_pipeline, "send_failure_alert") as alert:
            with self.assertRaises(SystemExit):
                run_pipeline.run_stage("Core", "whatever.py", fatal=True)
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
