"""Click CLI tests for ``poindexter topics sweep`` (Glad-Labs/poindexter#349).

The command is a thin wrapper around
``services.topic_batch_service.TopicBatchService.run_sweep`` — the same
callable the scheduler's ``run_niche_topic_sweep`` job uses. We patch
NicheService + TopicBatchService + asyncpg so the suite exercises the
Click glue (option parsing, output formatting, exit codes) without a
live DB.

For DB-backed exercise of the underlying service see
``tests/integration/test_niche_discovery_e2e.py`` and
``tests/unit/services/test_topic_batch_service.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from click.testing import CliRunner

from poindexter.cli.topics import topics_group


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _async_conn(*, fetchval_result=None) -> Any:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    conn.close = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.create_pool`` so the CLI never reaches a real DB."""
    conn = _async_conn()

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
        yield {"conn": conn, "pool": pool, "asyncpg": asyncpg}


def _niche(slug: str = "glad-labs", name: str = "Glad Labs",
           floor: int = 60) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        slug=slug,
        name=name,
        discovery_cadence_minute_floor=floor,
    )


def _snapshot(candidate_count: int = 3) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        niche_id=uuid4(),
        status="open",
        candidate_count=candidate_count,
        expires_at=None,
    )


def _candidate_view(title: str = "Top pick title") -> Any:
    return SimpleNamespace(
        id="cand-1",
        kind="external",
        title=title,
        summary=None,
        score=10.0,
        decay_factor=1.0,
        effective_score=10.0,
        rank_in_batch=1,
        operator_rank=None,
        operator_edited_topic=None,
        operator_edited_angle=None,
        score_breakdown={},
    )


def _batch_view(candidates=None) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        niche_id=uuid4(),
        status="open",
        picked_candidate_id=None,
        candidates=candidates if candidates is not None else [_candidate_view()],
    )


# ---------------------------------------------------------------------------
# topics sweep — argument parsing
# ---------------------------------------------------------------------------


class TestSweepArgs:
    def test_help(self, runner):
        result = runner.invoke(topics_group, ["sweep", "--help"])
        assert result.exit_code == 0
        assert "discovery sweep" in result.output.lower()
        assert "--niche" in result.output

    def test_missing_niche_flag_exits_nonzero(self, runner, fake_asyncpg):
        result = runner.invoke(topics_group, ["sweep"])
        assert result.exit_code != 0
        assert "--niche" in result.output


# ---------------------------------------------------------------------------
# topics sweep — successful sweep
# ---------------------------------------------------------------------------


class TestSweepSuccess:
    def test_creates_batch_and_prints_summary(self, runner, fake_asyncpg):
        n = _niche(slug="glad-labs", name="Glad Labs")
        snap = _snapshot(candidate_count=4)
        view = _batch_view(candidates=[_candidate_view(title="A sharper hook")])

        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)

        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=snap)
        svc_cls.return_value.show_batch = AsyncMock(return_value=view)

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = runner.invoke(topics_group, ["sweep", "--niche", "glad-labs"])

        assert result.exit_code == 0, result.output
        assert "glad-labs" in result.output
        assert "Glad Labs" in result.output
        assert str(snap.id) in result.output
        assert "4 candidates" in result.output
        assert "A sharper hook" in result.output
        # The CLI should pass the resolved niche id through to run_sweep.
        svc_cls.return_value.run_sweep.assert_awaited_once_with(niche_id=n.id)


# ---------------------------------------------------------------------------
# topics sweep — short-circuits (cadence + open batch)
# ---------------------------------------------------------------------------


class TestSweepShortCircuits:
    def test_open_batch_exists_prints_reason(self, runner, fake_asyncpg):
        n = _niche()
        existing_batch_id = uuid4()
        # When run_sweep returns None, the CLI re-checks for an open batch
        # to disambiguate the two short-circuit paths.
        fake_asyncpg["conn"].fetchval = AsyncMock(return_value=existing_batch_id)

        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=None)

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = runner.invoke(topics_group, ["sweep", "--niche", n.slug])

        assert result.exit_code == 0, result.output
        assert "no new batch" in result.output.lower()
        assert "open batch already exists" in result.output.lower()
        assert str(existing_batch_id) in result.output

    def test_cadence_floor_not_elapsed_prints_reason(self, runner, fake_asyncpg):
        n = _niche(floor=90)
        # No open batch row — cadence-floor short-circuit instead.
        fake_asyncpg["conn"].fetchval = AsyncMock(return_value=None)

        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=None)

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = runner.invoke(topics_group, ["sweep", "--niche", n.slug])

        assert result.exit_code == 0, result.output
        assert "no new batch" in result.output.lower()
        assert "cadence floor" in result.output.lower()
        assert "90" in result.output  # surfaced floor value


# ---------------------------------------------------------------------------
# topics sweep — error paths
# ---------------------------------------------------------------------------


class TestSweepErrors:
    def test_unknown_niche_returns_clean_error_code(self, runner, fake_asyncpg):
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=None)
        svc_cls = MagicMock()

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = runner.invoke(topics_group, ["sweep", "--niche", "no-such-niche"])

        assert result.exit_code == 1
        assert "unknown niche: no-such-niche" in result.output.lower()
        # Service must NOT be called when the slug doesn't resolve.
        svc_cls.return_value.run_sweep.assert_not_called()
