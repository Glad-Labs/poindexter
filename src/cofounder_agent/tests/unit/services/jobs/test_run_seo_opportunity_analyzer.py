"""Unit tests for the SEO opportunity analyzer job wiring (no DB)."""

from __future__ import annotations

from services.jobs.run_seo_opportunity_analyzer import (
    RunSeoOpportunityAnalyzerJob,
    _thresholds,
)


def test_job_has_required_attrs():
    job = RunSeoOpportunityAnalyzerJob()
    assert job.name == "run_seo_opportunity_analyzer"
    assert isinstance(job.schedule, str) and job.schedule
    assert job.idempotent is True


def test_job_registered_in_core_samples():
    # get_core_samples() instantiates every registered core-sample plugin, so
    # this also proves the job class imports and constructs cleanly.
    from plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(
        getattr(j, "name", None) == "run_seo_opportunity_analyzer" for j in jobs
    )


def test_thresholds_default_when_no_site_config():
    th = _thresholds(None)
    assert th["striking_position_max"] == 20.0
    assert th["push_position_max"] == 10.0


def test_thresholds_from_site_config_override_one_key():
    class _SC:
        def get_float(self, key, default):
            return {"seo.striking_distance.position_max": 18.0}.get(key, default)

    th = _thresholds(_SC())
    assert th["striking_position_max"] == 18.0  # overridden
    assert th["push_position_max"] == 10.0  # falls back to default
