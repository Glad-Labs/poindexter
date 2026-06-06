"""Unit tests for ``poindexter settings`` CLI subcommands.

Pins the 2026-05-28 phantom-key behaviour change. The original
2026-05-27 bandaid (reject any key with ``/``, suggest the canonical
key) was replaced with proper UX:

  - ``settings set`` auto-strips the ``category/`` prefix when the
    canonical row exists. Warns on category mismatch (informational).
  - ``settings set`` with ``category/key`` where no canonical row
    exists fails loud and points the operator at ``--allow-new`` +
    bare-key + ``--category``.
  - ``settings set`` with bare key + ``--allow-new`` creates a new row.
  - ``settings set`` with bare key + no canonical row + no
    ``--allow-new`` also fails loud (typo guard).
  - ``settings list`` renders ``key [category] = value`` so the
    leftmost copyable token is the canonical key.

Related regression: Glad-Labs/poindexter#253 (phantom row UPSERT) and
the dev-diary publishing throttle that hid behind the silent failure.

Tests use mocked asyncpg connections — no real DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.settings import (
    _split_category_prefix,
    settings_group,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_asyncpg_connect(*, existing_category: str | None):
    """Patch ``asyncpg.connect`` to return a conn whose ``fetchrow`` either
    returns a canonical row (with the given category) or ``None``.

    Used by the ``settings set`` tests to simulate the canonical row
    being present or absent without spinning up a real DB.
    """
    conn = MagicMock()
    if existing_category is None:
        conn.fetchrow = AsyncMock(return_value=None)
    else:
        # asyncpg returns Record-like objects; a dict with __getitem__
        # is good enough for our access pattern.
        conn.fetchrow = AsyncMock(
            return_value={"key": "__set_by_test__", "category": existing_category},
        )
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.close = AsyncMock()
    return patch(
        "asyncpg.connect", new=AsyncMock(return_value=conn),
    ), conn


def _patch_resolve_dsn():
    return patch(
        "poindexter.cli._bootstrap.resolve_dsn",
        return_value="postgresql://fake/test",
    )


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# _split_category_prefix — the helper itself
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplitCategoryPrefix:

    def test_bare_key_returns_unchanged_no_prefix(self):
        canonical, prefix = _split_category_prefix("daily_post_limit")
        assert canonical == "daily_post_limit"
        assert prefix is None

    def test_single_slash_splits_into_prefix_and_canonical(self):
        canonical, prefix = _split_category_prefix("pipeline/daily_post_limit")
        assert canonical == "daily_post_limit"
        assert prefix == "pipeline"

    def test_rsplit_uses_rightmost_slash(self):
        """If a key has multiple slashes, the canonical part is the
        right-most segment per spec."""
        canonical, prefix = _split_category_prefix("plugin/llm/foo")
        assert canonical == "foo"
        assert prefix == "plugin/llm"


# ---------------------------------------------------------------------------
# settings set — slash-prefix handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsSetSlashHandling:
    """Cover the 7 cases from the 2026-05-28 spec."""

    def test_bare_key_existing_row_updates_canonical(self, runner):
        """Regular case: bare key, canonical exists → silent update."""
        ctx, conn = _patch_asyncpg_connect(existing_category="pipeline")
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "daily_post_limit", "4"],
            )

        assert result.exit_code == 0, result.output
        assert "Updated: daily_post_limit = 4" in result.output
        # The UPSERT was called with the bare key, not a phantom slash form.
        assert conn.execute.await_count == 1
        sql_args = conn.execute.await_args.args
        assert sql_args[1] == "daily_post_limit"
        assert sql_args[2] == "4"

    def test_category_slash_key_existing_matching_category_silently_strips(
        self, runner,
    ):
        """``settings set pipeline/daily_post_limit 4`` where the canonical
        row's category IS ``pipeline`` → silent strip, no warning."""
        ctx, conn = _patch_asyncpg_connect(existing_category="pipeline")
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "pipeline/daily_post_limit", "4"],
            )

        assert result.exit_code == 0, result.output
        assert "Updated: daily_post_limit = 4" in result.output
        # No warning about category mismatch — they matched.
        assert "warning:" not in result.output.lower()
        # UPSERT went to the bare canonical key.
        assert conn.execute.await_args.args[1] == "daily_post_limit"

    def test_category_slash_key_existing_mismatched_category_warns_and_updates(
        self, runner,
    ):
        """Supplied prefix disagrees with row's actual category → warn but
        proceed with canonical key (matches MCP-tool behaviour)."""
        # Canonical row exists, but its category is "quality" not "pipeline".
        ctx, conn = _patch_asyncpg_connect(existing_category="quality")
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "pipeline/daily_post_limit", "4"],
            )

        assert result.exit_code == 0, result.output
        assert "warning" in result.output.lower()
        assert "'pipeline'" in result.output and "'quality'" in result.output
        assert "Updated: daily_post_limit = 4" in result.output
        # UPSERT still went to the bare canonical key (informational warn only).
        assert conn.execute.await_args.args[1] == "daily_post_limit"

    def test_category_slash_key_canonical_missing_fails_loud(self, runner):
        """``settings set pipeline/daily_post_limit 4`` with no canonical
        row → exit 2 with actionable error message."""
        ctx, conn = _patch_asyncpg_connect(existing_category=None)
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "pipeline/daily_post_limit", "4"],
            )

        assert result.exit_code == 2, result.output
        assert "no setting named" in result.output
        assert "daily_post_limit" in result.output
        assert "--allow-new" in result.output
        # Crucially: --category pipeline is suggested (echo back the
        # supplied prefix).
        assert "--category pipeline" in result.output
        # And we did NOT upsert anything.
        assert conn.execute.await_count == 0

    def test_bare_key_missing_no_allow_new_fails_loud(self, runner):
        """Typo guard: ``settings set typo_key 4`` with no canonical row
        AND no ``--allow-new`` → exit 2."""
        ctx, conn = _patch_asyncpg_connect(existing_category=None)
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group, ["set", "typo_key", "4"],
            )

        assert result.exit_code == 2, result.output
        assert "no setting named" in result.output
        assert "typo_key" in result.output
        assert "--allow-new" in result.output
        assert conn.execute.await_count == 0

    def test_bare_key_missing_with_allow_new_creates(self, runner):
        """``settings set new_key 4 --allow-new`` → upsert succeeds."""
        ctx, conn = _patch_asyncpg_connect(existing_category=None)
        with _patch_resolve_dsn(), ctx:
            result = runner.invoke(
                settings_group,
                ["set", "new_key", "4", "--allow-new", "--category", "pipeline"],
            )

        assert result.exit_code == 0, result.output
        assert "Updated: new_key = 4" in result.output
        # The UPSERT was called once with the new bare key.
        assert conn.execute.await_count == 1
        sql_args = conn.execute.await_args.args
        assert sql_args[1] == "new_key"
        assert sql_args[3] == "pipeline"  # category positional arg


