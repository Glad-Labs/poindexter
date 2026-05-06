"""Click CLI tests for ``poindexter topics`` (Glad-Labs/poindexter#403).

The new batch-oriented commands (``sweep`` / ``show-batch`` / ``rank-batch``
/ ``edit-winner`` / ``resolve-batch`` / ``reject-batch`` / ``niche``) build
their own ``asyncpg`` pool inline via ``asyncpg.create_pool`` and call
instance methods on ``services.topic_batch_service.TopicBatchService`` /
``services.niche_service.NicheService``. We patch those so the suite
exercises the Click glue (option parsing, output formatting, exit codes,
JSON output for ``niche show``) without a live DB.

Dedicated sweep coverage lives in
``tests/unit/poindexter/cli/test_topics_sweep.py`` — this file rounds out
coverage for the remaining commands. A happy-path sweep smoke test is
included so the file is self-contained per the acceptance in
poindexter#403.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from click.testing import CliRunner

from poindexter.cli.topics import topics_group


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _async_conn(*, fetchval_result=None) -> Any:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.create_pool`` so the CLI never reaches a real DB.

    The CLI does ``import asyncpg`` lazily inside each command's ``_impl()``
    body, so patching ``sys.modules['asyncpg']`` is in place by the time
    that import resolves.
    """
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


# ---------------------------------------------------------------------------
# Test-data factories
# ---------------------------------------------------------------------------


def _niche(slug: str = "glad-labs", name: str = "Glad Labs",
           floor: int = 60) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        slug=slug,
        name=name,
        active=True,
        writer_rag_mode="hybrid",
        batch_size=10,
        discovery_cadence_minute_floor=floor,
        target_audience_tags=["devs"],
    )


def _candidate(rank: int = 1, op_rank: int | None = None,
               title: str = "Top pick") -> Any:
    return SimpleNamespace(
        id=uuid4(),
        kind="external",
        title=title,
        summary=None,
        score=10.0,
        decay_factor=1.0,
        effective_score=10.0,
        rank_in_batch=rank,
        operator_rank=op_rank,
        operator_edited_topic=None,
        operator_edited_angle=None,
        score_breakdown={},
    )


def _batch_view(candidates=None, status: str = "open") -> Any:
    return SimpleNamespace(
        id=uuid4(),
        niche_id=uuid4(),
        status=status,
        picked_candidate_id=None,
        candidates=candidates if candidates is not None else [_candidate()],
    )


def _snapshot(candidate_count: int = 3) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        niche_id=uuid4(),
        status="open",
        candidate_count=candidate_count,
        expires_at=None,
    )


# ---------------------------------------------------------------------------
# topics sweep — happy-path smoke (full coverage in test_topics_sweep.py)
# ---------------------------------------------------------------------------


class TestSweep:
    def test_creates_batch_and_prints_summary(self, runner, fake_asyncpg):
        n = _niche()
        snap = _snapshot(candidate_count=4)
        view = _batch_view(candidates=[_candidate(title="A sharper hook")])

        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=snap)
        svc_cls.return_value.show_batch = AsyncMock(return_value=view)

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = runner.invoke(
                topics_group, ["sweep", "--niche", n.slug],
            )

        assert result.exit_code == 0, result.output
        assert "A sharper hook" in result.output
        assert str(snap.id) in result.output


# ---------------------------------------------------------------------------
# topics show-batch
# ---------------------------------------------------------------------------


