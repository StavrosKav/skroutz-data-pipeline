"""A live pipeline run must never look stale: the lock window has to exceed the worst-case runtime."""
import run_pipeline
from etl import clean_all
from scrapers import run_all

N_CLEANERS = 4  # phones, laptops, smartwatches, tablets; clean_all waits on each in turn


def test_stage_ceilings_are_named_constants():
    assert run_all.SCRAPE_STAGE_TIMEOUT == 7200
    assert clean_all.CLEAN_TIMEOUT == 1800


def test_lock_outlives_worst_case_run():
    worst = run_all.SCRAPE_STAGE_TIMEOUT + N_CLEANERS * clean_all.CLEAN_TIMEOUT
    assert run_pipeline._LOCK_STALE_SECONDS >= worst + 3600   # 1h margin for load, reports, publish


def test_lock_is_reclaimed_before_next_daily_run():
    assert run_pipeline._LOCK_STALE_SECONDS < 20 * 3600
