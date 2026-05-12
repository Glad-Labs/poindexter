"""Unit tests for brain/alert_sync.py — GH-28.

DB-driven Grafana alert rule sync. The module lives in ``brain/`` and
imports only stdlib + asyncpg, so all external I/O is mocked here:

* asyncpg pool → MagicMock with AsyncMock methods
* Grafana HTTP → monkeypatch of urllib.request.urlopen and
  urllib.error.URLError to simulate reachability + 2xx/4xx paths

Coverage:

1. ``rule_to_grafana_payload`` — DB row → Grafana API JSON shape
2. ``_hash_rule`` — deterministic + sensitive to every relevant field
3. ``sync_alert_rules`` — happy path, disabled switch, empty token,
   unchanged-rule skip, Grafana unreachable, 4xx response
4. End-to-end: ``_maybe_sync_grafana_alerts`` cadence counter
"""

from __future__ import annotations

import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Put brain/ on sys.path so the test can import ``brain.alert_sync``
# AND ``brain.brain_daemon`` — same prelude as
# test_brain_daemon_auto_remediate.py.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import alert_sync as asx  # noqa: E402

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _sample_row(**overrides) -> dict:
    """Canonical DB row shape the tests share."""
    row = {
        "name": "Pipeline Failure Rate Over 20%",
        "promql_query": (
            "SELECT ROUND(COUNT(*) FILTER (WHERE status = 'failed') * 100.0 "
            "/ NULLIF(COUNT(*), 0), 1) as failure_rate "
            "FROM pipeline_tasks_view"
        ),
        "threshold": 20.0,
        "duration": "0m",
        "severity": "critical",
        "enabled": True,
        "labels_json": {"team": "glad-labs", "category": "pipeline"},
        "annotations_json": {"summary": "high fail rate"},
    }
    row.update(overrides)
    return row


def _mock_pool(
    settings: dict[str, str] | None = None,
    rows: list[dict] | None = None,
    cached_hashes: dict[str, str] | None = None,
) -> MagicMock:
    """Build a pool that mimics the query order in sync_alert_rules:

    1. _get_setting('grafana_alert_sync_enabled')  → fetchval
    2. _get_setting('grafana_api_token')           → fetchval
    3. _get_setting('grafana_api_base_url')        → fetchval
    4. SELECT ... FROM alert_rules WHERE enabled   → fetch
    5. SELECT ... FROM brain_knowledge (hash cache)→ fetch
    6. per-rule hash upsert                        → execute
    """
    settings = settings or {}
    rows = rows or []
    cached_hashes = cached_hashes or {}

    pool = MagicMock()

    # fetchval — _get_setting. Order: enabled, token, base_url.
    pool.fetchval = AsyncMock(side_effect=[
        settings.get("grafana_alert_sync_enabled", "true"),
        settings.get("grafana_api_token", "tok-abc123"),
        settings.get("grafana_api_base_url", "http://grafana.test:3000"),
    ])

    # fetch — alert_rules, then hash cache.
    hash_rows = [
        {"attribute": f"hash:{name}", "value": h}
        for name, h in cached_hashes.items()
    ]
    pool.fetch = AsyncMock(side_effect=[rows, hash_rows])

    pool.execute = AsyncMock()

    return pool


class _FakeResponse:
    """Minimal object urllib.request.urlopen returns on 2xx."""
    def __init__(self, status: int, body: bytes = b"{}"):
        self.status = status
        self._body = body

    def read(self):
        return self._body


def _http_2xx(status: int = 200, body: bytes = b"{}"):
    """Factory for a urlopen mock that returns 2xx."""
    def _inner(req, timeout=None):
        return _FakeResponse(status, body)
    return _inner


def _http_unreachable(reason: str = "Connection refused"):
    """Factory for a urlopen mock that raises URLError (server down)."""
    err = urllib.error.URLError(reason)
    def _inner(req, timeout=None):
        raise err
    return _inner


def _http_4xx(status: int = 400, body: bytes = b'{"error":"bad"}'):
    """Factory for a urlopen mock that raises HTTPError (4xx/5xx)."""
    def _inner(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url if hasattr(req, "full_url") else "url",
            status, "err", {}, BytesIO(body),
        )
    return _inner