# ---------------------------------------------------------------------------
# settings list — leftmost-token-is-canonical format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsListFormat:
    """Pin the new ``key [category] = value`` rendering."""

    def test_active_only_list_renders_bare_key_first(self, runner):
        """Active-only path (HTTP-backed): leftmost token = bare key.

        Regression: previously rendered as ``category/key = value``,
        which trained operators to copy ``category/key`` back into
        ``settings set`` (phantom-key trap).
        """
        # Stub the HTTP layer's response shape.
        fake_data = {
            "items": [
                {
                    "key": "daily_post_limit",
                    "value_preview": "4",
                    "category": "pipeline",
                    "is_encrypted": False,
                },
                {
                    "key": "auto_publish_threshold",
                    "value_preview": "85",
                    "category": "quality",
                    "is_encrypted": False,
                },
            ],
            "total": 2,
        }

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *a, **kw):
                return MagicMock()

            async def json_or_raise(self, resp):
                return fake_data

        with patch(
            "poindexter.cli.settings.WorkerClient",
            return_value=_FakeClient(),
        ):
            result = runner.invoke(settings_group, ["list"])

        assert result.exit_code == 0, result.output
        # The bare key appears at the start of each row (after the
        # leading whitespace).
        for line in result.output.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(("daily_post_limit", "auto_publish_threshold")):
                # Confirm the format: bare key, then [category], then = value.
                # No "category/key" form anywhere.
                assert "pipeline/daily_post_limit" not in line
                assert "quality/auto_publish_threshold" not in line
                assert "[" in stripped and "]" in stripped
                break
        else:
            pytest.fail(
                f"No row started with the bare key — output was:\n{result.output}"
            )
