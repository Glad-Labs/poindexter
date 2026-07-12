"""Unit tests for the shared ``resolve_pool`` atom helper.

Pins the defense-in-depth pool fallback shared across the content atoms: prefer
``database_service.pool``, fall back to ``site_config._pool`` (the same live
handle ``caption_images``/``qa.vision`` source) and log a loud ``[POOL]``
WARNING when the fallback engages so a genuine missing-pool run surfaces in Loki
instead of silently dropping cost-logging (vision_scorer_unavailable follow-up
2026-07-12).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.content.atoms import _pool
from modules.content.atoms._pool import resolve_pool


@pytest.mark.unit
class TestResolvePool:
    def test_prefers_database_service_pool(self, monkeypatch):
        """A live database_service.pool wins — no fallback, no warning."""
        warned: list = []
        monkeypatch.setattr(_pool.logger, "warning", lambda *a, **k: warned.append(a))
        state = {
            "database_service": SimpleNamespace(pool="LIVE_DB_POOL"),
            "site_config": SimpleNamespace(_pool="SITE_POOL"),
        }
        assert resolve_pool(state, atom="qa.critic") == "LIVE_DB_POOL"
        assert warned == []

    def test_falls_back_to_site_config_pool_and_warns(self, monkeypatch):
        """database_service.pool is None but site_config._pool is live → fall
        back to it AND emit a named [POOL] warning."""
        warned: list = []
        monkeypatch.setattr(_pool.logger, "warning", lambda *a, **k: warned.append(a))
        state = {
            "database_service": SimpleNamespace(pool=None),
            "site_config": SimpleNamespace(_pool="SITE_POOL"),
            "task_id": "b740e4b8-0b23-40a9-826b-f4ea059df302",
        }
        assert resolve_pool(state, atom="qa.vision") == "SITE_POOL"
        assert len(warned) == 1
        msg = warned[0][0] % warned[0][1:]
        assert "[POOL]" in msg
        assert "qa.vision" in msg

    def test_missing_database_service_key_uses_fallback(self, monkeypatch):
        """The key can be absent entirely (not just None) — still fall back."""
        monkeypatch.setattr(_pool.logger, "warning", lambda *a, **k: None)
        state = {"site_config": SimpleNamespace(_pool="SITE_POOL")}
        assert resolve_pool(state, atom="seo") == "SITE_POOL"

    def test_returns_none_when_no_pool_anywhere_without_warning(self, monkeypatch):
        """Both handles dead (tests / ad-hoc CLI) → None and NO fallback
        warning: that is the pre-existing pool-optional state atoms tolerate,
        not the interesting 'db dead but site_config live' signal."""
        warned: list = []
        monkeypatch.setattr(_pool.logger, "warning", lambda *a, **k: warned.append(a))
        state = {"database_service": SimpleNamespace(pool=None), "site_config": None}
        assert resolve_pool(state, atom="qa.ragas") is None
        assert warned == []
