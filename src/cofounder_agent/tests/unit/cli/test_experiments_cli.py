"""Click CLI tests for ``poindexter experiments`` (Phase 1 PR 3).

Pins the behaviour the operator depends on:

- ``list`` renders a clean table + supports ``--json``.
- ``create`` rejects unknown niche + duplicate key.
- ``add-variant`` rejects unknown experiment / non-draft status / duplicate
  label / malformed rag_config JSON.
- ``activate`` rejects unknown / non-draft / one-variant / niche-conflict
  experiments and updates status when valid.
- ``status`` renders the scorecard sorted by approval-rate desc + supports
  ``--json``.
- ``conclude`` rejects unknown / already-concluded / unknown-winner and
  prints next-step guidance based on the winner's override columns.

Mirrors ``tests/unit/cli/test_topics_cli.py``: ``CliRunner`` + ``AsyncMock``
+ ``patch.dict("sys.modules", {"asyncpg": <mock>})`` so the CLI never
reaches a real DB. The CLI does ``import asyncpg`` lazily inside each
command's ``_impl()`` body, so the sys.modules patch is in place by the
time that import resolves.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from click.testing import CliRunner

from poindexter.cli.experiments import experiments_group


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _build_pool(conn: MagicMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.create_pool`` so the CLI never reaches a real DB.

    Each test sets ``conn.fetch`` / ``conn.fetchrow`` / ``conn.fetchval``
    / ``conn.execute`` to its own AsyncMocks before invoking the command.
    The ``UniqueViolationError`` exception class is attached to the
    asyncpg mock so the CLI's ``except asyncpg.UniqueViolationError``
    branches can be exercised by raising that exception from the
    fetchrow mock.
    """
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)

    pool = _build_pool(conn)

    class _FakeUniqueViolationError(Exception):
        pass

    async def _create_pool(_dsn, **_kwargs):
        return pool

    asyncpg = MagicMock()
    asyncpg.create_pool = _create_pool
    asyncpg.UniqueViolationError = _FakeUniqueViolationError

    with patch.dict("sys.modules", {"asyncpg": asyncpg}):
        yield {
            "conn": conn,
            "pool": pool,
            "asyncpg": asyncpg,
            "UniqueViolationError": _FakeUniqueViolationError,
        }


def _niche(slug: str = "glad-labs") -> Any:
    """SimpleNamespace shaped like ``services.niche_service.Niche``."""
    return SimpleNamespace(
        id=uuid4(),
        slug=slug,
        name=slug.replace("-", " ").title(),
        active=True,
        target_audience_tags=["devs"],
        writer_prompt_override=None,
        batch_size=5,
        discovery_cadence_minute_floor=60,
        default_template_slug=None,
    )


# ---------------------------------------------------------------------------
# experiments list
# ---------------------------------------------------------------------------


