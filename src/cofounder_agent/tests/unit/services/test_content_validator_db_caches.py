"""Unit tests for the DB-backed ``_load_fact_overrides_sync`` cache in
``modules/content/content_validator.py``.

Complements #2464's pool-presence gate (``site_config is None or
site_config._pool is None`` -> skip the network entirely): that fix covers
the "no pool at all" case (unit tests, bare ``SiteConfig()``). This test
covers the case it doesn't — a live pool IS present (production) but the
load itself fails or the DB is slow/unreachable. Before this fix, that
still re-issued a full DB connection attempt on every single
``validate_content()`` call instead of once per TTL window.

Tests force DSN resolution to a fixed fake value and stub ``asyncpg.connect``
so they're hermetic (no dependency on any real DSN/network in any
environment, dev machine or CI).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from modules.content import content_validator as cv


def _ensure_brain_importable() -> None:
    """Mirror content_validator's own upward search for brain/bootstrap.py
    so ``brain.bootstrap.resolve_database_url`` is patchable regardless of
    whether anything has imported it yet."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return
    pytest.skip("brain/bootstrap.py not found — cannot exercise DSN resolution seam")


class _FakeSiteConfigWithPool:
    """Stand-in for a DI'd SiteConfig with a live pool. Nothing here ever
    calls into the pool, only checks ``_pool is not None``."""

    def __init__(self) -> None:
        self._pool = object()


@pytest.fixture(autouse=True)
def _reset_content_validator_caches():
    cv._fact_overrides_cache = []
    cv._fact_overrides_ts = 0.0
    yield
    cv._fact_overrides_cache = []
    cv._fact_overrides_ts = 0.0


@pytest.mark.unit
class TestFactOverridesCooldown:
    def test_repeated_calls_after_failed_connect_do_not_reconnect(self, monkeypatch):
        _ensure_brain_importable()
        monkeypatch.setattr(
            "brain.bootstrap.resolve_database_url",
            lambda: "postgresql://fake-host-for-test/dsn",
        )

        calls = {"n": 0}

        async def _fake_connect(*args, **kwargs):
            calls["n"] += 1
            raise ConnectionRefusedError("boom")

        monkeypatch.setattr("asyncpg.connect", _fake_connect)

        site_config = _FakeSiteConfigWithPool()
        for _ in range(5):
            cv._load_fact_overrides_sync(site_config=site_config)

        assert calls["n"] == 1
