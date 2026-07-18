"""Tests — `_get_site_config` must announce a failed load.

Silent-excepts OLD-debt burn-down (batch 26). ``_get_site_config`` swallowed a
failed ``SiteConfig.load`` with ``pass  # fall back to empty config``. An
unloaded SiteConfig is not empty-and-obvious: every later
``cfg.get(key, default)`` quietly returns the **code default** instead of the
operator's tuned ``app_settings`` value.

This is the fifth sibling of the same shape fixed across the CLI in batch 25
(``posts`` / ``approval`` / ``publish_approval`` / ``schedule``) — and it is
**worse here, because the result is cached in a module global**:

    global _site_config
    if _site_config is None:
        ...
        try: await _site_config.load(pool)
        except Exception: pass

There is no retry. One failed load at startup means the MCP server serves
code-defaults for its **entire process lifetime**, where the CLI equivalents
degraded only for a single invocation. Nothing changes about the fallback —
it just has to say so, and ``logger.warning`` is the surface that reaches an
operator who is not watching a terminal.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

HERE = Path(__file__).resolve().parent
MCP_SERVER_DIR = HERE.parent
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

import server  # noqa: E402


@pytest.mark.asyncio
async def test_failed_site_config_load_is_visible(caplog, monkeypatch):
    import services.site_config as sc_mod

    class _BrokenSiteConfig:
        def __init__(self, *_a, **_k):
            pass

        async def load(self, _pool):
            raise RuntimeError("app_settings unreachable")

        def get(self, _key, default=""):
            return default

    monkeypatch.setattr(sc_mod, "SiteConfig", _BrokenSiteConfig)
    monkeypatch.setattr(server, "_get_pool", AsyncMock(return_value=MagicMock()))
    # The result is cached in a module global — clear it so the load runs.
    monkeypatch.setattr(server, "_site_config", None)

    with caplog.at_level(logging.WARNING):
        cfg = await server._get_site_config()

    # Degraded-but-working is the contract: it still returns a usable config.
    assert cfg is not None
    assert cfg.get("anything", "fallback") == "fallback"

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    combined = " ".join(r.getMessage() for r in warnings).lower()
    assert warnings, "a failed SiteConfig load must be visible at WARNING"
    assert "app_settings unreachable" in combined or "config" in combined, (
        "the warning must identify what failed — every setting this process "
        f"reads now silently returns its code default; got: {combined!r}"
    )
