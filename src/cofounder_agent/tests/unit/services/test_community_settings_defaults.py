"""The community-draft settings ship as app_settings defaults (not migration seeds)."""
from __future__ import annotations

from services.settings_defaults import DEFAULTS


def test_community_draft_model_defaults_empty():
    # Empty => generate_reddit_draft falls to pipeline_writer_model.
    assert DEFAULTS["community_draft_model"] == ""


def test_community_draft_timeout_default():
    assert DEFAULTS["community_draft_timeout_seconds"] == "180"