class TestList:
    def test_empty_prints_friendly_message(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=[])
        result = runner.invoke(experiments_group, ["list"])
        assert result.exit_code == 0, result.output
        assert "no experiments" in result.output.lower()

    def test_renders_table(self, runner, fake_asyncpg):
        rows = [
            {
                "key": "glad-labs/writer-model-test-2026-05",
                "niche_slug": "glad-labs",
                "status": "active",
                "objective_function": "views_7d",
                "created_at": "2026-05-28T00:00:00+00:00",
                "activated_at": "2026-05-28T01:00:00+00:00",
                "concluded_at": None,
                "winner_variant_label": None,
                "variant_count": 2,
                "outcome_count": 14,
            },
        ]
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=rows)
        result = runner.invoke(experiments_group, ["list"])
        assert result.exit_code == 0, result.output
        assert "glad-labs/writer-model-test-2026-05" in result.output
        assert "active" in result.output
        assert "14" in result.output

    def test_json_output(self, runner, fake_asyncpg):
        rows = [
            {
                "key": "k", "niche_slug": "n", "status": "draft",
                "objective_function": "views_7d",
                "created_at": "2026-05-28T00:00:00+00:00",
                "activated_at": None, "concluded_at": None,
                "winner_variant_label": None,
                "variant_count": 0, "outcome_count": 0,
            },
        ]
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=rows)
        result = runner.invoke(experiments_group, ["list", "--json"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed[0]["key"] == "k"

    def test_status_filter_applied(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=[])
        result = runner.invoke(
            experiments_group, ["list", "--status", "active"],
        )
        assert result.exit_code == 0, result.output
        sql, args = fake_asyncpg["conn"].fetch.await_args.args[0:2], \
                    fake_asyncpg["conn"].fetch.await_args.args[1:]
        # SQL contains "e.status = $1" and args includes 'active'
        assert "e.status" in fake_asyncpg["conn"].fetch.await_args.args[0]
        assert "active" in fake_asyncpg["conn"].fetch.await_args.args


# ---------------------------------------------------------------------------
# experiments create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_happy_path(self, runner, fake_asyncpg):
        n = _niche()
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        new_id = str(uuid4())
        fake_asyncpg["conn"].fetchrow = AsyncMock(
            return_value={"id": new_id},
        )

        with patch("services.niche_service.NicheService", ns_cls):
            result = runner.invoke(
                experiments_group,
                [
                    "create", "glad-labs/test-2026-05",
                    "--niche", n.slug,
                    "--description", "testing prompt v3 vs v4",
                ],
            )
        assert result.exit_code == 0, result.output
        assert new_id in result.output
        assert "draft" in result.output

    def test_unknown_niche_rejected(self, runner, fake_asyncpg):
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=None)
        with patch("services.niche_service.NicheService", ns_cls):
            result = runner.invoke(
                experiments_group,
                ["create", "k", "--niche", "no-such-niche"],
            )
        assert result.exit_code != 0
        assert "unknown niche" in result.output.lower()
        # INSERT should never have been attempted.
        fake_asyncpg["conn"].fetchrow.assert_not_called()

    def test_duplicate_key_surfaces_clean_message(self, runner, fake_asyncpg):
        n = _niche()
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        fake_asyncpg["conn"].fetchrow = AsyncMock(
            side_effect=fake_asyncpg["UniqueViolationError"]("dup"),
        )
        with patch("services.niche_service.NicheService", ns_cls):
            result = runner.invoke(
                experiments_group,
                ["create", "dup-key", "--niche", n.slug],
            )
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()


# ---------------------------------------------------------------------------
# experiments add-variant
# ---------------------------------------------------------------------------


class TestAddVariant:
    def test_happy_path(self, runner, fake_asyncpg):
        exp_id = str(uuid4())
        new_var_id = str(uuid4())
        # Two fetchrow calls: (1) experiment lookup, (2) INSERT RETURNING id.
        fake_asyncpg["conn"].fetchrow = AsyncMock(side_effect=[
            {"id": exp_id, "status": "draft"},
            {"id": new_var_id},
        ])
        result = runner.invoke(
            experiments_group,
            [
                "add-variant", "k",
                "--label", "A",
                "--writer-model", "gemma4:31b",
            ],
        )
        assert result.exit_code == 0, result.output
        assert new_var_id in result.output
        assert "A" in result.output
        assert "gemma4:31b" in result.output

    def test_unknown_experiment(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value=None)
        result = runner.invoke(
            experiments_group,
            ["add-variant", "no-such", "--label", "A"],
        )
        assert result.exit_code != 0
        assert "unknown experiment" in result.output.lower()

    def test_non_draft_status_rejected(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(
            return_value={"id": str(uuid4()), "status": "active"},
        )
        result = runner.invoke(
            experiments_group,
            ["add-variant", "k", "--label", "B"],
        )
        assert result.exit_code != 0
        assert "only draft" in result.output.lower()

    def test_duplicate_label_rejected(self, runner, fake_asyncpg):
        exp_id = str(uuid4())
        fake_asyncpg["conn"].fetchrow = AsyncMock(side_effect=[
            {"id": exp_id, "status": "draft"},
            fake_asyncpg["UniqueViolationError"]("dup label"),
        ])
        # Need to make the second call raise — but side_effect with a
        # mix of return and raise needs the exception classes (not instances)
        # to auto-raise. Rebuild with a callable to control behaviour:
        call_count = {"n": 0}

        async def _fetchrow(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"id": exp_id, "status": "draft"}
            raise fake_asyncpg["UniqueViolationError"]("dup label")

        fake_asyncpg["conn"].fetchrow = AsyncMock(side_effect=_fetchrow)
        result = runner.invoke(
            experiments_group,
            ["add-variant", "k", "--label", "A"],
        )
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()

    def test_label_required(self, runner, fake_asyncpg):
        result = runner.invoke(
            experiments_group, ["add-variant", "k"],
        )
        assert result.exit_code != 0
        assert "--label" in result.output

    def test_malformed_rag_config_rejected(self, runner, fake_asyncpg):
        result = runner.invoke(
            experiments_group,
            [
                "add-variant", "k", "--label", "A",
                "--rag-config", "{not valid json",
            ],
        )
        assert result.exit_code != 0
        assert "invalid --rag-config json" in result.output.lower()
        # Failure happened before any DB call.
        fake_asyncpg["conn"].fetchrow.assert_not_called()

    def test_rag_config_must_be_object(self, runner, fake_asyncpg):
        result = runner.invoke(
            experiments_group,
            [
                "add-variant", "k", "--label", "A",
                "--rag-config", "[1, 2, 3]",
            ],
        )
        assert result.exit_code != 0
        assert "must be a json object" in result.output.lower()


# ---------------------------------------------------------------------------
# experiments activate
# ---------------------------------------------------------------------------


class TestActivate:
    def test_happy_path(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={
            "id": str(uuid4()),
            "status": "draft",
            "niche_slug": "glad-labs",
            "variant_count": 2,
        })
        fake_asyncpg["conn"].fetchval = AsyncMock(return_value=None)
        fake_asyncpg["conn"].execute = AsyncMock(return_value=None)
        result = runner.invoke(experiments_group, ["activate", "k"])
        assert result.exit_code == 0, result.output
        assert "activated" in result.output.lower()
        fake_asyncpg["conn"].execute.assert_awaited()

    def test_unknown_experiment(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value=None)
        result = runner.invoke(experiments_group, ["activate", "no-such"])
        assert result.exit_code != 0
        assert "unknown experiment" in result.output.lower()

    def test_non_draft_rejected(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={
            "id": str(uuid4()),
            "status": "active",
            "niche_slug": "glad-labs",
            "variant_count": 2,
        })
        result = runner.invoke(experiments_group, ["activate", "k"])
        assert result.exit_code != 0
        assert "only draft" in result.output.lower()
        fake_asyncpg["conn"].execute.assert_not_called()

    def test_one_variant_rejected(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={
            "id": str(uuid4()),
            "status": "draft",
            "niche_slug": "glad-labs",
            "variant_count": 1,
        })
        result = runner.invoke(experiments_group, ["activate", "k"])
        assert result.exit_code != 0
        assert "need >=2" in result.output.lower() or "need 2" in result.output.lower()
        fake_asyncpg["conn"].execute.assert_not_called()

    def test_active_conflict_on_niche(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={
            "id": str(uuid4()),
            "status": "draft",
            "niche_slug": "glad-labs",
            "variant_count": 2,
        })
        fake_asyncpg["conn"].fetchval = AsyncMock(
            return_value="glad-labs/other-experiment",
        )
        result = runner.invoke(experiments_group, ["activate", "k"])
        assert result.exit_code != 0
        assert "already has an active experiment" in result.output.lower()
        assert "glad-labs/other-experiment" in result.output
        assert "conclude" in result.output.lower()
        fake_asyncpg["conn"].execute.assert_not_called()


# ---------------------------------------------------------------------------
# experiments status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_unknown_experiment(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value=None)
        result = runner.invoke(experiments_group, ["status", "no-such"])
        assert result.exit_code != 0
        assert "unknown experiment" in result.output.lower()

    def test_renders_scorecard_sorted_by_approval_desc(
        self, runner, fake_asyncpg,
    ):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={
            "key": "glad-labs/test",
            "niche_slug": "glad-labs",
            "status": "active",
            "objective_function": "views_7d",
            "description": "B vs A on writer model",
            "created_at": "2026-05-28T00:00:00+00:00",
            "activated_at": "2026-05-28T01:00:00+00:00",
            "concluded_at": None,
            "winner_variant_label": None,
            "conclusion_note": None,
        })
        # Caller orders by approval_rate_pct DESC, so we pass them
        # already-sorted; the test verifies the render order matches the
        # input order (i.e. CLI doesn't re-sort).
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=[
            {
                "variant_label": "B", "variant_active": True,
                "paused_at": None, "paused_reason": None,
                "posts_attempted": 15, "posts_approved": 12,
                "approval_rate_pct": 80.0,
                "avg_edit_distance_pct": 0.04,
                "avg_views_24h": 50.0, "avg_views_7d": 200.0,
                "avg_cost_per_post": 0.01, "total_cost": 0.15,
            },
            {
                "variant_label": "A", "variant_active": True,
                "paused_at": None, "paused_reason": None,
                "posts_attempted": 14, "posts_approved": 7,
                "approval_rate_pct": 50.0,
                "avg_edit_distance_pct": 0.10,
                "avg_views_24h": 30.0, "avg_views_7d": 100.0,
                "avg_cost_per_post": 0.02, "total_cost": 0.28,
            },
        ])
        result = runner.invoke(experiments_group, ["status", "glad-labs/test"])
        assert result.exit_code == 0, result.output
        # B (winner) renders before A.
        b_idx = result.output.find("B ")
        a_idx = result.output.find("A ")
        assert b_idx != -1 and a_idx != -1
        assert b_idx < a_idx, (
            "expected B (80% approval) to render before A (50% approval); "
            f"got B@{b_idx} A@{a_idx}\n{result.output}"
        )
        assert "views_7d" in result.output  # objective surfaced
        assert "glad-labs" in result.output

    def test_json_output(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={
            "key": "k", "niche_slug": "n", "status": "draft",
            "objective_function": "views_7d", "description": "",
            "created_at": "2026-05-28T00:00:00+00:00",
            "activated_at": None, "concluded_at": None,
            "winner_variant_label": None, "conclusion_note": None,
        })
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=[])
        result = runner.invoke(
            experiments_group, ["status", "k", "--json"],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["experiment"]["key"] == "k"
        assert parsed["variants"] == []


# ---------------------------------------------------------------------------
# experiments conclude
# ---------------------------------------------------------------------------


class TestConclude:
    def test_happy_path_with_model_winner(self, runner, fake_asyncpg):
        exp_id = str(uuid4())
        call_count = {"n": 0}

        async def _fetchrow(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "id": exp_id, "status": "active",
                    "niche_slug": "glad-labs",
                }
            # Variant lookup
            return {
                "label": "B",
                "writer_model": "gemma4:31b",
                "prompt_template_key": None,
                "prompt_template_version": None,
            }

        fake_asyncpg["conn"].fetchrow = AsyncMock(side_effect=_fetchrow)
        fake_asyncpg["conn"].execute = AsyncMock(return_value=None)
        result = runner.invoke(
            experiments_group,
            [
                "conclude", "k",
                "--winner", "B",
                "--note", "B won 73% approval",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "concluded" in result.output.lower()
        assert "B" in result.output
        assert "B won 73% approval" in result.output
        # Next-step guidance for model winner.
        assert "gemma4:31b" in result.output
        assert "cost_tier" in result.output.lower()
        fake_asyncpg["conn"].execute.assert_awaited()

    def test_happy_path_with_prompt_winner(self, runner, fake_asyncpg):
        exp_id = str(uuid4())
        call_count = {"n": 0}

        async def _fetchrow(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"id": exp_id, "status": "active", "niche_slug": "n"}
            return {
                "label": "v4",
                "writer_model": None,
                "prompt_template_key": "qa.writer.long_form",
                "prompt_template_version": 4,
            }

        fake_asyncpg["conn"].fetchrow = AsyncMock(side_effect=_fetchrow)
        result = runner.invoke(
            experiments_group,
            ["conclude", "k", "--winner", "v4"],
        )
        assert result.exit_code == 0, result.output
        assert "qa.writer.long_form" in result.output
        assert "v4" in result.output
        assert "langfuse" in result.output.lower()

    def test_unknown_experiment(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value=None)
        result = runner.invoke(
            experiments_group,
            ["conclude", "no-such", "--winner", "A"],
        )
        assert result.exit_code != 0
        assert "unknown experiment" in result.output.lower()

    def test_already_concluded(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={
            "id": str(uuid4()), "status": "concluded",
            "niche_slug": "glad-labs",
        })
        result = runner.invoke(
            experiments_group,
            ["conclude", "k", "--winner", "A"],
        )
        assert result.exit_code != 0
        assert "already concluded" in result.output.lower()
        fake_asyncpg["conn"].execute.assert_not_called()

    def test_unknown_winner_label(self, runner, fake_asyncpg):
        exp_id = str(uuid4())
        call_count = {"n": 0}

        async def _fetchrow(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"id": exp_id, "status": "active", "niche_slug": "n"}
            return None  # variant lookup miss

        fake_asyncpg["conn"].fetchrow = AsyncMock(side_effect=_fetchrow)
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=[
            {"label": "A"}, {"label": "B"},
        ])
        result = runner.invoke(
            experiments_group,
            ["conclude", "k", "--winner", "Z"],
        )
        assert result.exit_code != 0
        assert "does not match" in result.output.lower()
        assert "A" in result.output and "B" in result.output
        fake_asyncpg["conn"].execute.assert_not_called()

    def test_winner_required(self, runner, fake_asyncpg):
        result = runner.invoke(experiments_group, ["conclude", "k"])
        assert result.exit_code != 0
        assert "--winner" in result.output
