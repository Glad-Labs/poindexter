"""Unit tests for ``services/jobs/prune_orphan_embeddings.py`` (#106).

Patches the per-source handler dict so we don't need to mock every
underlying source table's join. Tests verify the *control flow*: which
sources are checked, which are skipped, how failures isolate, what
the metrics look like.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from services.jobs.prune_orphan_embeddings import (
    PruneOrphanEmbeddingsJob,
    _DEFAULT_BATCH_SIZE,
    _truthy,
)


# ---------------------------------------------------------------------------
# Fake pool — only fetch + fetchval for app_settings reads
# ---------------------------------------------------------------------------


class FakeConnection:
    def __init__(self, store: "FakeStore") -> None:
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT key, value FROM app_settings"):
            (prefix_pat,) = args
            prefix = prefix_pat.rstrip("%")
            return [
                {"key": k, "value": v}
                for k, v in self._store.app_settings.items()
                if k.startswith(prefix)
            ]
        raise AssertionError(
            f"FakeConnection.fetch saw unexpected SQL: {sql_norm[:80]}"
        )

    async def fetchval(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT value FROM app_settings WHERE key = $1"):
            (key,) = args
            return self._store.app_settings.get(key)
        raise AssertionError(
            f"FakeConnection.fetchval saw unexpected SQL: {sql_norm[:80]}"
        )


class FakeStore:
    def __init__(self) -> None:
        self.app_settings: dict[str, str] = {}


class FakePool:
    def __init__(self) -> None:
        self.store = FakeStore()

    def acquire(self):
        return FakeConnection(self.store)


@pytest.fixture
def fake_pool():
    return FakePool()


# ---------------------------------------------------------------------------
# _truthy
# ---------------------------------------------------------------------------


class TestTruthy:
    def test_on_string(self):
        assert _truthy("on") is True

    def test_off_string(self):
        assert _truthy("off") is False

    def test_none(self):
        assert _truthy(None) is False

    def test_alternates(self):
        for v in ("true", "1", "yes", "TRUE", "On"):
            assert _truthy(v) is True

    def test_native_bool(self):
        assert _truthy(True) is True
        assert _truthy(False) is False

    def test_unknown_string(self):
        assert _truthy("random") is False


# ---------------------------------------------------------------------------
# PruneOrphanEmbeddingsJob.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRun:
    async def test_default_does_nothing(self, fake_pool):
        # No app_settings rows → every handler skipped → no deletes.
        result = await PruneOrphanEmbeddingsJob().run(pool=fake_pool, config={})
        assert result.ok is True
        assert result.changes_made == 0
        assert result.metrics["per_table"] == {}
        assert "no orphan-check sources enabled" in result.detail

    async def test_runs_only_enabled_sources(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_orphan_check.posts": "on",
            "embedding_orphan_check.audit": "off",
            "embedding_orphan_check.brain": "off",
        }
        posts_handler = AsyncMock(return_value=3)
        audit_handler = AsyncMock(return_value=99)
        brain_handler = AsyncMock(return_value=99)
        with patch.dict(
            "services.jobs.prune_orphan_embeddings._HANDLERS",
            {
                "posts": posts_handler,
                "audit": audit_handler,
                "brain": brain_handler,
            },
            clear=True,
        ):
            result = await PruneOrphanEmbeddingsJob().run(
                pool=fake_pool, config={},
            )

        assert result.changes_made == 3
        assert result.metrics["per_table"] == {"posts": 3}
        assert sorted(result.metrics["skipped"]) == ["audit", "brain"]
        assert result.metrics["checked"] == ["posts"]
        # Only the enabled handler was called.
        posts_handler.assert_awaited_once()
        audit_handler.assert_not_awaited()
        brain_handler.assert_not_awaited()

    async def test_handler_failure_isolates(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_orphan_check.posts": "on",
            "embedding_orphan_check.audit": "on",
        }
        posts_handler = AsyncMock(side_effect=RuntimeError("DB exploded"))
        audit_handler = AsyncMock(return_value=5)
        with patch.dict(
            "services.jobs.prune_orphan_embeddings._HANDLERS",
            {"posts": posts_handler, "audit": audit_handler},
            clear=True,
        ):
            result = await PruneOrphanEmbeddingsJob().run(
                pool=fake_pool, config={},
            )

        # audit ran successfully despite posts failing.
        assert result.ok is True
        assert result.metrics["per_table"] == {"audit": 5}
        assert "posts" not in result.metrics["per_table"]

    async def test_unknown_enabled_source_surfaces_in_metrics(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_orphan_check.aliens": "on",
        }
        with patch.dict(
            "services.jobs.prune_orphan_embeddings._HANDLERS",
            {},
            clear=True,
        ):
            result = await PruneOrphanEmbeddingsJob().run(
                pool=fake_pool, config={},
            )
        assert "aliens" in result.metrics["unknown_enabled"]

    async def test_batch_size_passed_through(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_orphan_check.posts": "on",
            "embedding_orphan_check_batch_size": "50",
        }
        captured: dict[str, int] = {}

        async def capture(_pool, batch_size):
            captured["batch"] = batch_size
            return 0

        with patch.dict(
            "services.jobs.prune_orphan_embeddings._HANDLERS",
            {"posts": capture},
            clear=True,
        ):
            await PruneOrphanEmbeddingsJob().run(pool=fake_pool, config={})

        assert captured["batch"] == 50

    async def test_invalid_batch_size_falls_back(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_orphan_check.posts": "on",
            "embedding_orphan_check_batch_size": "garbage",
        }
        captured: dict[str, int] = {}

        async def capture(_pool, batch_size):
            captured["batch"] = batch_size
            return 0

        with patch.dict(
            "services.jobs.prune_orphan_embeddings._HANDLERS",
            {"posts": capture},
            clear=True,
        ):
            await PruneOrphanEmbeddingsJob().run(pool=fake_pool, config={})

        assert captured["batch"] == _DEFAULT_BATCH_SIZE

    async def test_metrics_total_matches_sum(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_orphan_check.posts": "on",
            "embedding_orphan_check.audit": "on",
        }
        with patch.dict(
            "services.jobs.prune_orphan_embeddings._HANDLERS",
            {
                "posts": AsyncMock(return_value=4),
                "audit": AsyncMock(return_value=7),
            },
            clear=True,
        ):
            result = await PruneOrphanEmbeddingsJob().run(
                pool=fake_pool, config={},
            )
        assert result.changes_made == 11
        assert result.metrics["total_orphans_pruned"] == 11


# ---------------------------------------------------------------------------
# Job protocol metadata
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_required_attrs(self):
        job = PruneOrphanEmbeddingsJob()
        assert job.name == "prune_orphan_embeddings"
        assert job.idempotent is True
        # Different time slot from the TTL job (paired but non-colliding).
        assert job.schedule == "23 3 * * *"
