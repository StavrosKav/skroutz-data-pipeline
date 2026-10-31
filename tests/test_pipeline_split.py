"""run_pipeline split: same stage order, same public names, behaviour unchanged."""
from unittest import mock

import run_pipeline as rp

POST_ORDER = ["Coverage check", "Matview refresh", "Charts", "Drop digest", "Watchlist alerts",
              "Disappeared alert", "Dashboard", "README stats", "Publish artifacts"]


def test_post_stage_order_is_frozen():
    assert [label for _, label in rp.post_stages()] == POST_ORDER


def test_core_stage_order_is_frozen():
    labels = [s[0] for s in rp.STAGES]
    assert labels[-3:] == ["Clean", "Data Quality Agent", "Load SQL"]


def test_moved_functions_are_re_exported():
    from pipeline import email_html, publish, readme_stats
    assert rp.publish_artifacts is publish.publish_artifacts
    assert rp.publish_pages_branch is publish.publish_pages_branch
    assert rp.PAGES_BRANCH == publish.PAGES_BRANCH == "gh-pages"
    assert rp._html_shell is email_html._html_shell and rp._pct_badge is email_html._pct_badge
    assert rp.update_readme_stats is readme_stats.update_readme_stats


def test_main_runs_core_then_post_stages_then_summary_in_order():
    calls = []
    fake_post = [(mock.Mock(side_effect=lambda lab=lab: calls.append(lab)), lab) for lab in POST_ORDER]
    fake_post[2] = (mock.Mock(side_effect=RuntimeError("boom")), "Charts")  # one failure must not stop the rest
    with mock.patch.object(rp, "_acquire_lock", return_value=True), \
         mock.patch.object(rp, "_release_lock") as rel, \
         mock.patch.object(rp, "_run_log_start"), mock.patch.object(rp, "_run_log_note"), \
         mock.patch.object(rp, "_run_log_finish") as fin, mock.patch.object(rp, "_cleanup_old_logs"), \
         mock.patch.object(rp, "_check_gap"), \
         mock.patch.object(rp._notif, "tg_pipeline_start"), mock.patch.object(rp._notif, "tg_send"), \
         mock.patch.object(rp, "run_stage", side_effect=lambda label, *a: calls.append(label)), \
         mock.patch.object(rp, "post_stages", return_value=fake_post), \
         mock.patch.object(rp, "send_success_summary", side_effect=lambda e: calls.append("summary")):
        rp.main()
    core = [s[0] for s in rp.STAGES]
    assert calls == core + [x for x in POST_ORDER if x != "Charts"] + ["summary"]
    fin.assert_called_with("success")
    rel.assert_called_once()


def test_main_exits_quietly_when_locked():
    with mock.patch.object(rp, "_acquire_lock", return_value=False), \
         mock.patch.object(rp._notif, "tg_send") as send, mock.patch.object(rp, "run_stage") as rs:
        try:
            rp.main()
        except SystemExit as e:
            assert e.code == 0
    rs.assert_not_called()
    send.assert_called_once()
