"""Click CLI tests for ``poindexter post approve --filter ...`` (bulk mode).

Glad-Labs/poindexter#338 — bulk-approval bullet of the gate-system polish
issue. Bulk mode is a UX shortcut: it resolves a strict-allowlist
filter into a list of post ids, then re-uses the single-post
``approve_gate`` service per match. Each call still writes
audit_log + pipeline_gate_history rows and fires the dispatcher
webhook, so bulk = N single approves, not one bulk SQL UPDATE.

The CLI builds its own ``asyncpg`` pool inline (matching the
``poindexter post create`` / ``poindexter topics`` commands), so we
patch ``asyncpg.create_pool`` via ``sys.modules`` and stub
``services.gates.post_approval_gates`` so the suite exercises the Click
glue (option parsing, dry-run guard, ceiling enforcement, confirmation
prompt, streaming progress lines, exit codes) without a live DB.

Filter-parser unit tests live in test_post_approve_filter.py — this
file covers the CLI integration end of the wiring.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from click.testing import CliRunner

from poindexter.cli.posts import post_group


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _async_conn(*, fetch_result=None) -> Any:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_result or [])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.create_pool`` so the CLI never reaches a real DB.

    The CLI does ``import asyncpg`` lazily inside ``_make_gate_pool``, so
    patching ``sys.modules['asyncpg']`` is in place by the time that
    import resolves. The ``fetch_payload`` attribute on the returned
    dict can be reassigned by the test so each test scopes its own
    matched-rows result.
    """
    state: dict[str, Any] = {"fetch_payload": []}
    conn = _async_conn()

    async def _fetch_dispatch(*_args, **_kwargs):
        return list(state["fetch_payload"])

    conn.fetch = AsyncMock(side_effect=_fetch_dispatch)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)

    async def _create_pool(_dsn, **_kwargs):
        return pool

    asyncpg = MagicMock()
    asyncpg.create_pool = _create_pool

    with patch.dict("sys.modules", {"asyncpg": asyncpg}):
        yield {
            "state": state,
            "conn": conn,
            "pool": pool,
            "asyncpg": asyncpg,
        }


@pytest.fixture
def fake_site_config():
    """Stub ``services.site_config.SiteConfig`` with knob defaults.

    The bulk command reads two settings:
    ``cli_post_approve_bulk_max_count`` (default 100) and
    ``cli_post_approve_bulk_require_confirm`` (default 'true'). Tests
    that need a tighter ceiling or a non-prompting run override these
    via the ``settings`` dict.
    """
    settings: dict[str, Any] = {
        "cli_post_approve_bulk_max_count": 100,
        "cli_post_approve_bulk_require_confirm": "false",
    }

    class _Stub:
        def __init__(self, **_kwargs):
            self._settings = settings

        async def load(self, _pool):
            return None

        def get(self, key, default=None):
            return self._settings.get(key, default)

    site_module = MagicMock()
    site_module.SiteConfig = _Stub
    with patch.dict("sys.modules", {"services.site_config": site_module}):
        yield settings


@pytest.fixture
def fake_gates():
    """Stub ``services.gates.post_approval_gates`` for the bulk command.

    Tests reach into the returned dict to control behaviour — set
    ``next_pending`` to None to simulate "no pending gate" or assign
    ``approve_side_effect`` to make ``approve_gate`` raise.
    """
    state: dict[str, Any] = {
        "next_pending": {"gate_name": "draft", "ordinal": 1},
        "approve_side_effect": None,
        "approve_calls": [],
    }

    class GateServiceError(Exception):
        pass

    async def _approve_gate(_pool, post_id, gate_name, **kwargs):
        state["approve_calls"].append(
            {"post_id": post_id, "gate_name": gate_name, **kwargs}
        )
        side = state["approve_side_effect"]
        if side is not None:
            if isinstance(side, dict):
                exc = side.get(post_id)
                if exc is not None:
                    raise exc
            else:
                raise side
        return {
            "id": str(uuid4()),
            "post_id": post_id,
            "gate_name": gate_name,
            "state": "approved",
        }

    async def _advance_workflow(_pool, _post_id):
        # Bulk path discards the return value; single path only reaches
        # ``.__dict__`` after asyncio.run, so a tiny stub is enough.
        class _Advance:
            __dict__ = {"next_gate": None, "ready_to_distribute": True}
        return _Advance()

    async def _get_next_pending_gate(_pool, _post_id):
        return state["next_pending"]

    module = MagicMock()
    module.GateServiceError = GateServiceError
    module.approve_gate = _approve_gate
    module.advance_workflow = _advance_workflow
    module.get_next_pending_gate = _get_next_pending_gate
    module.CANONICAL_GATE_NAMES = (
        "topic", "draft", "podcast", "video", "short", "final",
        "media_generation_failed",
    )

    with patch.dict(
        "sys.modules",
        {"services.gates.post_approval_gates": module},
    ):
        yield state


