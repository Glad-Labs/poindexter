"""Unit tests for ``services.experiment_admin`` — the transport-agnostic
operator surface behind ``poindexter experiments``.

Extracted from the CLI per the transport-adapter contract (epic #1340,
guard #1344): the service owns the SQL + validation and returns plain
data, raising :class:`ExperimentAdminError` (or the
:class:`ActiveExperimentConflict` subclass) for the operator-facing
rejection cases. The CLI now delegates here and translates these
exceptions to ``click.ClickException`` at the transport edge — so this
file pins the *service* contract, while ``test_experiments_cli.py`` keeps
pinning the operator-facing CLI behaviour end-to-end.

Fake-pool pattern mirrors ``tests/unit/cli/test_experiments_cli.py``: a
``MagicMock`` conn with ``AsyncMock`` fetch/fetchrow/fetchval/execute,
wrapped in a pool whose ``acquire()`` is an async context manager.
``asyncpg`` is patched into ``sys.modules`` so the service's lazy
``import asyncpg`` (for ``UniqueViolationError``) resolves to a fake whose
exception class the tests raise to exercise the duplicate-row branches.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services import experiment_admin


def _build_pool(conn: MagicMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def fake_pool():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    pool = _build_pool(conn)

    class _FakeUniqueViolationError(Exception):
        pass

    asyncpg = MagicMock()
    asyncpg.UniqueViolationError = _FakeUniqueViolationError

    with patch.dict("sys.modules", {"asyncpg": asyncpg}):
        yield {
            "conn": conn,
            "pool": pool,
            "UniqueViolationError": _FakeUniqueViolationError,
        }


def _niche(slug: str = "glad-labs"):
    """Truthy stand-in for ``services.niche_service.Niche`` (the service only
    checks truthiness + uses the ``niche_slug`` argument, not the row)."""
    return SimpleNamespace(id=uuid4(), slug=slug, name=slug.title(), active=True)


# ---------------------------------------------------------------------------
# list_experiments
# ---------------------------------------------------------------------------


class TestListExperiments:
    async def test_returns_rows_as_dicts(self, fake_pool):
        rows = [{"key": "k", "niche_slug": "n", "status": "active"}]
        fake_pool["conn"].fetch = AsyncMock(return_value=rows)
        out = await experiment_admin.list_experiments(fake_pool["pool"])
        assert out == [{"key": "k", "niche_slug": "n", "status": "active"}]

    async def test_no_filters_uses_true_predicate(self, fake_pool):
        await experiment_admin.list_experiments(fake_pool["pool"])
        sql = fake_pool["conn"].fetch.await_args.args[0]
        assert "TRUE" in sql
        # Only the SQL string is passed — no positional filter args.
        assert len(fake_pool["conn"].fetch.await_args.args) == 1

    async def test_status_filter_applied(self, fake_pool):
        await experiment_admin.list_experiments(fake_pool["pool"], status="active")
        args = fake_pool["conn"].fetch.await_args.args
        assert "e.status" in args[0]
        assert "active" in args

    async def test_niche_filter_applied(self, fake_pool):
        await experiment_admin.list_experiments(fake_pool["pool"], niche="glad-labs")
        args = fake_pool["conn"].fetch.await_args.args
        assert "e.niche_slug" in args[0]
        assert "glad-labs" in args


# ---------------------------------------------------------------------------
# create_experiment
# ---------------------------------------------------------------------------


class TestCreateExperiment:
    async def test_returns_new_id(self, fake_pool):
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=_niche())
        new_id = str(uuid4())
        fake_pool["conn"].fetchrow = AsyncMock(return_value={"id": new_id})
        with patch("services.niche_service.NicheService", ns_cls):
            got = await experiment_admin.create_experiment(
                fake_pool["pool"],
                key="glad-labs/test",
                niche_slug="glad-labs",
                description="d",
                objective="views_7d",
            )
        assert got == new_id

    async def test_unknown_niche_raises_without_insert(self, fake_pool):
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=None)
        with patch("services.niche_service.NicheService", ns_cls):
            with pytest.raises(experiment_admin.ExperimentAdminError, match="unknown niche"):
                await experiment_admin.create_experiment(
                    fake_pool["pool"],
                    key="k",
                    niche_slug="no-such",
                    description="",
                    objective="views_7d",
                )
        fake_pool["conn"].fetchrow.assert_not_called()

    async def test_duplicate_key_raises_clean(self, fake_pool):
        ns_cls = MagicMock()
        ns_cls.return_value.get_by_slug = AsyncMock(return_value=_niche())
        fake_pool["conn"].fetchrow = AsyncMock(side_effect=fake_pool["UniqueViolationError"]("dup"))
        with patch("services.niche_service.NicheService", ns_cls):
            with pytest.raises(experiment_admin.ExperimentAdminError, match="already exists"):
                await experiment_admin.create_experiment(
                    fake_pool["pool"],
                    key="dup",
                    niche_slug="glad-labs",
                    description="",
                    objective="views_7d",
                )


# ---------------------------------------------------------------------------
# add_variant
# ---------------------------------------------------------------------------


class TestAddVariant:
    async def test_returns_new_id(self, fake_pool):
        exp_id, var_id = str(uuid4()), str(uuid4())
        fake_pool["conn"].fetchrow = AsyncMock(
            side_effect=[{"id": exp_id, "status": "draft"}, {"id": var_id}]
        )
        got = await experiment_admin.add_variant(
            fake_pool["pool"],
            key="k",
            label="A",
            weight=1.0,
            prompt_template_key=None,
            prompt_template_version=None,
            writer_model="gemma-4-31B-it-qat:latest",
            rag_config={},
        )
        assert got == var_id

    async def test_unknown_experiment_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(return_value=None)
        with pytest.raises(experiment_admin.ExperimentAdminError, match="unknown experiment"):
            await experiment_admin.add_variant(
                fake_pool["pool"],
                key="no-such",
                label="A",
                weight=1.0,
                prompt_template_key=None,
                prompt_template_version=None,
                writer_model=None,
                rag_config={},
            )

    async def test_non_draft_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(
            return_value={"id": str(uuid4()), "status": "active"}
        )
        with pytest.raises(experiment_admin.ExperimentAdminError, match="only draft"):
            await experiment_admin.add_variant(
                fake_pool["pool"],
                key="k",
                label="B",
                weight=1.0,
                prompt_template_key=None,
                prompt_template_version=None,
                writer_model=None,
                rag_config={},
            )

    async def test_duplicate_label_raises(self, fake_pool):
        exp_id = str(uuid4())
        calls = {"n": 0}

        async def _fetchrow(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"id": exp_id, "status": "draft"}
            raise fake_pool["UniqueViolationError"]("dup label")

        fake_pool["conn"].fetchrow = AsyncMock(side_effect=_fetchrow)
        with pytest.raises(experiment_admin.ExperimentAdminError, match="already exists"):
            await experiment_admin.add_variant(
                fake_pool["pool"],
                key="k",
                label="A",
                weight=1.0,
                prompt_template_key=None,
                prompt_template_version=None,
                writer_model=None,
                rag_config={},
            )


# ---------------------------------------------------------------------------
# activate_experiment
# ---------------------------------------------------------------------------


class TestActivateExperiment:
    async def test_happy_path_executes_update(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "status": "draft",
                "niche_slug": "glad-labs",
                "variant_count": 2,
            }
        )
        fake_pool["conn"].fetchval = AsyncMock(return_value=None)
        await experiment_admin.activate_experiment(fake_pool["pool"], key="k")
        fake_pool["conn"].execute.assert_awaited()

    async def test_unknown_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(return_value=None)
        with pytest.raises(experiment_admin.ExperimentAdminError, match="unknown experiment"):
            await experiment_admin.activate_experiment(fake_pool["pool"], key="no-such")

    async def test_non_draft_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "status": "active",
                "niche_slug": "glad-labs",
                "variant_count": 2,
            }
        )
        with pytest.raises(experiment_admin.ExperimentAdminError, match="only draft"):
            await experiment_admin.activate_experiment(fake_pool["pool"], key="k")
        fake_pool["conn"].execute.assert_not_called()

    async def test_one_variant_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "status": "draft",
                "niche_slug": "glad-labs",
                "variant_count": 1,
            }
        )
        with pytest.raises(experiment_admin.ExperimentAdminError, match=">=2"):
            await experiment_admin.activate_experiment(fake_pool["pool"], key="k")
        fake_pool["conn"].execute.assert_not_called()

    async def test_active_conflict_raises_typed(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "status": "draft",
                "niche_slug": "glad-labs",
                "variant_count": 2,
            }
        )
        fake_pool["conn"].fetchval = AsyncMock(return_value="glad-labs/other")
        with pytest.raises(experiment_admin.ActiveExperimentConflict) as ei:
            await experiment_admin.activate_experiment(fake_pool["pool"], key="k")
        assert ei.value.conflict_key == "glad-labs/other"
        assert ei.value.niche_slug == "glad-labs"
        # ActiveExperimentConflict is an ExperimentAdminError so a single
        # except in the CLI could catch both, but the CLI wants the typed
        # one for the conclude-suggestion message.
        assert isinstance(ei.value, experiment_admin.ExperimentAdminError)
        fake_pool["conn"].execute.assert_not_called()


# ---------------------------------------------------------------------------
# get_scorecard
# ---------------------------------------------------------------------------


class TestGetScorecard:
    async def test_returns_experiment_and_rows(self, fake_pool):
        exp = {"key": "k", "niche_slug": "n", "status": "active"}
        rows = [{"variant_label": "A"}, {"variant_label": "B"}]
        fake_pool["conn"].fetchrow = AsyncMock(return_value=exp)
        fake_pool["conn"].fetch = AsyncMock(return_value=rows)
        got_exp, got_rows = await experiment_admin.get_scorecard(fake_pool["pool"], key="k")
        assert got_exp == exp
        assert got_rows == rows

    async def test_unknown_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(return_value=None)
        with pytest.raises(experiment_admin.ExperimentAdminError, match="unknown experiment"):
            await experiment_admin.get_scorecard(fake_pool["pool"], key="no-such")


# ---------------------------------------------------------------------------
# conclude_experiment
# ---------------------------------------------------------------------------


class TestConcludeExperiment:
    async def test_returns_experiment_and_variant(self, fake_pool):
        exp_id = str(uuid4())
        calls = {"n": 0}

        async def _fetchrow(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"id": exp_id, "status": "active", "niche_slug": "glad-labs"}
            return {
                "label": "B",
                "writer_model": "gemma-4-31B-it-qat:latest",
                "prompt_template_key": None,
                "prompt_template_version": None,
            }

        fake_pool["conn"].fetchrow = AsyncMock(side_effect=_fetchrow)
        exp, variant = await experiment_admin.conclude_experiment(
            fake_pool["pool"],
            key="k",
            winner="B",
            note="B won",
        )
        assert exp["niche_slug"] == "glad-labs"
        assert variant["label"] == "B"
        fake_pool["conn"].execute.assert_awaited()

    async def test_unknown_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(return_value=None)
        with pytest.raises(experiment_admin.ExperimentAdminError, match="unknown experiment"):
            await experiment_admin.conclude_experiment(
                fake_pool["pool"],
                key="no-such",
                winner="A",
                note="",
            )

    async def test_already_concluded_raises(self, fake_pool):
        fake_pool["conn"].fetchrow = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "status": "concluded",
                "niche_slug": "n",
            }
        )
        with pytest.raises(experiment_admin.ExperimentAdminError, match="already concluded"):
            await experiment_admin.conclude_experiment(
                fake_pool["pool"],
                key="k",
                winner="A",
                note="",
            )
        fake_pool["conn"].execute.assert_not_called()

    async def test_unknown_winner_lists_defined_labels(self, fake_pool):
        exp_id = str(uuid4())
        calls = {"n": 0}

        async def _fetchrow(*_a, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"id": exp_id, "status": "active", "niche_slug": "n"}
            return None  # variant lookup miss

        fake_pool["conn"].fetchrow = AsyncMock(side_effect=_fetchrow)
        fake_pool["conn"].fetch = AsyncMock(return_value=[{"label": "A"}, {"label": "B"}])
        with pytest.raises(experiment_admin.ExperimentAdminError) as ei:
            await experiment_admin.conclude_experiment(
                fake_pool["pool"],
                key="k",
                winner="Z",
                note="",
            )
        msg = str(ei.value)
        assert "does not match" in msg.lower()
        assert "A" in msg and "B" in msg
        fake_pool["conn"].execute.assert_not_called()
