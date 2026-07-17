"""Unit tests — source_featured_image best-effort-failure visibility.

Silent-excepts OLD-debt burn-down (batch 3). This stage runs for every post's
featured image. Of the five flagged silent handlers, two silently changed
behaviour and now emit a non-paging ``info`` finding while still returning their
fallback:

* ``_load_styles_from_settings`` — a JSON typo in the operator's ``image_styles``
  setting made the whole custom-style list vanish silently (``[]``), so the
  operator's configuration was ignored with no signal.
* ``_load_recent_published_styles`` — when BOTH the pooled and the raw-connect
  reads fail, cross-post style rotation silently dies and the same style recurs
  across posts (the exact regression this function's own docstring records being
  fixed once already).

The other three are documented ``# silent-ok``: the fire-and-forget
``image_style_picked`` audit row (the style is already applied), the
``create_task`` no-running-loop guard (tests/bootstrap), and the pooled-read
failure that deliberately FALLS THROUGH to the raw-connect path (whose own
failure is the finding above).

Mirrors ``tests/unit/modules/content/test_writer_core_silent_failures.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import modules.content.stages.source_featured_image as sfi
from services.site_config import SiteConfig

pytestmark = pytest.mark.unit


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


# --- _load_styles_from_settings: malformed image_styles JSON ---------------


def test_load_styles_from_settings_valid_json_parses():
    sc = SiteConfig(initial_config={
        "image_styles": '[{"scene": "watercolor", "tags": "soft, wash"}]',
    })
    assert sfi._load_styles_from_settings(sc) == [("watercolor", "soft, wash")]


def test_load_styles_from_settings_emits_finding_on_bad_json(monkeypatch):
    calls = _capture(monkeypatch)
    sc = SiteConfig(initial_config={"image_styles": "{not valid json"})

    result = sfi._load_styles_from_settings(sc)

    # Non-blocking: falls back to no custom styles — but the operator SET them,
    # so a silent [] hides a config error. Now visible.
    assert result == []
    assert len(calls) == 1
    assert calls[0]["kind"] == "image_styles_setting_malformed"
    assert calls[0]["dedup_key"] == "image_styles_setting_malformed"
    assert calls[0].get("severity", "info") == "info"


def test_load_styles_from_settings_unset_is_silent(monkeypatch):
    """An empty/unset setting is the OSS default, not an error — no finding."""
    calls = _capture(monkeypatch)
    assert sfi._load_styles_from_settings(SiteConfig(initial_config={})) == []
    assert calls == []


# --- _load_recent_published_styles: both read paths fail -------------------


class _AcquireRaisingPool:
    def acquire(self):
        raise RuntimeError("pool acquire boom")


@pytest.mark.asyncio
async def test_load_recent_styles_emits_finding_when_both_paths_fail(monkeypatch):
    calls = _capture(monkeypatch)
    # Pooled read fails (silent fall-through) → raw-connect read also fails
    # (the real end state) → cross-post rotation is dead, so surface it.
    monkeypatch.setattr(
        "asyncpg.connect", AsyncMock(side_effect=RuntimeError("connect boom")),
    )
    sc = SiteConfig(initial_config={
        "image_style_dedup_window": "5",
        "database_url": "postgresql://unreachable/db",
    })
    sc._pool = _AcquireRaisingPool()

    result = await sfi._load_recent_published_styles(sc)

    assert result == []  # non-blocking — rotation just can't see history
    assert len(calls) == 1
    assert calls[0]["kind"] == "recent_published_styles_read_failed"
    assert calls[0]["dedup_key"] == "recent_published_styles_read_failed"
    assert calls[0].get("severity", "info") == "info"