# --------------------------------------------------------------------------- #
# rule_to_grafana_payload                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestRuleToGrafanaPayload:
    def test_returns_expected_top_level_shape(self):
        payload = asx.rule_to_grafana_payload(_sample_row())
        # Required Grafana provisioning fields:
        for key in ("uid", "title", "folderUID", "ruleGroup", "condition",
                    "noDataState", "execErrState", "for", "labels",
                    "annotations", "data"):
            assert key in payload, f"missing {key}"

    def test_uid_is_stable_for_same_name(self):
        row = _sample_row(name="Stable Rule")
        assert (
            asx.rule_to_grafana_payload(row)["uid"]
            == asx.rule_to_grafana_payload(row)["uid"]
        )

    def test_uid_differs_for_different_names(self):
        a = asx.rule_to_grafana_payload(_sample_row(name="Rule A"))["uid"]
        b = asx.rule_to_grafana_payload(_sample_row(name="Rule B"))["uid"]
        assert a != b

    def test_severity_is_promoted_to_label(self):
        payload = asx.rule_to_grafana_payload(_sample_row(severity="critical"))
        assert payload["labels"]["severity"] == "critical"

    def test_query_is_embedded_as_refId_A_expr(self):
        row = _sample_row(promql_query="up{job='worker'}")
        payload = asx.rule_to_grafana_payload(row)
        stage_a = next(d for d in payload["data"] if d["refId"] == "A")
        assert stage_a["model"]["expr"] == "up{job='worker'}"

    def test_threshold_lands_in_refId_B_evaluator(self):
        payload = asx.rule_to_grafana_payload(_sample_row(threshold=42.5))
        stage_b = next(d for d in payload["data"] if d["refId"] == "B")
        params = stage_b["model"]["conditions"][0]["evaluator"]["params"]
        assert params == [42.5]

    def test_json_strings_decode_transparently(self):
        """labels_json / annotations_json may arrive as JSON strings
        (edge case of asyncpg fallback) — the helper must decode them."""
        row = _sample_row(
            labels_json='{"team":"x"}',
            annotations_json='{"summary":"y"}',
        )
        payload = asx.rule_to_grafana_payload(row)
        assert payload["labels"]["team"] == "x"
        assert payload["annotations"]["summary"] == "y"


# --------------------------------------------------------------------------- #
# _hash_rule                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestHashRule:
    def test_hash_is_deterministic(self):
        row = _sample_row()
        assert asx._hash_rule(row) == asx._hash_rule(row)

    def test_hash_changes_when_query_changes(self):
        a = asx._hash_rule(_sample_row(promql_query="foo"))
        b = asx._hash_rule(_sample_row(promql_query="bar"))
        assert a != b

    def test_hash_changes_when_threshold_changes(self):
        a = asx._hash_rule(_sample_row(threshold=10.0))
        b = asx._hash_rule(_sample_row(threshold=20.0))
        assert a != b

    def test_hash_changes_when_labels_change(self):
        a = asx._hash_rule(_sample_row(labels_json={"a": "1"}))
        b = asx._hash_rule(_sample_row(labels_json={"a": "2"}))
        assert a != b

    def test_hash_invariant_under_label_dict_insertion_order(self):
        """Python dicts preserve insertion order; the hash must not."""
        a = asx._hash_rule(_sample_row(labels_json={"team": "x", "env": "y"}))
        b = asx._hash_rule(_sample_row(labels_json={"env": "y", "team": "x"}))
        assert a == b

    def test_hash_invariant_whether_labels_arrive_as_dict_or_json_string(self):
        a = asx._hash_rule(_sample_row(labels_json={"team": "x"}))
        b = asx._hash_rule(_sample_row(labels_json='{"team": "x"}'))
        assert a == b