def _post_row(*, post_id: str | None = None, title: str = "Hello",
              created_at: str = "2026-05-01T00:00:00Z") -> dict[str, Any]:
    return {
        "id": post_id or str(uuid4()),
        "title": title,
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# (1) Dry-run is the default — no approves fired
# ---------------------------------------------------------------------------


class TestDryRunDefault:
    def test_dry_run_default_prints_count_and_sample_no_approve_calls(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        rows = [
            _post_row(title="One"),
            _post_row(title="Two"),
            _post_row(title="Three"),
        ]
        fake_asyncpg["state"]["fetch_payload"] = rows

        result = runner.invoke(
            post_group,
            ["approve", "--filter", "state=draft"],
        )

        assert result.exit_code == 0, result.output
        assert "[dry-run]" in result.output
        assert "3 post(s) match" in result.output
        # Sample is rendered.
        assert "One" in result.output
        assert "Two" in result.output
        assert "Three" in result.output
        # Hint to escalate.
        assert "--no-dry-run" in result.output
        # Critically: zero approve_gate calls.
        assert fake_gates["approve_calls"] == []


# ---------------------------------------------------------------------------
# (2) --no-dry-run --yes streams + summarises and calls approve_gate per row
# ---------------------------------------------------------------------------


class TestExecuteWithYes:
    def test_executes_one_approve_per_match_and_summarises(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        # Toggle the global confirm requirement off so --yes wins.
        fake_site_config["cli_post_approve_bulk_require_confirm"] = "false"
        ids = [str(uuid4()) for _ in range(3)]
        rows = [
            _post_row(post_id=ids[0], title="Alpha"),
            _post_row(post_id=ids[1], title="Bravo"),
            _post_row(post_id=ids[2], title="Charlie"),
        ]
        fake_asyncpg["state"]["fetch_payload"] = rows

        result = runner.invoke(
            post_group,
            ["approve", "--filter", "state=draft", "--no-dry-run", "--yes"],
        )

        assert result.exit_code == 0, result.output
        # Streaming progress: one line per match.
        assert "Approving post 1/3:" in result.output
        assert "Approving post 2/3:" in result.output
        assert "Approving post 3/3:" in result.output
        # Final summary tally.
        assert "Bulk approved 3 posts" in result.output
        assert "(0 failed)" in result.output
        # One approve_gate call per matched row, in order.
        assert [c["post_id"] for c in fake_gates["approve_calls"]] == ids
        # All approvals used the post's next pending gate name.
        assert {c["gate_name"] for c in fake_gates["approve_calls"]} == {"draft"}


# ---------------------------------------------------------------------------
# (3) Date predicate parameterises correctly
# ---------------------------------------------------------------------------


class TestDateFilterParameterisation:
    def test_created_after_binds_iso_timestamp_as_param(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        fake_asyncpg["state"]["fetch_payload"] = []

        result = runner.invoke(
            post_group,
            [
                "approve",
                "--filter", "created_after=2026-05-01T00:00:00Z",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.output
        # Inspect the SQL we passed to ``conn.fetch``: parameter
        # placeholder must be in the SQL, datetime must be the param.
        call = fake_asyncpg["conn"].fetch.await_args
        sql = call.args[0]
        # The condition includes the placeholder, NOT the literal value.
        assert "posts.created_at > $1" in sql
        assert "2026-05-01" not in sql  # value must NOT appear inline
        # The bound param is a datetime in UTC, parsed from the ISO input.
        params = call.args[1:]
        assert len(params) == 1
        from datetime import datetime, timezone
        assert isinstance(params[0], datetime)
        assert params[0] == datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# (4) Invalid column name in filter -> clear error, exit code 2
# ---------------------------------------------------------------------------


class TestInvalidColumn:
    def test_unknown_column_exits_2_with_clear_error(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        result = runner.invoke(
            post_group,
            ["approve", "--filter", "wrong_col=draft"],
        )

        assert result.exit_code == 2, result.output
        assert "unknown filter column" in result.output.lower()
        # No DB access attempted.
        fake_asyncpg["conn"].fetch.assert_not_called()
        assert fake_gates["approve_calls"] == []

    def test_invalid_state_value_rejected(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        result = runner.invoke(
            post_group,
            ["approve", "--filter", "state=not_a_real_gate"],
        )

        assert result.exit_code == 2, result.output
        assert "canonical gate name" in result.output.lower()


# ---------------------------------------------------------------------------
# (5) Match count exceeds ceiling -> refuse without --max
# ---------------------------------------------------------------------------


class TestCeilingEnforcement:
    def test_exceeding_ceiling_refuses_without_max_override(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        fake_site_config["cli_post_approve_bulk_max_count"] = 3
        fake_site_config["cli_post_approve_bulk_require_confirm"] = "false"
        # Five matches, ceiling is 3.
        rows = [_post_row(title=f"P{i}") for i in range(5)]
        fake_asyncpg["state"]["fetch_payload"] = rows

        result = runner.invoke(
            post_group,
            ["approve", "--filter", "state=draft", "--no-dry-run", "--yes"],
        )

        assert result.exit_code != 0
        assert "refusing to approve more than" in result.output.lower()
        # Critical: no approve_gate calls were made.
        assert fake_gates["approve_calls"] == []

    def test_max_override_below_ceiling_still_refuses_when_exceeded(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        fake_site_config["cli_post_approve_bulk_max_count"] = 100
        fake_site_config["cli_post_approve_bulk_require_confirm"] = "false"
        rows = [_post_row(title=f"P{i}") for i in range(5)]
        fake_asyncpg["state"]["fetch_payload"] = rows

        result = runner.invoke(
            post_group,
            [
                "approve",
                "--filter", "state=draft",
                "--no-dry-run", "--yes",
                "--max", "2",
            ],
        )

        assert result.exit_code != 0
        assert "refusing" in result.output.lower()
        assert fake_gates["approve_calls"] == []


# ---------------------------------------------------------------------------
# (6) --no-dry-run without --yes + stdin "n" -> bails out
# ---------------------------------------------------------------------------


class TestConfirmationPrompt:
    def test_n_at_prompt_aborts_without_approving(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        fake_site_config["cli_post_approve_bulk_require_confirm"] = "false"
        rows = [_post_row(title="One"), _post_row(title="Two")]
        fake_asyncpg["state"]["fetch_payload"] = rows

        result = runner.invoke(
            post_group,
            ["approve", "--filter", "state=draft", "--no-dry-run"],
            input="n\n",
        )

        assert result.exit_code == 0, result.output
        # The prompt fired (text appears in output).
        assert "About to approve 2" in result.output
        assert "Aborted" in result.output
        assert fake_gates["approve_calls"] == []

    def test_y_at_prompt_proceeds(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        fake_site_config["cli_post_approve_bulk_require_confirm"] = "false"
        rows = [_post_row(title="Only")]
        fake_asyncpg["state"]["fetch_payload"] = rows

        result = runner.invoke(
            post_group,
            ["approve", "--filter", "state=draft", "--no-dry-run"],
            input="y\n",
        )

        assert result.exit_code == 0, result.output
        assert "About to approve 1" in result.output
        assert "Bulk approved 1 posts" in result.output
        assert len(fake_gates["approve_calls"]) == 1


# ---------------------------------------------------------------------------
# (7) per-post approve_gate raises -> tally, continue, don't crash
# ---------------------------------------------------------------------------


class TestPerPostFailureContinues:
    def test_failures_tally_and_other_posts_still_approved(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        fake_site_config["cli_post_approve_bulk_require_confirm"] = "false"
        ids = [str(uuid4()) for _ in range(3)]
        rows = [
            _post_row(post_id=ids[0], title="Alpha"),
            _post_row(post_id=ids[1], title="Bravo (boom)"),
            _post_row(post_id=ids[2], title="Charlie"),
        ]
        fake_asyncpg["state"]["fetch_payload"] = rows

        # Make the middle post raise GateServiceError.
        from services.gates.post_approval_gates import GateServiceError
        fake_gates["approve_side_effect"] = {
            ids[1]: GateServiceError("Gate already approved"),
        }

        result = runner.invoke(
            post_group,
            ["approve", "--filter", "state=draft", "--no-dry-run", "--yes"],
        )

        # Partial failure -> exit code 1.
        assert result.exit_code == 1, result.output
        # All three were attempted, even though the second failed.
        attempted = [c["post_id"] for c in fake_gates["approve_calls"]]
        assert attempted == ids
        # Failure line appears in the output.
        assert "FAIL" in result.output
        # Summary distinguishes successes from failures.
        assert "Bulk approved 2 posts" in result.output
        assert "(1 failed)" in result.output


# ---------------------------------------------------------------------------
# (Bonus) Single-mode regression — bulk flags shouldn't break the
# original `poindexter post approve <id> --gate <name>` path.
# ---------------------------------------------------------------------------


class TestSingleModeRegression:
    def test_bulk_flag_with_positional_id_is_rejected(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        result = runner.invoke(
            post_group,
            [
                "approve", str(uuid4()),
                "--filter", "state=draft",
            ],
        )
        assert result.exit_code == 2
        assert "post_id is not allowed with --filter" in result.output

    def test_missing_gate_in_single_mode_is_rejected(
        self, runner, fake_asyncpg, fake_site_config, fake_gates,
    ):
        result = runner.invoke(
            post_group,
            ["approve", str(uuid4())],
        )
        assert result.exit_code == 2
        assert "missing --gate" in result.output.lower()