class TestShowBatch:
    def test_no_open_batch_prints_message(self, runner, fake_asyncpg):
        n = _niche()
        fake_asyncpg["conn"].fetchval = AsyncMock(return_value=None)

        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        svc_cls = MagicMock()

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = runner.invoke(
                topics_group, ["show-batch", "--niche", n.slug],
            )

        assert result.exit_code == 0, result.output
        assert "no open batch" in result.output.lower()
        svc_cls.return_value.show_batch.assert_not_called()

    def test_renders_candidates_with_markers(self, runner, fake_asyncpg):
        n = _niche()
        bid = uuid4()
        fake_asyncpg["conn"].fetchval = AsyncMock(return_value=bid)

        view = _batch_view(candidates=[
            _candidate(rank=1, op_rank=None, title="System pick"),
            _candidate(rank=2, op_rank=1, title="Operator pick"),
        ])
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        svc_cls = MagicMock()
        svc_cls.return_value.show_batch = AsyncMock(return_value=view)

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = runner.invoke(
                topics_group, ["show-batch", "--niche", n.slug],
            )

        assert result.exit_code == 0, result.output
        assert "System pick" in result.output
        assert "Operator pick" in result.output
        # Operator-ranked rows print "#1"; un-edited rows print "sys#N".
        assert "sys#1" in result.output
        assert "#1" in result.output

    def test_unknown_niche_returns_clean_error(self, runner, fake_asyncpg):
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=None)

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch(
                "services.topic_batch_service.TopicBatchService", MagicMock(),
            ),
        ):
            result = runner.invoke(
                topics_group, ["show-batch", "--niche", "no-such"],
            )

        assert result.exit_code == 1
        assert "unknown niche" in result.output.lower()


# ---------------------------------------------------------------------------
# topics rank-batch
# ---------------------------------------------------------------------------


class TestRankBatch:
    def test_resolves_markers_and_calls_service(self, runner, fake_asyncpg):
        c1 = _candidate(rank=1, op_rank=None, title="Sys top")
        c2 = _candidate(rank=2, op_rank=2, title="Op two")
        bid = uuid4()
        view = _batch_view(candidates=[c1, c2])
        view.id = bid

        svc_cls = MagicMock()
        svc_cls.return_value.show_batch = AsyncMock(return_value=view)
        svc_cls.return_value.rank_batch = AsyncMock(return_value=None)

        with patch(
            "services.topic_batch_service.TopicBatchService", svc_cls,
        ):
            result = runner.invoke(
                topics_group,
                ["rank-batch", str(bid), "--order", "sys#1,#2"],
            )

        assert result.exit_code == 0, result.output
        assert "Ranked 2 candidates" in result.output
        kwargs = svc_cls.return_value.rank_batch.await_args.kwargs
        assert kwargs["batch_id"] == bid
        assert kwargs["ordered_candidate_ids"] == [c1.id, c2.id]

    def test_unknown_marker_fails_clean(self, runner, fake_asyncpg):
        view = _batch_view(candidates=[_candidate(rank=1)])
        bid = uuid4()
        view.id = bid

        svc_cls = MagicMock()
        svc_cls.return_value.show_batch = AsyncMock(return_value=view)

        with patch(
            "services.topic_batch_service.TopicBatchService", svc_cls,
        ):
            result = runner.invoke(
                topics_group,
                ["rank-batch", str(bid), "--order", "sys#99"],
            )

        assert result.exit_code != 0
        assert "no candidate matches sys#99" in result.output.lower()
        svc_cls.return_value.rank_batch.assert_not_called()


# ---------------------------------------------------------------------------
# topics edit-winner
# ---------------------------------------------------------------------------


class TestEditWinner:
    def test_topic_only(self, runner, fake_asyncpg):
        bid = uuid4()
        svc_cls = MagicMock()
        svc_cls.return_value.edit_winner = AsyncMock(return_value=None)

        with patch(
            "services.topic_batch_service.TopicBatchService", svc_cls,
        ):
            result = runner.invoke(
                topics_group,
                ["edit-winner", str(bid), "--topic", "Better hook"],
            )

        assert result.exit_code == 0, result.output
        assert "edited winner" in result.output.lower()
        kwargs = svc_cls.return_value.edit_winner.await_args.kwargs
        assert kwargs["batch_id"] == bid
        assert kwargs["topic"] == "Better hook"
        assert kwargs["angle"] is None

    def test_no_flags_fails_before_service_call(self, runner, fake_asyncpg):
        bid = uuid4()
        svc_cls = MagicMock()

        with patch(
            "services.topic_batch_service.TopicBatchService", svc_cls,
        ):
            result = runner.invoke(
                topics_group, ["edit-winner", str(bid)],
            )

        assert result.exit_code != 0
        assert "--topic" in result.output
        svc_cls.return_value.edit_winner.assert_not_called()


