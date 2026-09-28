"""Unit tests for ``brain/probe_severity.py``.

Covers the pure classification functions, the DB-backed override JSON
blob (parse + validate + malformed-input handling), and the
``sender_for`` convenience wrapper used by the single-probe-per-cycle
callers (``business_probes.py``, ``post_performance_probe.py``).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import probe_severity as ps
from tests.unit._nonempty import nonempty


def _make_pool(value: str | None) -> Any:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=value)
    return pool


# ---------------------------------------------------------------------------
# severity_for / is_paging_severity — pure, no I/O
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSeverityFor:
    def test_named_probe_returns_its_code_default(self):
        assert ps.severity_for("db_ping", {}) == "critical"

    def test_unlisted_probe_returns_default_severity(self):
        assert ps.severity_for("some_new_probe_nobody_classified", {}) == ps.DEFAULT_SEVERITY
        assert ps.DEFAULT_SEVERITY == "warning"

    def test_override_wins_over_code_default(self):
        assert ps.severity_for("db_ping", {"db_ping": "warning"}) == "warning"

    def test_override_can_promote_an_unlisted_probe(self):
        assert ps.severity_for("cadence_slo", {"cadence_slo": "critical"}) == "critical"

    def test_custom_defaults_table_honored(self):
        assert ps.severity_for("x", {}, defaults={"x": "error"}) == "error"


@pytest.mark.unit
class TestIsPagingSeverity:
    @pytest.mark.parametrize("sev", ["critical", "CRITICAL", "error", " Error "])
    def test_paging_severities(self, sev):
        assert ps.is_paging_severity(sev) is True

    @pytest.mark.parametrize("sev", ["warning", "info", "", None, "nonsense"])
    def test_non_paging_severities(self, sev):
        assert ps.is_paging_severity(sev) is False


@pytest.mark.unit
def test_default_severity_table_only_uses_valid_severities():
    """A typo in PROBE_DEFAULT_SEVERITY's own values would silently
    default-deny paging for a probe meant to be critical."""
    for name, sev in nonempty(ps.PROBE_DEFAULT_SEVERITY.items(), "PROBE_DEFAULT_SEVERITY"):
        assert sev in ps.VALID_SEVERITIES, f"{name!r} has invalid severity {sev!r}"


# ---------------------------------------------------------------------------
# load_overrides — DB read + JSON parse + validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestLoadOverrides:
    async def test_empty_setting_returns_empty_dict(self):
        assert await ps.load_overrides(_make_pool(None)) == {}
        assert await ps.load_overrides(_make_pool("")) == {}
        assert await ps.load_overrides(_make_pool("   ")) == {}

    async def test_valid_json_object_parsed(self):
        pool = _make_pool('{"db_ping": "warning", "cadence_slo": "critical"}')
        assert await ps.load_overrides(pool) == {
            "db_ping": "warning",
            "cadence_slo": "critical",
        }

    async def test_values_are_normalized_lowercase_and_stripped(self):
        pool = _make_pool('{"db_ping": " CRITICAL "}')
        assert await ps.load_overrides(pool) == {"db_ping": "critical"}

    async def test_malformed_json_dropped_with_warning(self, caplog):
        pool = _make_pool("{not json")
        with caplog.at_level(logging.WARNING, logger="brain.probe_severity"):
            result = await ps.load_overrides(pool)
        assert result == {}
        assert any("not a JSON object" in r.getMessage() for r in caplog.records)

    async def test_json_array_dropped_with_warning(self, caplog):
        """A JSON list is valid JSON but not the expected shape (an object
        keyed by probe name) -- must be rejected the same as malformed JSON,
        not silently misinterpreted."""
        pool = _make_pool('["critical", "warning"]')
        with caplog.at_level(logging.WARNING, logger="brain.probe_severity"):
            result = await ps.load_overrides(pool)
        assert result == {}
        assert any("not a JSON object" in r.getMessage() for r in caplog.records)

    async def test_invalid_severity_value_dropped_others_kept(self, caplog):
        pool = _make_pool('{"db_ping": "urgent", "cadence_slo": "critical"}')
        with caplog.at_level(logging.WARNING, logger="brain.probe_severity"):
            result = await ps.load_overrides(pool)
        assert result == {"cadence_slo": "critical"}
        assert any("invalid severity" in r.getMessage() for r in caplog.records)

    async def test_db_read_failure_returns_empty_dict(self, caplog):
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("db exploded"))
        with caplog.at_level(logging.WARNING, logger="brain.probe_severity"):
            result = await ps.load_overrides(pool)
        assert result == {}


# ---------------------------------------------------------------------------
# sender_for — the single-probe-per-cycle convenience wrapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestSenderFor:
    async def test_critical_probe_resolves_to_notify_fn(self):
        notify_fn, info_fn = AsyncMock(), AsyncMock()
        sender = await ps.sender_for(_make_pool(None), "silent_alerter", notify_fn, info_fn)
        assert sender is notify_fn

    async def test_warning_probe_resolves_to_info_fn(self):
        notify_fn, info_fn = AsyncMock(), AsyncMock()
        sender = await ps.sender_for(_make_pool(None), "webhook_freshness", notify_fn, info_fn)
        assert sender is info_fn

    async def test_warning_probe_falls_back_to_notify_fn_when_no_info_fn(self):
        notify_fn = AsyncMock()
        sender = await ps.sender_for(_make_pool(None), "webhook_freshness", notify_fn)
        assert sender is notify_fn

    async def test_db_override_flips_the_resolved_sender(self):
        notify_fn, info_fn = AsyncMock(), AsyncMock()
        pool = _make_pool('{"webhook_freshness": "critical"}')
        sender = await ps.sender_for(pool, "webhook_freshness", notify_fn, info_fn)
        assert sender is notify_fn