# --------------------------------------------------------------------------- #
# sync_alert_rules                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncAlertRulesDisabledOrUnconfigured:
    """Config-gated early returns — DB queries should be minimal and
    Grafana should not be called."""

    async def test_disabled_switch_returns_without_http(self):
        pool = _mock_pool(settings={"grafana_alert_sync_enabled": "false"})
        with patch("urllib.request.urlopen") as urlopen:
            summary = await asx.sync_alert_rules(pool)
        urlopen.assert_not_called()
        assert summary["enabled"] is False
        assert "disabled" in summary["error"]

    async def test_empty_token_returns_without_http_and_warns(self, caplog):
        import logging
        # Reset module-level skip counter so tests are order-independent
        # (the fail-loud escalation in sync_alert_rules accumulates state
        # across calls — see _empty_token_skips).
        asx._empty_token_skips = 0
        asx._empty_token_alarm_at = 4
        pool = _mock_pool(settings={"grafana_api_token": ""})
        with patch("urllib.request.urlopen") as urlopen, \
             caplog.at_level(logging.WARNING, logger="brain.alert_sync"):
            summary = await asx.sync_alert_rules(pool)
        urlopen.assert_not_called()
        assert summary["enabled"] is False
        assert "token" in summary["error"]
        assert any("token" in r.getMessage().lower() for r in caplog.records)
        # First skip increments the counter but does NOT fire the alarm.
        assert summary["empty_token_skip_count"] == 1

    async def test_empty_token_alarm_fires_at_threshold(self):
        """After N consecutive empty-token cycles the sync MUST page the
        operator — the silent-failure pattern this fix closes. Default
        threshold is 4 cycles (~1 h at the standard 15-min cadence).
        """
        asx._empty_token_skips = 0
        asx._empty_token_alarm_at = 4
        notify_calls = []

        async def fake_notify(message, *, pool=None):  # noqa: ARG001
            notify_calls.append(message)
            return {"telegram_message_id": 1}

        # Patch the lazy-imported notify symbol on the brain_daemon module
        # the helper imports from. We can't pre-stub the import path
        # because the helper imports inside the function — patch the
        # resolved attribute instead.
        import types
        fake_module = types.ModuleType("brain_daemon")
        fake_module.notify = fake_notify  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"brain_daemon": fake_module}):
            pool = _mock_pool(settings={"grafana_api_token": ""})
            with patch("urllib.request.urlopen"):
                summaries = []
                for _ in range(4):
                    # Each iteration constructs a fresh pool because the
                    # AsyncMock side_effect list is consumed per call.
                    summaries.append(
                        await asx.sync_alert_rules(
                            _mock_pool(settings={"grafana_api_token": ""}),
                        )
                    )
        # Cycles 1-3: warn only. Cycle 4: alarm fires.
        assert summaries[0]["empty_token_skip_count"] == 1
        assert summaries[3]["empty_token_skip_count"] == 4
        assert len(notify_calls) == 1, (
            f"expected one operator page after 4 silent skips, "
            f"got {len(notify_calls)}"
        )
        assert "grafana_api_token" in notify_calls[0]
        assert "60 min" in notify_calls[0] or "4 cycle" in notify_calls[0]

    async def test_token_present_resets_skip_counter(self):
        """A successful sync (token present) must reset the counter so a
        temporary misconfig doesn't permanently mute the alarm.
        """
        asx._empty_token_skips = 3
        asx._empty_token_alarm_at = 8
        pool = _mock_pool(rows=[])  # token defaults to "tok-abc123"
        with patch("urllib.request.urlopen", side_effect=_http_2xx()):
            await asx.sync_alert_rules(pool)
        assert asx._empty_token_skips == 0
        assert asx._empty_token_alarm_at == 4


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncAlertRulesHappyPath:
    async def test_all_changed_rules_are_pushed(self):
        rows = [_sample_row(name="R1"), _sample_row(name="R2")]
        pool = _mock_pool(rows=rows)
        # 200 response to first call for each rule (PUT upserts).
        with patch("urllib.request.urlopen", side_effect=_http_2xx()):
            summary = await asx.sync_alert_rules(pool)
        assert summary["enabled"] is True
        assert summary["rules_total"] == 2
        assert summary["rules_synced"] == 2
        assert summary["rules_failed"] == 0
        # Hash upsert called once per synced rule.
        assert pool.execute.await_count >= 2

    async def test_unchanged_rule_is_skipped(self):
        row = _sample_row(name="stable")
        cached = {"stable": asx._hash_rule(row)}
        pool = _mock_pool(rows=[row], cached_hashes=cached)
        with patch("urllib.request.urlopen", side_effect=_http_2xx()) as urlopen:
            summary = await asx.sync_alert_rules(pool)
        urlopen.assert_not_called()
        assert summary["rules_unchanged"] == 1
        assert summary["rules_synced"] == 0

    async def test_put_404_falls_back_to_post(self):
        row = _sample_row(name="new-rule")
        pool = _mock_pool(rows=[row])
        calls = {"n": 0}

        def _handler(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                # First call is PUT → 404 (rule doesn't exist yet)
                raise urllib.error.HTTPError(
                    "u", 404, "not found", {}, BytesIO(b'{"msg":"nope"}'),
                )
            # Second call is POST → 201
            return _FakeResponse(201)

        with patch("urllib.request.urlopen", side_effect=_handler):
            summary = await asx.sync_alert_rules(pool)
        assert summary["rules_synced"] == 1
        assert calls["n"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncAlertRulesErrorHandling:
    """AC #5 — Grafana unreachable must log + skip, never crash. 4xx
    must be counted as a failure but the cycle must continue to the
    next rule (so one bad rule doesn't block the rest)."""

    async def test_grafana_unreachable_logs_and_returns_cleanly(self, caplog):
        import logging
        rows = [_sample_row(name="R1"), _sample_row(name="R2")]
        pool = _mock_pool(rows=rows)

        with patch(
            "urllib.request.urlopen",
            side_effect=_http_unreachable("Connection refused"),
        ), caplog.at_level(logging.WARNING, logger="brain.alert_sync"):
            summary = await asx.sync_alert_rules(pool)

        # Did NOT crash.
        assert summary["enabled"] is True
        # Surfaced the failure.
        assert "unreachable" in summary["error"].lower()
        assert summary["rules_failed"] >= 1
        # Warned the operator.
        assert any(
            "unreachable" in r.getMessage().lower() for r in caplog.records
        )

    async def test_4xx_logs_and_continues_to_next_rule(self, caplog):
        import logging
        rows = [_sample_row(name="bad"), _sample_row(name="good")]
        pool = _mock_pool(rows=rows)
        calls = {"n": 0}

        def _handler(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                # First rule PUT → 400
                raise urllib.error.HTTPError(
                    "u", 400, "bad", {}, BytesIO(b'{"msg":"nope"}'),
                )
            # Second rule PUT → 200
            return _FakeResponse(200)

        with patch("urllib.request.urlopen", side_effect=_handler), \
             caplog.at_level(logging.WARNING, logger="brain.alert_sync"):
            summary = await asx.sync_alert_rules(pool)

        assert summary["rules_failed"] == 1
        assert summary["rules_synced"] == 1

    async def test_missing_alert_rules_table_is_handled(self):
        """Rerunning before migration 0073 has applied must not crash."""
        import asyncpg
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=["true", "tok", "http://g"])
        pool.fetch = AsyncMock(side_effect=asyncpg.exceptions.UndefinedTableError(
            "relation alert_rules does not exist"
        ))
        pool.execute = AsyncMock()
        summary = await asx.sync_alert_rules(pool)
        assert summary["rules_total"] == 0
        assert "alert_rules table" in summary["error"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestMaybeSyncGrafanaAlertsCadence:
    """The counter in brain_daemon must only fire sync every N cycles."""

    async def test_counter_fires_sync_every_interval(self):
        from brain import brain_daemon as bd

        # Reset the module-level counter so tests are independent.
        bd._alert_sync_cycle_counter = 0
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value="3")  # interval = 3

        with patch.object(bd, "sync_alert_rules", new=AsyncMock()) as sync_mock:
            # Cycles 1 & 2 → no sync. Cycle 3 → sync + reset.
            await bd._maybe_sync_grafana_alerts(pool)
            await bd._maybe_sync_grafana_alerts(pool)
            assert sync_mock.await_count == 0
            await bd._maybe_sync_grafana_alerts(pool)
            assert sync_mock.await_count == 1
            # Counter reset → next sync is 3 cycles away.
            await bd._maybe_sync_grafana_alerts(pool)
            await bd._maybe_sync_grafana_alerts(pool)
            assert sync_mock.await_count == 1
            await bd._maybe_sync_grafana_alerts(pool)
            assert sync_mock.await_count == 2

    async def test_interval_zero_disables_sync(self):
        from brain import brain_daemon as bd

        bd._alert_sync_cycle_counter = 0
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value="0")

        with patch.object(bd, "sync_alert_rules", new=AsyncMock()) as sync_mock:
            for _ in range(5):
                await bd._maybe_sync_grafana_alerts(pool)
            assert sync_mock.await_count == 0

    async def test_sync_failure_does_not_propagate(self):
        from brain import brain_daemon as bd

        bd._alert_sync_cycle_counter = 0
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value="1")

        with patch.object(
            bd, "sync_alert_rules",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            # Must NOT raise.
            await bd._maybe_sync_grafana_alerts(pool)