# ---------------------------------------------------------------------------
# topics resolve-batch
# ---------------------------------------------------------------------------


class TestResolveBatch:
    def test_calls_service_and_prints_id(self, runner, fake_asyncpg):
        bid = uuid4()
        svc_cls = MagicMock()
        svc_cls.return_value.resolve_batch = AsyncMock(return_value=None)

        with patch(
            "services.topic_batch_service.TopicBatchService", svc_cls,
        ):
            result = runner.invoke(
                topics_group, ["resolve-batch", str(bid)],
            )

        assert result.exit_code == 0, result.output
        assert str(bid) in result.output
        assert "resolved" in result.output.lower()
        svc_cls.return_value.resolve_batch.assert_awaited_once_with(
            batch_id=bid,
        )


# ---------------------------------------------------------------------------
# topics reject-batch
# ---------------------------------------------------------------------------


class TestRejectBatch:
    def test_with_reason(self, runner, fake_asyncpg):
        bid = uuid4()
        svc_cls = MagicMock()
        svc_cls.return_value.reject_batch = AsyncMock(return_value=None)

        with patch(
            "services.topic_batch_service.TopicBatchService", svc_cls,
        ):
            result = runner.invoke(
                topics_group,
                ["reject-batch", str(bid), "--reason", "off-topic"],
            )

        assert result.exit_code == 0, result.output
        assert "rejected" in result.output.lower()
        assert str(bid) in result.output
        kwargs = svc_cls.return_value.reject_batch.await_args.kwargs
        assert kwargs["batch_id"] == bid
        assert kwargs["reason"] == "off-topic"

    def test_default_reason_is_empty_string(self, runner, fake_asyncpg):
        bid = uuid4()
        svc_cls = MagicMock()
        svc_cls.return_value.reject_batch = AsyncMock(return_value=None)

        with patch(
            "services.topic_batch_service.TopicBatchService", svc_cls,
        ):
            result = runner.invoke(
                topics_group, ["reject-batch", str(bid)],
            )

        assert result.exit_code == 0, result.output
        kwargs = svc_cls.return_value.reject_batch.await_args.kwargs
        assert kwargs["reason"] == ""


# ---------------------------------------------------------------------------
# topics niche list / show
# ---------------------------------------------------------------------------


class TestNicheList:
    def test_lists_active_niches(self, runner, fake_asyncpg):
        n1 = _niche(slug="ai-ml", name="AI / ML")
        n2 = _niche(slug="hardware", name="PC Hardware")

        ns_cls = MagicMock()
        ns_cls.return_value.list_active = AsyncMock(return_value=[n1, n2])

        with patch("services.niche_service.NicheService", ns_cls):
            result = runner.invoke(topics_group, ["niche", "list"])

        assert result.exit_code == 0, result.output
        assert "ai-ml" in result.output
        assert "hardware" in result.output


class TestNicheShow:
    def test_outputs_valid_json_with_goals_and_sources(
        self, runner, fake_asyncpg,
    ):
        n = _niche(slug="ai-ml", name="AI / ML")
        goals = [SimpleNamespace(goal_type="traffic", weight_pct=70)]
        sources = [
            SimpleNamespace(source_name="rss", enabled=True, weight_pct=50),
        ]

        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=n)
        ns_cls.return_value.get_goals = AsyncMock(return_value=goals)
        ns_cls.return_value.get_sources = AsyncMock(return_value=sources)

        with patch("services.niche_service.NicheService", ns_cls):
            result = runner.invoke(
                topics_group, ["niche", "show", "ai-ml"],
            )

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["slug"] == "ai-ml"
        assert parsed["name"] == "AI / ML"
        assert parsed["goals"] == [{"type": "traffic", "weight": 70}]
        assert parsed["sources"][0]["name"] == "rss"
        assert parsed["sources"][0]["enabled"] is True

    def test_unknown_slug_errors(self, runner, fake_asyncpg):
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=None)

        with patch("services.niche_service.NicheService", ns_cls):
            result = runner.invoke(
                topics_group, ["niche", "show", "no-such"],
            )

        assert result.exit_code != 0
        assert "unknown niche" in result.output.lower()
