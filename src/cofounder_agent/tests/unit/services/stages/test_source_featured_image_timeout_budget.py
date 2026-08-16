"""The featured-image stage's timeout budget must contain its own steps.

This file exists because the same defect has now appeared twice from opposite
ends, and both times it presented as a *clean* run that silently lost the hero:

* 2026-07-31 — a hardcoded 300s node timeout against a 483s configured render
  budget discarded an image that had already rendered AND uploaded, two seconds
  before the stage returned it. Fixed by deriving the node timeout from the
  settings (``resolve_stage_timeout_seconds``).
* poindexter#3229 follow-up — the derived value was still wrong, because the
  overhead term (120s) was smaller than the prompt-build call it is named for
  (``image_prompt_timeout_seconds``, and ~197s measured in production).

So the invariant is not "the numbers are currently X"; it is that the budget
dominates the steps inside it. Asserting the relationship rather than the
literals means a future retune cannot reintroduce the bug by moving one key.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from modules.content.stages.source_featured_image import (
    DEFAULT_STAGE_OVERHEAD_SECONDS,
    resolve_stage_timeout_seconds,
)
from services.settings_defaults import DEFAULTS


def _site_config(**overrides):
    settings = {
        "image_gen_render_attempts": 2,
        "image_gen_retry_backoff_seconds": 3,
        "image_render_timeout_seconds": 300,
        "image_featured_stage_overhead_seconds": DEFAULT_STAGE_OVERHEAD_SECONDS,
        "image_prompt_timeout_seconds": 240,
    }
    settings.update(overrides)
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda k, d=None: settings.get(k, d))
    sc.get_int = MagicMock(side_effect=lambda k, d=0: int(settings.get(k, d)))
    sc.get_float = MagicMock(side_effect=lambda k, d=0.0: float(settings.get(k, d)))
    return sc


class TestShippedDefaultsAreSelfConsistent:
    def test_overhead_covers_the_prompt_build_it_is_named_for(self):
        """The prompt build is the largest item the overhead term covers."""
        overhead = int(DEFAULTS["image_featured_stage_overhead_seconds"])
        prompt_timeout = int(DEFAULTS["image_prompt_timeout_seconds"])
        assert overhead >= prompt_timeout, (
            f"image_featured_stage_overhead_seconds ({overhead}) must be >= "
            f"image_prompt_timeout_seconds ({prompt_timeout}) — the overhead "
            "exists to cover the prompt-build call plus the GPU-lock wait and "
            "the R2 upload, so it cannot be smaller than the prompt build alone."
        )

    def test_code_fallback_matches_the_shipped_default(self):
        """A drifting literal is how the 2026-07-31 RCA started."""
        assert DEFAULT_STAGE_OVERHEAD_SECONDS == int(
            DEFAULTS["image_featured_stage_overhead_seconds"]
        )

    def test_token_cap_is_large_enough_for_a_finished_reply(self):
        """Measured: the prompt model is still mid-plan below ~768 tokens.

        Anything smaller guarantees a truncated scratchpad, which is the
        condition services.image_prompt_sanitizer exists to clean up after —
        it should be the exception, not every run.
        """
        assert int(DEFAULTS["image_prompt_max_tokens"]) >= 768


class TestDerivedNodeTimeout:
    def test_contains_every_render_attempt_plus_overhead(self):
        budget = resolve_stage_timeout_seconds(_site_config())
        assert budget == 2 * 300 + 1 * 3 + DEFAULT_STAGE_OVERHEAD_SECONDS

    def test_contains_the_prompt_build_on_top_of_a_full_render_budget(self):
        """The worst realistic run: every attempt burned, then the prompt build."""
        cfg = _site_config()
        budget = resolve_stage_timeout_seconds(cfg)
        renders = 2 * 300 + 1 * 3
        assert budget - renders >= 240, (
            "the node timeout must leave room for the prompt build after the "
            "render attempts, or the wrapper kills a stage that is still working"
        )

    @pytest.mark.parametrize("attempts,render", [(1, 60), (3, 300), (5, 120)])
    def test_scales_with_configured_attempts(self, attempts, render):
        cfg = _site_config(
            image_gen_render_attempts=attempts, image_render_timeout_seconds=render,
        )
        expected = attempts * render + (attempts - 1) * 3 + DEFAULT_STAGE_OVERHEAD_SECONDS
        assert resolve_stage_timeout_seconds(cfg) == expected
