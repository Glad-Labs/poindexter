"""Unit tests for brain/alert_dispatcher.py.

Covers the contract from Glad-Labs/poindexter#340 prep:

1. Polls only undispatched rows (``WHERE dispatched_at IS NULL``).
2. Calls ``notify_operator(message, critical=...)`` with the formatted
   message + ``critical=True`` for ``severity=='critical'``.
3. Marks each row ``dispatched_at = NOW(), dispatch_result = 'sent'`` on
   success.
4. Marks each row ``dispatch_result = 'error: <msg>'`` when the notify
   call raises — without crashing the loop.
5. Best-effort poll: a DB error in the SELECT step returns an empty
   summary instead of propagating.

All DB I/O is mocked via AsyncMock (mirrors the pattern in
test_brain_daemon_auto_remediate.py).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern used by test_brain_daemon_auto_remediate.py
# so the import resolves before the import below.
#
# Path: tests/unit/services/test_brain_alert_dispatcher.py
# parents[0] = services/
# parents[1] = unit/
# parents[2] = tests/
# parents[3] = cofounder_agent/
# parents[4] = src/
# parents[5] = repo root (contains brain/)
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import alert_dispatcher as ad  # noqa: E402


def _make_row(
    *,
    row_id: int,
    alertname: str = "PoindexterPostgresDown",
    status: str = "firing",
    severity: str = "critical",
    category: str = "infrastructure",
    summary: str = "pg down",
    description: str = "",
) -> dict:
    """Build a dict in the shape ``alert_events`` rows return from asyncpg."""
    import json

    labels = {
        "alertname": alertname,
        "severity": severity,
        "category": category,
    }
    annotations = {"summary": summary}
    if description:
        annotations["description"] = description
    return {
        "id": row_id,
        "alertname": alertname,
        "status": status,
        "severity": severity,
        "category": category,
        "labels": json.dumps(labels),
        "annotations": json.dumps(annotations),
    }


def _make_pool(rows: list[dict]) -> MagicMock:
    """Build a pool whose .fetch returns ``rows`` once, then [].

    Each test invokes ``poll_and_dispatch`` once so the side_effect list
    only needs the first response. ``execute`` is a plain AsyncMock —
    tests assert on its call_args_list to verify the mark-as-sent /
    mark-as-error queries fire with the right (sql, *args).
    """
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.execute = AsyncMock(return_value="OK")
    return pool


@pytest.mark.unit
@pytest.mark.asyncio
class TestPollAndDispatch:
    """Happy path + failure path for the brain dispatcher."""

    async def test_polls_only_undispatched_rows(self):
        """The poll SQL must filter on ``dispatched_at IS NULL``.

        Without this clause the brain would re-page every historical
        row on every cycle.
        """
        pool = _make_pool([])
        notify = AsyncMock(return_value=None)
        result = await ad.poll_and_dispatch(pool, notify_fn=notify)

        assert pool.fetch.await_count == 1
        sql = pool.fetch.call_args.args[0]
        assert "FROM alert_events" in sql
        assert "dispatched_at IS NULL" in sql
        # Default batch size threads through.
        assert pool.fetch.call_args.args[1] == 50
        assert result == {"polled": 0, "sent": 0, "errors": 0}
        notify.assert_not_awaited()

    async def test_critical_dispatches_with_critical_flag_and_marks_sent(self):
        """Critical row → notify(critical=True) + mark dispatched."""
        rows = [_make_row(row_id=42, severity="critical")]
        pool = _make_pool(rows)
        notify = AsyncMock(return_value=None)

        result = await ad.poll_and_dispatch(pool, notify_fn=notify)

        # Notify called once, with critical=True and a formatted message
        # that includes the alertname + severity tag.
        notify.assert_awaited_once()
        call_args = notify.call_args
        message = call_args.args[0]
        assert "PoindexterPostgresDown" in message
        assert "critical" in message
        assert call_args.kwargs.get("critical") is True

        # And the row was marked sent (not errored).
        exec_calls = pool.execute.await_args_list
        assert len(exec_calls) == 1
        sql, *args = exec_calls[0].args
        assert "UPDATE alert_events" in sql
        assert "dispatched_at = NOW()" in sql
        assert "dispatch_result = 'sent'" in sql
        assert args[0] == 42

        assert result == {"polled": 1, "sent": 1, "errors": 0}

    async def test_warning_dispatches_with_critical_false(self):
        """Non-critical severities pass critical=False so the dispatcher
        routes to Discord instead of Telegram."""
        rows = [_make_row(row_id=7, severity="warning", category="content")]
        pool = _make_pool(rows)
        notify = AsyncMock(return_value=None)

        await ad.poll_and_dispatch(pool, notify_fn=notify)

        notify.assert_awaited_once()
        assert notify.call_args.kwargs.get("critical") is False

    async def test_notify_failure_marks_row_with_error_and_continues(self):
        """A raise from notify_operator must NOT crash the loop.

        The row gets marked ``dispatch_result = 'error: ...'`` and the
        next row in the batch is processed normally.
        """
        rows = [
            _make_row(row_id=1, severity="critical", alertname="First"),
            _make_row(row_id=2, severity="warning", alertname="Second"),
        ]
        pool = _make_pool(rows)

        # First call raises, second succeeds.
        notify = AsyncMock(side_effect=[
            RuntimeError("telegram timeout"),
            None,
        ])

        result = await ad.poll_and_dispatch(pool, notify_fn=notify)

        # Both rows attempted.
        assert notify.await_count == 2

        # Two execute calls: row 1 marked as error, row 2 marked as sent.
        assert pool.execute.await_count == 2
        first_call = pool.execute.await_args_list[0]
        first_sql, *first_args = first_call.args
        assert "UPDATE alert_events" in first_sql
        assert "dispatch_result = $2" in first_sql
        assert first_args[0] == 1
        # The 'error: ' prefix + the actual exception text get persisted.
        assert first_args[1].startswith("error:")
        assert "telegram timeout" in first_args[1]

        second_call = pool.execute.await_args_list[1]
        second_sql, *second_args = second_call.args
        assert "dispatch_result = 'sent'" in second_sql
        assert second_args[0] == 2

        assert result == {"polled": 2, "sent": 1, "errors": 1}

    async def test_db_poll_error_returns_empty_summary_without_raising(self):
        """A DB SELECT failure must not crash the brain cycle.

        Most likely cause is migration 0137 not having run yet, in which
        case ``dispatched_at`` doesn't exist. The dispatcher should log
        + return an empty summary; the brain's outer loop keeps polling
        on its 30s cadence.
        """
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=Exception(
            "column \"dispatched_at\" does not exist"
        ))
        pool.execute = AsyncMock()
        notify = AsyncMock(return_value=None)

        result = await ad.poll_and_dispatch(pool, notify_fn=notify)

        assert result == {"polled": 0, "sent": 0, "errors": 0}
        notify.assert_not_awaited()
        pool.execute.assert_not_awaited()

    async def test_no_notify_channel_marks_rows_as_errored(self):
        """If neither worker-side notify nor brain.notify is reachable,
        the dispatcher marks every polled row with a clear error string
        instead of leaving them undispatched (which would re-poll
        forever)."""
        rows = [_make_row(row_id=99)]
        pool = _make_pool(rows)

        # Pass notify_fn=None AND patch the resolver to return None so
        # the "no channel reachable" branch fires deterministically.
        async def _no_channel():
            return None

        result = await ad.poll_and_dispatch(
            pool,
            notify_fn=None,
        )
        # The resolver may find a real channel in some test environments
        # (e.g. brain_daemon imported with a sync notify). The contract
        # we care about is: when nothing is reachable, the row is
        # marked, and we never re-poll the same row. Force the no-channel
        # path explicitly via a patch in case the resolver picks up
        # something stub-like.
        if result["sent"] or (result["errors"] and "no notify channel" not in
                              str(pool.execute.await_args_list)):
            # Real channel resolved — re-run with explicit None override
            # by patching _resolve_notify_fn.
            pool.fetch = AsyncMock(return_value=rows)
            pool.execute = AsyncMock(return_value="OK")
            from unittest.mock import patch
            with patch.object(ad, "_resolve_notify_fn", _no_channel):
                result = await ad.poll_and_dispatch(pool, notify_fn=None)

        assert result["polled"] == 1
        assert result["errors"] == 1
        assert result["sent"] == 0
        # The error-mark UPDATE was the call we made.
        exec_call = pool.execute.await_args_list[-1]
        sql, *args = exec_call.args
        assert "UPDATE alert_events" in sql
        assert args[0] == 99
        assert "no notify channel" in args[1]

    async def test_batch_size_threads_through_to_query(self):
        """Caller-provided batch_size lands in the LIMIT param."""
        pool = _make_pool([])
        notify = AsyncMock(return_value=None)
        await ad.poll_and_dispatch(pool, batch_size=10, notify_fn=notify)
        assert pool.fetch.call_args.args[1] == 10


@pytest.mark.unit
class TestFormatAlertMessage:
    """Mirror of the route-side formatter contract — keep both helpers
    in sync. If you change one, change both (and update both tests)."""

    def test_basic_header(self):
        msg = ad._format_alert_message({
            "status": "firing",
            "labels": {"alertname": "Foo", "severity": "critical"},
            "annotations": {"summary": "short"},
        })
        assert "[FIRING · critical]" in msg
        assert "Foo" in msg
        assert "short" in msg

    def test_appends_description(self):
        msg = ad._format_alert_message({
            "status": "firing",
            "labels": {"alertname": "Foo", "severity": "warning"},
            "annotations": {"summary": "s", "description": "long detail"},
        })
        assert "long detail" in msg

    def test_handles_missing_fields(self):
        # Bare minimum payload — formatter must not raise.
        msg = ad._format_alert_message({})
        assert "UnknownAlert" in msg
        assert "info" in msg


@pytest.mark.unit
@pytest.mark.asyncio
class TestBrainNotifyAdapter:
    """The brain.notify adapter wraps a sync function that returns
    True/False — Glad-Labs/poindexter#342 added the bool contract so
    the dispatcher could record honest dispatch_result values instead
    of phantom 'sent' rows when the operator's pager was actually
    silent.

    These tests construct the adapter directly via _resolve_notify_fn
    and exercise the False/True branches.
    """

    async def test_adapter_raises_notify_failed_when_underlying_returns_false(
        self, monkeypatch,
    ):
        """notify() returning False → adapter raises NotifyFailed.

        Why an exception instead of bool propagation: poll_and_dispatch
        already has a try/except that marks the row with
        ``dispatch_result = 'error: <exc>'``. Raising plugs into that
        path with zero new code; returning False would have required
        a second branch that mirrors the same UPDATE.
        """
        # Stub a brain_daemon-shaped module with notify -> False.
        fake_module = MagicMock()
        fake_module.notify = MagicMock(return_value=False)
        monkeypatch.setitem(sys.modules, "brain_daemon", fake_module)
        # Force the worker-side notify_operator import to fail so the
        # resolver falls through to the brain.notify branch.
        monkeypatch.setitem(sys.modules, "services.integrations.operator_notify", None)

        adapter = await ad._resolve_notify_fn()
        assert adapter is not None

        with pytest.raises(ad.NotifyFailed):
            await adapter("test message", critical=True)

        fake_module.notify.assert_called_once_with("test message")

    async def test_adapter_returns_normally_when_underlying_returns_true(
        self, monkeypatch,
    ):
        """notify() returning True → adapter returns (no exception).

        poll_and_dispatch then marks the row dispatch_result='sent'.
        """
        fake_module = MagicMock()
        fake_module.notify = MagicMock(return_value=True)
        monkeypatch.setitem(sys.modules, "brain_daemon", fake_module)
        monkeypatch.setitem(sys.modules, "services.integrations.operator_notify", None)

        adapter = await ad._resolve_notify_fn()
        assert adapter is not None
        # Should not raise.
        await adapter("test message", critical=False)
        fake_module.notify.assert_called_once()

    async def test_adapter_treats_none_return_as_success(self, monkeypatch):
        """Legacy notify() that returns None (pre-#342) keeps working.

        We only treat ``False`` (explicit identity) as failure so
        callers that haven't been updated yet — or test stubs that
        don't bother returning a value — don't suddenly start
        flagging every row as errored.
        """
        fake_module = MagicMock()
        fake_module.notify = MagicMock(return_value=None)
        monkeypatch.setitem(sys.modules, "brain_daemon", fake_module)
        monkeypatch.setitem(sys.modules, "services.integrations.operator_notify", None)

        adapter = await ad._resolve_notify_fn()
        assert adapter is not None
        # Should not raise.
        await adapter("test message")

    async def test_dispatcher_marks_error_when_notify_returns_false(
        self,
    ):
        """End-to-end: notify returning False → row gets error mark.

        The integration the operator cares about: an alert lands in
        ``alert_events``, the dispatcher polls it, the brain notify
        path returns False (no token, malformed URL, etc.), and the
        row's ``dispatch_result`` honestly reflects that the page
        didn't go out instead of recording a fake 'sent'.
        """
        rows = [_make_row(row_id=42, severity="critical")]
        pool = _make_pool(rows)

        # Inject a notify_fn that mimics the adapter's "raise on False"
        # contract directly, bypassing the resolver chain.
        async def _fail_notify(message: str, *, critical: bool = False) -> None:
            raise ad.NotifyFailed("brain.notify reported no channel")

        result = await ad.poll_and_dispatch(pool, notify_fn=_fail_notify)

        # Row was marked errored, NOT sent.
        assert result == {"polled": 1, "sent": 0, "errors": 1}
        assert pool.execute.await_count == 1
        sql, *args = pool.execute.await_args_list[0].args
        assert "UPDATE alert_events" in sql
        # Uses the parameterized error path, not the literal 'sent' UPDATE.
        assert "dispatch_result = $2" in sql
        assert args[0] == 42
        assert args[1].startswith("error:")
        assert "no channel" in args[1]


@pytest.mark.unit
class TestRowToAlertDict:
    """Reshape from asyncpg row dict back into the Alertmanager-style
    payload the formatter expects."""

    def test_parses_jsonb_strings(self):
        import json

        row = {
            "id": 1,
            "alertname": "X",
            "status": "firing",
            "severity": "critical",
            "category": "infra",
            "labels": json.dumps({"alertname": "X", "severity": "critical"}),
            "annotations": json.dumps({"summary": "ok"}),
        }
        alert = ad._row_to_alert_dict(row)
        assert alert["status"] == "firing"
        assert alert["labels"]["alertname"] == "X"
        assert alert["annotations"]["summary"] == "ok"

    def test_falls_back_to_columns_when_label_jsonb_missing_keys(self):
        # asyncpg may also hand back already-parsed dicts.
        row = {
            "id": 1,
            "alertname": "X",
            "status": "firing",
            "severity": "critical",
            "category": "infra",
            "labels": {},
            "annotations": {},
        }
        alert = ad._row_to_alert_dict(row)
        # Severity + alertname back-filled from the row columns.
        assert alert["labels"]["alertname"] == "X"
        assert alert["labels"]["severity"] == "critical"
        assert alert["labels"]["category"] == "infra"
