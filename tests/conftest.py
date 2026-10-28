"""Shared pytest fixtures for the whole suite."""
import pytest


@pytest.fixture(autouse=True)
def _no_pipeline_date(monkeypatch):
    """Tests must not depend on a PIPELINE_DATE left in the caller's shell."""
    monkeypatch.delenv("PIPELINE_DATE", raising=False)
