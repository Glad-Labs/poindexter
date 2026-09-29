"""Unit tests for ``services/jobs/drain_sentry_relay.py``.

The drain itself is covered in ``tests/unit/services/test_sentry_relay.py``.
These pin the job's side of the contract: each way site errors can stop
reaching anyone raises its own finding kind (each kind has a delivery
policy in settings_defaults), and a quiet install stays quiet.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.jobs.drain_sentry_relay import DrainSentryRelayJob
from poindexter.services.sentry_relay import DrainOutcome


def _site_config(settings: dict[str, str]) -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": settings.get(key, default)
    return sc


CONFIGURED = {"_site_config": _site_config({"sentry_relay_url": "https://relay.example.com"})}


async def _run(outcome: DrainOutcome | None = None, *, raises: BaseException | None = None,
               config: dict[str, Any] | None = None):
    drain = AsyncMock(return_value=outcome, side_effect=raises)
    findings: list[dict[str, Any]] = []
    with patch("poindexter.services.sentry_relay.drain_sentry_relay", drain), patch(
        "poindexter.services.jobs.drain_sentry_relay.emit_finding",
        side_effect=lambda **kw: findings.append(kw),
    ):
        result = await DrainSentryRelayJob().run(
            pool=MagicMock(), config=CONFIGURED if config is None else config
        )
    return result, findings, drain


@pytest.mark.asyncio
async def test_no_site_config_returns_not_ok():
    result = await DrainSentryRelayJob().run(pool=MagicMock(), config={})
    assert result.ok is False
    assert "_site_config" in result.detail


@pytest.mark.asyncio
async def test_unconfigured_is_a_noop_that_never_calls_the_drain():
    result, findings, drain = await _run(
        config={"_site_config": _site_config({"sentry_relay_url": ""})}
    )
    assert result.ok is True
    assert "no-op" in result.detail
    assert result.metrics == {"configured": 0}
    drain.assert_not_called()
    assert findings == []


@pytest.mark.asyncio
async def test_a_clean_pass_reports_what_it_forwarded():
    outcome = DrainOutcome(configured=True, pulled=3, forwarded=3, acked=3)
    result, findings, _ = await _run(outcome)
    assert result.ok is True
    assert result.changes_made == 3
    assert result.metrics["forwarded"] == 3
    assert result.metrics["configured"] == 1
    assert findings == []


@pytest.mark.asyncio
async def test_a_raised_drain_is_a_failed_run_with_a_finding():
    result, findings, _ = await _run(raises=RuntimeError("relay 502"))
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "sentry_relay_drain_failed"
    assert finding["severity"] == "warn"
    assert "relay 502" in finding["body"]


@pytest.mark.asyncio
async def test_drain_errors_fail_the_run_and_raise_a_finding():
    outcome = DrainOutcome(
        configured=True, pulled=2, deferred=1, backlog=2,
        errors=["GlitchTip answered 503"],
    )
    result, findings, _ = await _run(outcome)
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "sentry_relay_drain_failed"
    assert "GlitchTip answered 503" in finding["body"]
    assert "2 envelope(s) still queued" in finding["body"]


@pytest.mark.asyncio
async def test_rejections_raise_their_own_finding_with_the_reasons():
    outcome = DrainOutcome(
        configured=True, pulled=2, rejected=2, acked=2,
        rejections=["GlitchTip answered 403 for project 2: Denied"],
    )
    result, findings, _ = await _run(outcome)
    # A rejection is a verdict, not a failed run — but it is never silent.
    assert result.ok is True
    [finding] = findings
    assert finding["kind"] == "sentry_relay_envelope_rejected"
    assert "403 for project 2" in finding["body"]


@pytest.mark.asyncio
async def test_expired_envelopes_raise_their_own_finding():
    outcome = DrainOutcome(configured=True, expired=7)
    result, findings, _ = await _run(outcome)
    [finding] = findings
    assert finding["kind"] == "sentry_relay_envelopes_expired"
    assert "7 site error envelope(s) expired" in finding["title"]
    assert result.metrics["expired"] == 7


def test_every_finding_kind_the_job_emits_has_a_delivery_policy():
    """consumer_contract_lint checks this statically too; pin it at the unit
    level so a renamed kind fails here with a readable message."""
    import ast
    import inspect

    from poindexter.services import settings_defaults
    from poindexter.services.jobs import drain_sentry_relay

    tree = ast.parse(inspect.getsource(drain_sentry_relay))
    kinds = {
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "emit_finding"
        for kw in node.keywords
        if kw.arg == "kind" and isinstance(kw.value, ast.Constant)
    }
    assert kinds == {
        "sentry_relay_drain_failed",
        "sentry_relay_envelope_rejected",
        "sentry_relay_envelopes_expired",
    }
    for kind in sorted(kinds):
        assert settings_defaults.DEFAULTS.get(f"findings.{kind}.delivery") == "discord", kind
