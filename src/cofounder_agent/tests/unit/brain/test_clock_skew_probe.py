"""Unit tests for ``brain/clock_skew_probe.py`` (2026-07-08 investigation).

Pins the DB wall-clock skew detector: an external-absolute reference
(injected here in place of the HTTP ``Date:`` fetch) compared against
Postgres ``clock_timestamp()``, an edge-triggered ``db_clock_skew``
finding (critical → Telegram) with a renotify window, graceful
``unknown`` when the reference (or the PG clock) can't be read — with
NO false page — a continuous ``clock_skew_samples`` write for the
time-series, and self-pruning of old samples.

The reference is injected (``ref_fetcher``) so skew is deterministic and
no network is touched; ``now_fn`` controls the renotify clock.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from brain.clock_skew_probe import (
    DEFAULT_REFERENCE_URL,
    DEFAULT_RENOTIFY_MINUTES,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SEVERITY,
    DEFAULT_THRESHOLD_SECONDS,
    FINDING_KIND,
    run_clock_skew_probe,
)

# A fixed "real" external epoch and the container/host wall clock aligned to it.
_REF_EPOCH = 1_783_484_819.0
_NOW = _REF_EPOCH  # container clock agrees with the reference by default


def _ref(value):
    """Build an injected async reference fetcher returning ``value`` (or None)."""

    async def _fetch(url):  # noqa: ANN001, ARG001
        return value

    return _fetch


def _pool(
    *,
    pg_epoch=_REF_EPOCH,
    prev_state=None,
    last_notified_at=None,
    enabled="true",
):
    """asyncpg pool stub.

    ``fetchval`` routes three read shapes by SQL text: app_settings
    lookups (by key arg), the ``clock_timestamp()`` epoch read, and the
    brain_knowledge state reads (by attribute arg). ``execute`` captures
    finding / sample / prune / state writes for assertion.
    """
    pool = MagicMock()

    async def _fetchval(query, *args, **kwargs):  # noqa: ANN001, ARG001
        if "app_settings" in query:
            key = args[0] if args else ""
            if key == "clock_skew_probe_enabled":
                return enabled
            return None  # every other setting falls back to its in-code default
        if "clock_timestamp" in query:
            return pg_epoch
        if "brain_knowledge" in query:
            attr = args[1] if len(args) > 1 else ""
            if attr == "last_state":
                return prev_state
            if attr == "last_notified_at":
                return None if last_notified_at is None else str(last_notified_at)
            return None
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock()
    return pool


def _calls_containing(pool, needle):
    return [
        c.args
        for c in pool.execute.call_args_list
        if c.args and needle in c.args[0]
    ]


def _audit_calls(pool):
    return _calls_containing(pool, "audit_log")


def _sample_inserts(pool):
    return [
        c for c in _calls_containing(pool, "clock_skew_samples")
        if "INSERT" in c[0]
    ]


def _prune_calls(pool):
    return [
        c for c in _calls_containing(pool, "clock_skew_samples")
        if "DELETE" in c[0]
    ]


def _state_writes(pool):
    return [
        c for c in _calls_containing(pool, "brain_knowledge")
        if "INSERT" in c[0] or "UPDATE" in c[0]
    ]


@pytest.mark.asyncio
async def test_healthy_clock_no_finding():
    pool = _pool(pg_epoch=_REF_EPOCH + 3, prev_state="ok")  # 3s < 120s threshold
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["ok"] is True
    assert res["status"] == "ok"
    assert _audit_calls(pool) == []
    inserts = _sample_inserts(pool)
    assert len(inserts) == 1
    assert inserts[0][3] == "ok"  # (sql, skew, url, status)


@pytest.mark.asyncio
async def test_skew_transition_emits_finding_with_shape():
    pool = _pool(pg_epoch=_REF_EPOCH + 14_400, prev_state="ok")  # +4h
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["ok"] is False
    assert res["status"] == "skewed"
    assert res["skew_seconds"] == pytest.approx(14_400.0)
    calls = _audit_calls(pool)
    assert len(calls) == 1
    details = json.loads(calls[0][1])
    assert details["kind"] == FINDING_KIND == "db_clock_skew"  # dot-free
    assert details["dedup_key"] == "db_clock_skew"
    assert details["extra"]["skew_seconds"] == pytest.approx(14_400.0)
    assert calls[0][2] == DEFAULT_SEVERITY == "critical"  # severity bound param
    assert _sample_inserts(pool)[0][3] == "skewed"


@pytest.mark.asyncio
async def test_persistent_skew_within_renotify_window_does_not_refire():
    pool = _pool(
        pg_epoch=_REF_EPOCH + 14_400,
        prev_state="skewed",
        last_notified_at=_NOW - 10,  # 10s ago, well within 60-min window
    )
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["status"] == "skewed"
    assert _audit_calls(pool) == []


@pytest.mark.asyncio
async def test_persistent_skew_past_renotify_window_refires():
    pool = _pool(
        pg_epoch=_REF_EPOCH + 14_400,
        prev_state="skewed",
        last_notified_at=_NOW - 7_200,  # 2h ago > 60-min window
    )
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["status"] == "skewed"
    assert len(_audit_calls(pool)) == 1


@pytest.mark.asyncio
async def test_already_skewed_at_boot_emits():
    """prev=None (no brain_knowledge row yet) + skewed must still surface."""
    pool = _pool(pg_epoch=_REF_EPOCH + 14_400, prev_state=None)
    await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert len(_audit_calls(pool)) == 1


@pytest.mark.asyncio
async def test_reference_unreachable_is_unknown_no_finding():
    pool = _pool(pg_epoch=_REF_EPOCH + 14_400, prev_state="ok")
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(None), now_fn=lambda: _NOW)
    assert res["ok"] is True
    assert res["status"] == "unknown"
    assert _audit_calls(pool) == []  # never page when we can't confirm
    assert _state_writes(pool) == []  # state untouched on unknown
    # A sample is still recorded so the gap is visible, with NULL skew.
    inserts = _sample_inserts(pool)
    assert len(inserts) == 1
    assert inserts[0][1] is None  # skew_seconds NULL
    assert inserts[0][3] == "unknown"


@pytest.mark.asyncio
async def test_pg_clock_unreadable_is_unknown_no_writes():
    pool = _pool(pg_epoch=None)
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["status"] == "unknown"
    assert _audit_calls(pool) == []
    assert _sample_inserts(pool) == []  # nothing to sample


@pytest.mark.asyncio
async def test_disabled_probe_is_silent():
    pool = _pool(pg_epoch=_REF_EPOCH + 999_999, enabled="false")
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["status"] == "disabled"
    assert res["ok"] is True
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_clears_state_no_finding():
    pool = _pool(pg_epoch=_REF_EPOCH + 5, prev_state="skewed")  # back in range
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["ok"] is True
    assert res["status"] == "ok"
    assert _audit_calls(pool) == []
    writes = _state_writes(pool)
    # last_state written back to 'ok'
    assert any("ok" in c[3] for c in writes if len(c) > 3)


@pytest.mark.asyncio
async def test_self_prunes_old_samples():
    pool = _pool(pg_epoch=_REF_EPOCH, prev_state="ok")
    await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert len(_prune_calls(pool)) == 1


@pytest.mark.asyncio
async def test_threshold_is_strict_boundary():
    # Exactly at threshold → not skewed.
    at = _pool(pg_epoch=_REF_EPOCH + DEFAULT_THRESHOLD_SECONDS, prev_state="ok")
    res_at = await run_clock_skew_probe(at, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res_at["status"] == "ok"
    # One second past → skewed.
    over = _pool(pg_epoch=_REF_EPOCH + DEFAULT_THRESHOLD_SECONDS + 1, prev_state="ok")
    res_over = await run_clock_skew_probe(over, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res_over["status"] == "skewed"


@pytest.mark.asyncio
async def test_negative_skew_also_detected():
    """A DB clock in the PAST (behind the reference) is skew too."""
    pool = _pool(pg_epoch=_REF_EPOCH - 14_400, prev_state="ok")
    res = await run_clock_skew_probe(pool, ref_fetcher=_ref(_REF_EPOCH), now_fn=lambda: _NOW)
    assert res["status"] == "skewed"
    assert len(_audit_calls(pool)) == 1


def test_seeded_defaults_match_in_code_fallback():
    """settings_defaults.py seeds and the in-code DEFAULT_* constants must
    agree — else a fresh install (seed) and a broken-settings fallback
    (in-code) behave differently."""
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["clock_skew_probe_enabled"] == "true"
    assert DEFAULTS["clock_skew_reference_url"] == DEFAULT_REFERENCE_URL
    assert DEFAULTS["clock_skew_threshold_seconds"] == str(DEFAULT_THRESHOLD_SECONDS)
    assert DEFAULTS["clock_skew_severity"] == DEFAULT_SEVERITY
    assert DEFAULTS["clock_skew_renotify_minutes"] == str(DEFAULT_RENOTIFY_MINUTES)
    assert DEFAULTS["clock_skew_sample_retention_days"] == str(DEFAULT_RETENTION_DAYS)
    assert DEFAULTS[f"findings.{FINDING_KIND}.delivery"] == "telegram"
