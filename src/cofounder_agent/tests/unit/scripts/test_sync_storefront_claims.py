"""Unit tests for the gladlabs.ai half of ``scripts/sync_claude_md_db_stats.py``.

Why this file exists: on 2026-09-16 the storefront's Pro pitch read "950+
live-tuned settings" while the README — same repo, same day — correctly said
"1,800+". The README was in the nightly DB-stat sync and the storefront was
not, so one public surface stayed true and the other silently halved. These
tests pin the storefront into that sync and, more importantly, pin the two
properties that make a sync trustworthy rather than merely present:

1. a claim that drifts is REWRITTEN, and
2. a claim whose wording changed WARNS instead of passing as "already correct"
   — the #2832 failure mode, where a reworded sentence froze a count and the
   job kept reporting success.
"""
from __future__ import annotations

import sys
from collections import OrderedDict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load(script_rel: str, name: str):
    script = _repo_root() / script_rel
    spec = spec_from_file_location(name, script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SYNC = _load("scripts/sync_claude_md_db_stats.py", "sync_db_stats_storefront_t")

# 1,843 floors to "1,800+" at the app_settings step of 100.
STATS: OrderedDict[str, int] = OrderedDict(
    [("live_posts", 207), ("total_posts", 381), ("pipeline_tasks", 2118),
     ("app_settings", 1843), ("app_settings_secret", 70), ("embeddings", 83421)]
)

PAGE_STALE = """              <p className="sf-card__body">
                The 950+ live-tuned settings that run Matt&apos;s content
                business — exported every week.
              </p>"""

GUIDE_STALE = """              <span>
                <strong>THE LIVE-TUNED CONFIG SEED</strong> — 950+ production
                values from the running business.
              </span>"""


@pytest.mark.unit
class TestStorefrontSync:
    def test_stale_page_claim_is_rewritten(self):
        out, changes = SYNC.apply_to_storefront(STATS, "page", text=PAGE_STALE)
        assert "The 1,800+ live-tuned settings" in out
        assert "950+" not in out
        assert changes and not any(SYNC.is_warning(c) for c in changes)

    def test_stale_guide_claim_is_rewritten(self):
        out, changes = SYNC.apply_to_storefront(STATS, "guide", text=GUIDE_STALE)
        assert "— 1,800+ production" in out
        assert "950+" not in out
        assert changes and not any(SYNC.is_warning(c) for c in changes)

    def test_already_correct_is_a_no_op(self):
        """The nightly must be silent on the ~99% of nights nothing crossed a
        flooring step, or the signal is worthless."""
        fresh = PAGE_STALE.replace("950+", "1,800+")
        out, changes = SYNC.apply_to_storefront(STATS, "page", text=fresh)
        assert out == fresh
        assert not changes

    def test_reworded_anchor_WARNS_rather_than_passing_silently(self):
        """The failure this whole mechanism exists to prevent. A dead anchor
        must never read as 'already in sync' — that is how the README's test
        count froze for two months while the job reported success."""
        reworded = PAGE_STALE.replace(
            "The 950+ live-tuned settings", "The many carefully-tuned settings"
        )
        out, changes = SYNC.apply_to_storefront(STATS, "page", text=reworded)
        assert out == reworded, "must not rewrite what it could not match"
        assert changes, "a dead anchor must produce output, not silence"
        assert any(SYNC.is_warning(c) for c in changes)

    def test_warning_names_the_real_file_not_readme(self):
        """substitute_anchored defaulted to saying 'README.md wording changed'.
        Pointed at page.js that sends the reader to the wrong file, which is
        the whole cost of a warning — it is only useful if it is actionable."""
        reworded = PAGE_STALE.replace(
            "The 950+ live-tuned settings", "The many carefully-tuned settings"
        )
        _, changes = SYNC.apply_to_storefront(STATS, "page", text=reworded)
        warning = next(c for c in changes if SYNC.is_warning(c))
        assert "page.js" in warning
        assert "README.md" not in warning

    def test_storefront_and_readme_cannot_disagree(self):
        """Both surfaces floor the same stat with the same step, so they can
        never differ by more than a rounding — the exact failure observed
        (README 1,800+ vs site 950+ on the same day)."""
        page, _ = SYNC.apply_to_storefront(STATS, "page", text=PAGE_STALE)
        readme_claim = SYNC.floored(
            STATS["app_settings"], SYNC.FLOOR_STEPS["app_settings"]
        )
        assert readme_claim in page
