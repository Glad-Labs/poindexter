"""Unit tests — one-off best-effort-failure visibility (gap-site burn-down
batch 6, multi-site files).

Nine single-shot (non-loop) sites across six files swallowed a genuine
failure at ``logger.debug`` and returned a sentinel — invisible below the
alerting bar. Each now fires exactly one ``emit_finding(info)`` alongside
the existing debug log: visible, non-paging, non-blocking. Unlike batch 5,
these are not loop-shaped, so there is no aggregate-count pattern — one
call site, one finding call.

A tenth site (``template_runner.py`` — ``has_resumable_checkpoint``) is
intentionally left ``# silent-ok``, not tested here: per its own docstring
it only gates an optional resume-vs-restart convenience, never a
correctness or data-loss concern.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import live_activity
from services.pipeline_experiment_hook import (
    assign_pipeline_variant,
    record_pipeline_outcome,
)
from services.prompt_manager import UnifiedPromptManager
from services.research_service import ResearchService
from services.site_config import SiteConfig
from services.template_runner import TemplateRunner

pytestmark = pytest.mark.unit

_LF_PATCH_TARGET = "services.langfuse_experiments.LangfuseExperimentService"


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


def _stub_db_with_failing_pool() -> SimpleNamespace:
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("pool down"))
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return SimpleNamespace(pool=pool)


class _StubSiteConfig:
    def get(self, key: str, default: Any = None) -> Any:
        return default


def _make_mocked_langfuse_service(
    *, dataset_side_effect: Exception | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.assign = AsyncMock(return_value="variant-a")
    svc._get_client = AsyncMock(return_value=MagicMock(
        get_dataset=MagicMock(side_effect=dataset_side_effect),
    ))
    svc.record_outcome = AsyncMock(return_value=True)
    return svc


# ---------------------------------------------------------------------------
# pipeline_experiment_hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_assign_emits_finding_when_setting_lookup_raises(monkeypatch):
    calls = _capture(monkeypatch)
    db = _stub_db_with_failing_pool()
    with patch(_LF_PATCH_TARGET) as svc_ctor:
        result = await assign_pipeline_variant(
            task_id="task-db-blip",
            database_service=db,
            site_config=_StubSiteConfig(),
            models_by_phase={},
        )
    assert result == {"experiment_key": None, "variant_key": None}
    svc_ctor.assert_not_called()
    assert len(calls) == 1
    assert calls[0]["kind"] == "experiment_active_key_read_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_assign_emits_finding_when_variant_dataset_load_fails(monkeypatch):
    calls = _capture(monkeypatch)
    db = SimpleNamespace(pool=MagicMock())
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="exp-1")
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    db.pool.acquire = MagicMock(return_value=acquire_ctx)

    mocked_svc = _make_mocked_langfuse_service(
        dataset_side_effect=RuntimeError("langfuse dataset 500"),
    )
    models: dict[str, str] = {}
    with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
        result = await assign_pipeline_variant(
            task_id="task-cfg-blip",
            database_service=db,
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
    # Non-blocking — the task is still assigned to the variant, just
    # without its config applied.
    assert result == {"experiment_key": "exp-1", "variant_key": "variant-a"}
    assert len(calls) == 1
    assert calls[0]["kind"] == "experiment_variant_config_load_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_assign_emits_finding_when_service_import_fails(monkeypatch):
    calls = _capture(monkeypatch)
    db = SimpleNamespace(pool=MagicMock())
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="exp-1")
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    db.pool.acquire = MagicMock(return_value=acquire_ctx)

    monkeypatch.setitem(sys.modules, "services.langfuse_experiments", None)
    result = await assign_pipeline_variant(
        task_id="task-import-blip",
        database_service=db,
        site_config=_StubSiteConfig(),
        models_by_phase={},
    )
    assert result == {"experiment_key": None, "variant_key": None}
    assert len(calls) == 1
    assert calls[0]["kind"] == "experiment_service_import_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_record_outcome_emits_finding_when_service_import_fails(monkeypatch):
    calls = _capture(monkeypatch)
    db = SimpleNamespace(pool=MagicMock())
    monkeypatch.setitem(sys.modules, "services.langfuse_experiments", None)
    await record_pipeline_outcome(
        assignment={"experiment_key": "exp-1", "variant_key": "variant-a"},
        task_id="task-import-blip",
        database_service=db,
        site_config=_StubSiteConfig(),
        metrics={"quality_score": 80},
    )
    assert len(calls) == 1
    assert calls[0]["kind"] == "experiment_service_import_failed"
    # Same dedup_key as the assign-side twin — shared root cause, one cooldown.
    assert calls[0]["dedup_key"] == "experiment_service_import_failed"


# ---------------------------------------------------------------------------
# live_activity
# ---------------------------------------------------------------------------


def _failing_pool(method: str) -> MagicMock:
    conn = MagicMock()
    setattr(conn, method, AsyncMock(side_effect=RuntimeError("db down")))
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


@pytest.mark.asyncio(loop_scope="session")
async def test_begin_emits_finding_on_insert_failure(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _failing_pool("fetchval")
    result = await live_activity.begin(
        pool, kind="content", ref_id="post-1", title="Test post",
    )
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "live_activity_begin_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_reap_stale_emits_finding_on_update_failure(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _failing_pool("execute")
    result = await live_activity.reap_stale(pool, reaper_seconds=600)
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["kind"] == "live_activity_reap_stale_failed"


# ---------------------------------------------------------------------------
# prompt_manager.UnifiedPromptManager._init_langfuse_client
# ---------------------------------------------------------------------------


def test_init_langfuse_client_emits_finding_when_site_config_unavailable(
    monkeypatch,
):
    calls = _capture(monkeypatch)
    mgr = UnifiedPromptManager(site_config=SiteConfig())
    mgr._site_config = None
    import services.prompt_manager as pm_module

    monkeypatch.setattr(
        pm_module, "_sc", MagicMock(side_effect=RuntimeError("no container")),
    )
    result = mgr._init_langfuse_client()
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "langfuse_client_init_failed"


def test_init_langfuse_client_emits_finding_when_site_config_read_fails(
    monkeypatch,
):
    calls = _capture(monkeypatch)

    class _RaisingSiteConfig:
        def get(self, key, default=None):
            raise RuntimeError("settings cache corrupted")

    mgr = UnifiedPromptManager(site_config=_RaisingSiteConfig())
    result = mgr._init_langfuse_client()
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "langfuse_client_init_failed"
    # Same shared helper/dedup_key as the site_config-unavailable case.
    assert calls[0]["dedup_key"] == "langfuse_client_init_failed"


# ---------------------------------------------------------------------------
# research_service.ResearchService
# ---------------------------------------------------------------------------


def _raising_pool_fetch() -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("db down"))
    return pool


@pytest.mark.asyncio(loop_scope="session")
async def test_find_internal_links_emits_finding_on_db_error(monkeypatch):
    calls = _capture(monkeypatch)
    svc = ResearchService(pool=_raising_pool_fetch(), site_config=SiteConfig())
    result = await svc._find_internal_links("some topic words here")
    assert result == []
    assert len(calls) == 1
    assert calls[0]["kind"] == "internal_link_search_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_web_search_emits_finding_on_researcher_failure(monkeypatch):
    calls = _capture(monkeypatch)
    svc = ResearchService(pool=None, site_config=SiteConfig())

    with patch(
        "services.web_research.WebResearcher",
        side_effect=RuntimeError("duckduckgo unreachable"),
    ):
        result = await svc._web_search("some topic")

    assert result == []
    assert len(calls) == 1
    assert calls[0]["kind"] == "web_search_failed"


# ---------------------------------------------------------------------------
# template_runner.TemplateRunner._postgres_enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_postgres_enabled_emits_finding_when_flag_read_fails(monkeypatch):
    calls = _capture(monkeypatch)

    class _RaisingSiteConfig:
        def get_bool(self, key, default=False):
            raise RuntimeError("settings cache corrupted")

    runner = TemplateRunner.__new__(TemplateRunner)
    runner._site_config = _RaisingSiteConfig()
    result = await runner._postgres_enabled()
    assert result is False
    assert len(calls) == 1
    assert calls[0]["kind"] == "postgres_checkpointer_flag_read_failed"
