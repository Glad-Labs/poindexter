"""Unit tests for ``services/jobs/niche_job_flags.py`` (Glad-Labs/poindexter#521).

Pins the per-niche scheduler-job enable/disable convention:

- key format ``niche.<slug>.jobs.<job_name>.enabled``
- absent row → fail-safe default ``True`` (enabled)
- explicit ``false`` → disabled
- per-niche AND per-job independence (toggling one niche/job leaves the
  others untouched)
- the global master switch ``plugin.job.<job_name>.enabled`` lives on a
  DIFFERENT key, so the two never collide
"""

from __future__ import annotations

import pytest

from services.jobs.niche_job_flags import (
    NICHE_JOB_ENABLED_KEY,
    niche_job_enabled,
    niche_job_key,
)
from services.site_config import SiteConfig


@pytest.mark.unit
class TestNicheJobKey:
    def test_key_format(self):
        assert niche_job_key("dev_diary", "backfill_podcasts") == (
            "niche.dev_diary.jobs.backfill_podcasts.enabled"
        )

    def test_template_matches_helper(self):
        assert NICHE_JOB_ENABLED_KEY.format(
            slug="gaming", job_name="backfill_videos"
        ) == niche_job_key("gaming", "backfill_videos")

    def test_per_niche_keys_distinct(self):
        assert niche_job_key("a", "backfill_videos") != niche_job_key(
            "b", "backfill_videos"
        )

    def test_per_job_keys_distinct(self):
        assert niche_job_key("dev_diary", "backfill_videos") != niche_job_key(
            "dev_diary", "backfill_podcasts"
        )


@pytest.mark.unit
class TestNicheJobEnabled:
    def test_absent_setting_defaults_enabled(self):
        # Empty config — the row simply isn't there.
        sc = SiteConfig(initial_config={})
        assert niche_job_enabled(sc, "dev_diary", "backfill_podcasts") is True

    def test_explicit_false_disables(self):
        sc = SiteConfig(initial_config={
            "niche.dev_diary.jobs.backfill_podcasts.enabled": "false",
        })
        assert niche_job_enabled(sc, "dev_diary", "backfill_podcasts") is False

    def test_explicit_true_enabled(self):
        sc = SiteConfig(initial_config={
            "niche.dev_diary.jobs.backfill_podcasts.enabled": "true",
        })
        assert niche_job_enabled(sc, "dev_diary", "backfill_podcasts") is True

    def test_none_site_config_uses_default(self):
        assert niche_job_enabled(None, "dev_diary", "backfill_videos") is True
        assert (
            niche_job_enabled(None, "dev_diary", "backfill_videos", default=False)
            is False
        )

    def test_podcast_off_video_on_same_niche(self):
        """Acceptance case: toggle podcasts OFF for dev_diary while videos
        stay ON — a single app_settings row each, fully independent."""
        sc = SiteConfig(initial_config={
            "niche.dev_diary.jobs.backfill_podcasts.enabled": "false",
            # backfill_videos has NO row → defaults enabled
        })
        assert niche_job_enabled(sc, "dev_diary", "backfill_podcasts") is False
        assert niche_job_enabled(sc, "dev_diary", "backfill_videos") is True

    def test_one_niche_off_other_niche_on(self):
        sc = SiteConfig(initial_config={
            "niche.dev_diary.jobs.backfill_podcasts.enabled": "false",
        })
        assert niche_job_enabled(sc, "dev_diary", "backfill_podcasts") is False
        # A different niche with no row → still enabled.
        assert niche_job_enabled(sc, "gaming", "backfill_podcasts") is True

    def test_does_not_collide_with_global_master_switch(self):
        """The per-niche key and the global ``plugin.job.<job>.enabled``
        master switch are different rows. Setting the per-niche flag must
        not be readable as the master switch and vice-versa."""
        sc = SiteConfig(initial_config={
            "plugin.job.backfill_podcasts.enabled": "false",  # global OFF
            "niche.dev_diary.jobs.backfill_podcasts.enabled": "true",  # niche ON
        })
        # The helper only reads the per-niche key — the global master
        # switch (enforced by the scheduler) is invisible to it.
        assert niche_job_enabled(sc, "dev_diary", "backfill_podcasts") is True
        # And the master-switch row is reachable on its own distinct key.
        assert sc.get_bool("plugin.job.backfill_podcasts.enabled", True) is False
