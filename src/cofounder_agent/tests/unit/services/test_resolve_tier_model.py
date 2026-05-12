"""Unit tests for ``services.llm_providers.dispatcher.resolve_tier_model``.

Pin the no-silent-defaults contract: an unknown tier or a missing
``app_settings.cost_tier.<tier>.model`` row must raise loudly. Lane B
sweep agents lean on this to know they can replace literal model names
with ``await resolve_tier_model(pool, tier)`` without smuggling in a
fallback path.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.llm_providers.dispatcher import resolve_tier_model


class _FakeConn:
    def __init__(self, value: str | None):
        self._value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query: str, *args: Any) -> str | None:
        return self._value


class _FakePool:
    def __init__(self, value: str | None):
        self._value = value

    def acquire(self):
        return _FakeConn(self._value)


@pytest.mark.asyncio
async def test_returns_configured_model():
    pool = _FakePool("ollama/gemma3:27b")
    assert await resolve_tier_model(pool, "standard") == "ollama/gemma3:27b"


@pytest.mark.asyncio
async def test_strips_whitespace():
    pool = _FakePool("  ollama/gemma3:27b  ")
    assert await resolve_tier_model(pool, "standard") == "ollama/gemma3:27b"


@pytest.mark.asyncio
async def test_raises_on_unknown_tier():
    pool = _FakePool("ollama/gemma3:27b")
    with pytest.raises(ValueError, match="unknown tier"):
        await resolve_tier_model(pool, "platinum")


@pytest.mark.asyncio
async def test_raises_on_missing_row():
    pool = _FakePool(None)
    with pytest.raises(RuntimeError, match="no model configured"):
        await resolve_tier_model(pool, "standard")


@pytest.mark.asyncio
async def test_raises_on_empty_row():
    pool = _FakePool("")
    with pytest.raises(RuntimeError, match="no model configured"):
        await resolve_tier_model(pool, "standard")


@pytest.mark.asyncio
async def test_all_four_tiers_accepted():
    pool = _FakePool("ollama/test:1")
    for tier in ("free", "budget", "standard", "premium"):
        assert await resolve_tier_model(pool, tier) == "ollama/test:1"


@pytest.mark.asyncio
async def test_flagship_tier_accepted():
    """The 5th tier ('flagship') was added after the original test was written.

    Pinning this prevents a future refactor from silently dropping the tier
    while leaving the four older ones working — that kind of regression
    would only surface when an operator picks ``flagship`` in production.
    """
    pool = _FakePool("anthropic/claude-opus-4-7")
    assert await resolve_tier_model(pool, "flagship") == "anthropic/claude-opus-4-7"


@pytest.mark.asyncio
async def test_raises_on_empty_string_tier():
    pool = _FakePool("ollama/gemma3:27b")
    with pytest.raises(ValueError, match="unknown tier"):
        await resolve_tier_model(pool, "")


@pytest.mark.asyncio
async def test_tier_name_is_case_sensitive():
    """Tier check uses ``not in`` against a literal tuple, so uppercase fails.

    The contract is intentionally strict — callers must use the canonical
    lowercase names from ``_TIER_NAMES``. Any normalisation belongs at the
    caller (e.g. CLI input parsing), not here.
    """
    pool = _FakePool("ollama/gemma3:27b")
    with pytest.raises(ValueError, match="unknown tier"):
        await resolve_tier_model(pool, "STANDARD")


@pytest.mark.asyncio
async def test_tier_name_with_whitespace_rejected():
    pool = _FakePool("ollama/gemma3:27b")
    with pytest.raises(ValueError, match="unknown tier"):
        await resolve_tier_model(pool, " standard")


@pytest.mark.asyncio
async def test_raises_on_whitespace_only_value():
    """``"   "`` is not None and not empty, but stripping leaves nothing.

    The ``not val or not val.strip()`` guard handles both cases. If someone
    later simplifies to ``not val``, this test catches the regression.
    """
    pool = _FakePool("   ")
    with pytest.raises(RuntimeError, match="no model configured"):
        await resolve_tier_model(pool, "standard")


@pytest.mark.asyncio
async def test_strips_tabs_and_newlines():
    pool = _FakePool("\tollama/gemma3:27b\n")
    assert await resolve_tier_model(pool, "standard") == "ollama/gemma3:27b"


class _RaisingPool:
    """Pool whose ``acquire`` itself raises (not the fetchval)."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def acquire(self):
        raise self._exc


@pytest.mark.asyncio
async def test_wraps_pool_acquire_error_in_runtime_error():
    """Any DB-layer failure must surface as RuntimeError with the key name.

    Callers distinguish ``ValueError`` (caller passed a bad tier) from
    ``RuntimeError`` (the configuration plane is broken). The wrapping is
    part of the contract — if a future refactor lets the raw asyncpg
    error escape, every caller's error handling needs to change.
    """
    pool = _RaisingPool(ConnectionError("pool exhausted"))
    with pytest.raises(RuntimeError, match="cost_tier.standard.model"):
        await resolve_tier_model(pool, "standard")


class _FetchvalRaisingConn:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query: str, *args: Any) -> str | None:
        raise self._exc


class _FetchvalRaisingPool:
    def __init__(self, exc: Exception):
        self._exc = exc

    def acquire(self):
        return _FetchvalRaisingConn(self._exc)


@pytest.mark.asyncio
async def test_wraps_fetchval_error_and_preserves_cause():
    """The ``from exc`` chain lets debuggers walk back to the asyncpg error.

    Without ``raise ... from exc`` the original traceback gets shadowed
    by a bare ``During handling of the above exception``. Pinning the
    ``__cause__`` attribute documents that operators get the full chain.
    """
    original = RuntimeError("connection reset by peer")
    pool = _FetchvalRaisingPool(original)
    with pytest.raises(RuntimeError, match="query for") as excinfo:
        await resolve_tier_model(pool, "premium")
    assert excinfo.value.__cause__ is original


class _RecordingConn:
    """Records the query + args so tests can assert on the SQL shape."""

    def __init__(self, value: str | None):
        self._value = value
        self.last_query: str | None = None
        self.last_args: tuple[Any, ...] = ()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query: str, *args: Any) -> str | None:
        self.last_query = query
        self.last_args = args
        return self._value


class _RecordingPool:
    def __init__(self, value: str | None):
        self._conn = _RecordingConn(value)

    def acquire(self):
        return self._conn


@pytest.mark.asyncio
async def test_queries_app_settings_with_namespaced_key():
    """Pin the SQL contract: parameterised query against the canonical key.

    The ``cost_tier.<tier>.model`` namespace is shared with bootstrap seeds
    and the operator UI. If this key shape ever drifts, every other surface
    touching ``app_settings`` for tier config breaks silently.
    """
    pool = _RecordingPool("ollama/gemma3:27b")
    await resolve_tier_model(pool, "budget")
    assert pool._conn.last_args == ("cost_tier.budget.model",)
    query = pool._conn.last_query
    assert query is not None
    assert "FROM app_settings" in query
    assert "WHERE key = $1" in query
