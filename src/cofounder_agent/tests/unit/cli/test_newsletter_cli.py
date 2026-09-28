"""``poindexter newsletter`` — thin adapter over the signup-path services.

Pins the wiring: no SQL in the adapter, registration on the main app, options
threaded to the service, and a non-zero exit whenever the path is broken so
the commands are scriptable. The behaviour behind them is tested in
tests/unit/services/test_newsletter_audience.py and
test_newsletter_signup_canary.py.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import click
import pytest
from click.testing import CliRunner

from poindexter.cli import newsletter as cli
from poindexter.services import newsletter_audience as na
from poindexter.services import newsletter_signup_canary as canary


@pytest.fixture
def ctx(monkeypatch):
    """Stub the pool/SiteConfig seam; the services are patched per test."""
    monkeypatch.setattr(cli, "_open_ctx", AsyncMock(return_value=(object(), object())))
    monkeypatch.setattr(cli, "close_cli_pool", AsyncMock())


def _patch_sync(monkeypatch, outcome=None, exc=None) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake(pool, site_config, **kw):
        seen.update(kw)
        if exc is not None:
            raise exc
        return outcome

    monkeypatch.setattr(na, "sync_segment_to_subscribers", fake)
    return seen


def _patch_canary(monkeypatch, outcome=None, exc=None) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake(site_config, **kw):
        seen.update(kw)
        if exc is not None:
            raise exc
        return outcome

    monkeypatch.setattr(canary, "run_signup_canary", fake)
    return seen


# --- purity + registration --------------------------------------------------


def test_adapter_holds_no_sql():
    src = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    for token in ("import asyncpg", "asyncpg.", "conn.fetch", "conn.execute",
                  "SELECT ", "INSERT ", "UPDATE ", "DELETE "):
        assert token not in src, f"{token!r} in {cli.__name__}"


def test_registered_on_the_main_app():
    from poindexter.cli.app import main

    group = main.commands.get("newsletter")
    assert isinstance(group, click.Group)
    assert {"sync", "canary"} <= set(group.commands)


# --- sync -------------------------------------------------------------------


def test_sync_prints_the_summary(ctx, monkeypatch):
    seen = _patch_sync(monkeypatch, na.AudienceSyncOutcome(
        segment_id="seg-1", contacts_seen=3, imported=2, skipped_canary=1,
    ))
    result = CliRunner().invoke(cli.newsletter_group, ["sync"])
    assert result.exit_code == 0, result.output
    assert "segment seg-1: 3 contact(s) in the segment: imported 2" in result.output
    assert "skipped 1: signup canary" in result.output
    assert seen == {"dry_run": False}


def test_sync_dry_run_is_threaded_and_labelled(ctx, monkeypatch):
    seen = _patch_sync(monkeypatch, na.AudienceSyncOutcome(
        segment_id="seg-1", dry_run=True, contacts_seen=1, imported=1,
    ))
    result = CliRunner().invoke(cli.newsletter_group, ["sync", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert seen == {"dry_run": True}
    assert result.output.startswith("[dry run] ")
    assert "would import 1" in result.output


def test_sync_json(ctx, monkeypatch):
    _patch_sync(monkeypatch, na.AudienceSyncOutcome(segment_id="seg-1", imported=4))
    result = CliRunner().invoke(cli.newsletter_group, ["sync", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["segment_id"] == "seg-1"
    assert payload["imported"] == 4
    assert payload["error_detail"] == []


def test_sync_errors_exit_non_zero(ctx, monkeypatch):
    _patch_sync(monkeypatch, na.AudienceSyncOutcome(segment_id="seg-1", errors=["contact c1: boom"]))
    result = CliRunner().invoke(cli.newsletter_group, ["sync"])
    assert result.exit_code == 1
    assert "contact c1: boom" in result.output


def test_sync_without_a_segment_says_how_to_fix_it(ctx, monkeypatch):
    _patch_sync(monkeypatch, na.AudienceSyncOutcome(segment_id=None))
    result = CliRunner().invoke(cli.newsletter_group, ["sync"])
    assert result.exit_code != 0
    assert "resend_audience_id is not set" in result.output


def test_sync_bad_segment_id_is_a_clean_error(ctx, monkeypatch):
    _patch_sync(monkeypatch, exc=na.AudienceConfigError("resend_audience_id 'x/y' is not a plain Resend id token"))
    result = CliRunner().invoke(cli.newsletter_group, ["sync"])
    assert result.exit_code != 0
    assert "plain Resend id token" in result.output
    cli.close_cli_pool.assert_awaited()  # the pool is closed on the error path too


# --- canary -----------------------------------------------------------------


def test_canary_healthy(ctx, monkeypatch):
    seen = _patch_canary(monkeypatch, canary.SignupCanaryOutcome(
        url="https://site.example/api/newsletter/subscribe", segment_id="seg-1",
        route_ok=True, in_segment=True, cleaned_up=True, http_status=200,
        attempts=1, latency_ms=80,
    ))
    result = CliRunner().invoke(cli.newsletter_group, ["canary"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("captured:")
    assert "segment seg-1" in result.output
    assert seen == {"url": None}


def test_canary_url_is_threaded(ctx, monkeypatch):
    seen = _patch_canary(monkeypatch, canary.SignupCanaryOutcome(
        url="https://preview.example/api/newsletter/subscribe",
        route_ok=True, in_segment=True, cleaned_up=True, http_status=200,
    ))
    result = CliRunner().invoke(
        cli.newsletter_group,
        ["canary", "--url", "https://preview.example/api/newsletter/subscribe"],
    )
    assert result.exit_code == 0, result.output
    assert seen == {"url": "https://preview.example/api/newsletter/subscribe"}


def test_canary_broken_exits_non_zero_with_the_cause(ctx, monkeypatch):
    _patch_canary(monkeypatch, canary.SignupCanaryOutcome(
        url="u", http_status=503, problem="the signup endpoint did not capture the canary (HTTP 503)",
    ))
    result = CliRunner().invoke(cli.newsletter_group, ["canary"])
    assert result.exit_code == 1
    assert "NOT CAPTURED" in result.output
    assert "HTTP 503" in result.output


def test_canary_json(ctx, monkeypatch):
    _patch_canary(monkeypatch, canary.SignupCanaryOutcome(
        url="u", segment_id="seg-1", http_status=503, problem="broken",
    ))
    result = CliRunner().invoke(cli.newsletter_group, ["canary", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["healthy"] is False
    assert payload["problem"] == "broken"


def test_canary_off_says_how_to_run_it(ctx, monkeypatch):
    _patch_canary(monkeypatch, None)
    result = CliRunner().invoke(cli.newsletter_group, ["canary"])
    assert result.exit_code != 0
    assert "--url" in result.output


def test_canary_config_error_is_a_clean_error(ctx, monkeypatch):
    _patch_canary(monkeypatch, exc=canary.CanaryConfigError("resend_api_key is not set"))
    result = CliRunner().invoke(cli.newsletter_group, ["canary"])
    assert result.exit_code != 0
    assert "resend_api_key is not set" in result.output
