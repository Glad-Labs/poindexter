"""Unit tests — final gap-site burn-down batch (batch 6b).

Fifteen single-shot sites plus three loop-shaped sites across
modules/routes/plugins/services/utils, all previously swallowing a
genuine failure at ``logger.debug`` and returning a sentinel. Each
single-shot site now fires exactly one ``emit_finding(info)``; the three
loop-shaped sites (self_consistency_rail, web_research, startup_manager)
use the aggregate-count pattern established in batch 5 — one finding
after the batch/gather completes, never per-iteration.

Five further sites in this same sweep are intentionally left
``# silent-ok`` and are not tested here: ``database_service.py`` (runs
before the pool exists — nothing to emit through yet), ``ollama_client.py``
(``check_health`` — unreachable Ollama is explicitly documented as
normal), ``media_asset_recorder.py`` (dedupe check — worst case is a
duplicate row, not data loss), ``post_pipeline_actions.py`` (preview QA —
already explicitly "non-critical"), and ``doctor.py`` (a diagnostic tool
with no logger, by design must not depend on the DB-backed findings
infra it might be called on to diagnose).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.internal_link_coherence import get_tag_slugs_for_post
from modules.finance.probes import _default_egress_ip_fetch
from plugins.llm_providers.gemini import GeminiProvider
from plugins.scheduler import PluginScheduler
from services import content_revisions_logger, experiment_runner, profiling
from services import publishing_adapters_db, research_context, task_failure_alerts
from services import tasks_db as tasks_db_module
from services.integrations import operator_notify
from services.jobs import run_dev_diary_post
from services.self_consistency_rail import _sample_summaries
from services.web_research import WebResearcher
from utils import startup_manager as startup_manager_module

pytestmark = pytest.mark.unit


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


def _raising_pool_acquire(exc: Exception) -> MagicMock:
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(side_effect=exc)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


# ---------------------------------------------------------------------------
# One-off sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_get_tag_slugs_for_post_emits_finding_on_db_error(monkeypatch):
    calls = _capture(monkeypatch)
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("db down"))
    result = await get_tag_slugs_for_post(pool, post_id="post-1")
    assert result == set()
    assert len(calls) == 1
    assert calls[0]["kind"] == "link_coherence_tag_lookup_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_egress_ip_fetch_emits_finding_on_httpx_failure(monkeypatch):
    calls = _capture(monkeypatch)
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value="https://echo.example/ip")
    with patch("httpx.AsyncClient", side_effect=RuntimeError("no network")):
        result = await _default_egress_ip_fetch(pool)
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "finance_egress_ip_lookup_failed"


def test_gemini_is_enabled_emits_finding_on_site_config_error():
    provider = GeminiProvider(site_config=None)

    class _RaisingSiteConfig:
        def get(self, key, default=None):
            raise RuntimeError("cache corrupted")

    calls: list[dict] = []
    import utils.findings as findings_module
    orig = findings_module.emit_finding
    findings_module.emit_finding = lambda **kw: calls.append(kw)
    try:
        result = provider._is_enabled(_RaisingSiteConfig())
    finally:
        findings_module.emit_finding = orig
    assert result is False
    assert len(calls) == 1
    assert calls[0]["kind"] == "gemini_enabled_flag_read_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_persisted_last_run_epoch_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch)
    scheduler = PluginScheduler.__new__(PluginScheduler)
    scheduler._pool = _raising_pool_acquire(RuntimeError("pool down"))
    result = await scheduler._persisted_last_run_epoch("some_job")
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "scheduler_last_run_lookup_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_cost_guard_check_emits_finding_on_import_failure(monkeypatch):
    from routes.triage_routes import _cost_guard_check

    calls = _capture(monkeypatch)
    monkeypatch.setitem(sys.modules, "services.cost_guard", None)
    site_config = SimpleNamespace(get=lambda k, d=None: d)
    await _cost_guard_check(site_config)
    assert len(calls) == 1
    assert calls[0]["kind"] == "cost_guard_module_unavailable"


@pytest.mark.asyncio(loop_scope="session")
async def test_log_revision_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _raising_pool_acquire(RuntimeError("db down"))
    result = await content_revisions_logger.log_revision(
        pool, task_id="task-1", content="hello world", change_type="draft",
    )
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "content_revision_log_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_weighted_selection_enabled_emits_finding_on_conn_error(monkeypatch):
    calls = _capture(monkeypatch)
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
    result = await experiment_runner._weighted_selection_enabled(conn)
    assert result is False
    assert len(calls) == 1
    assert calls[0]["kind"] == "weighted_selection_flag_read_failed"


def test_resolve_site_config_emits_finding_when_get_site_config_raises(monkeypatch):
    calls: list[dict] = []
    import utils.findings as findings_module
    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))

    fake_shared_context = MagicMock()
    fake_shared_context.get_site_config = MagicMock(
        side_effect=RuntimeError("no lifespan bound"),
    )
    monkeypatch.setitem(
        sys.modules, "services.integrations.shared_context", fake_shared_context,
    )
    result = operator_notify._resolve_site_config()
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "notify_site_config_resolve_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_last_run_date_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch)
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
    result = await run_dev_diary_post._get_last_run_date(pool)
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "dev_diary_last_run_read_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_podcast_record_episode_asset_emits_finding_on_import_failure(
    monkeypatch,
):
    from services.podcast_service import EpisodeResult, PodcastService

    calls = _capture(monkeypatch)
    # `from services import media_asset_recorder` resolves via the parent
    # package's cached attribute once ANY earlier test has imported it —
    # sys.modules alone isn't enough once that's happened, so strip both.
    import services as services_pkg
    monkeypatch.delattr(services_pkg, "media_asset_recorder", raising=False)
    monkeypatch.setitem(sys.modules, "services.media_asset_recorder", None)
    svc = PodcastService.__new__(PodcastService)
    svc._site_config = SimpleNamespace(_pool=None, get=lambda k, d=None: d)
    result = EpisodeResult(
        success=True, file_path="ep.mp3", duration_seconds=60, file_size_bytes=1000,
    )
    await svc._record_episode_asset(
        post_id="post-1", result=result, voice="en-US", title="Test Episode",
    )
    assert len(calls) == 1
    assert calls[0]["kind"] == "media_asset_recorder_unavailable"
    assert calls[0]["dedup_key"] == "media_asset_recorder_unavailable"


def test_setup_pyroscope_emits_finding_on_site_config_import_failure(monkeypatch):
    calls: list[dict] = []
    import utils.findings as findings_module
    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    monkeypatch.setitem(sys.modules, "services.site_config", None)
    profiling.setup_pyroscope("test-service", site_config=None)
    assert len(calls) == 1
    assert calls[0]["kind"] == "pyroscope_site_config_import_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_load_enabled_publishers_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _raising_pool_acquire(RuntimeError("db down"))
    result = await publishing_adapters_db.load_enabled_publishers(pool)
    assert result == []
    assert len(calls) == 1
    assert calls[0]["kind"] == "publishing_adapters_lookup_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_build_rag_context_emits_finding_on_outer_exception(monkeypatch):
    calls = _capture(monkeypatch)
    with patch(
        "poindexter.memory.MemoryClient", side_effect=RuntimeError("boom"),
        create=True,
    ):
        import types
        fake_module = types.ModuleType("poindexter.memory")
        fake_module.MemoryClient = MagicMock(side_effect=RuntimeError("boom"))
        grandparent = sys.modules.get("poindexter") or types.ModuleType("poindexter")
        grandparent.memory = fake_module
        monkeypatch.setitem(sys.modules, "poindexter", grandparent)
        monkeypatch.setitem(sys.modules, "poindexter.memory", fake_module)

        result = await research_context.build_rag_context(None, "some topic")
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "rag_context_build_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_persistent_dedup_check_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _raising_pool_acquire(RuntimeError("db down"))
    result = await task_failure_alerts._persistent_check_and_record(
        pool, "task-1", "hash-1", "boom", "warning", 3600,
    )
    assert result is False
    assert len(calls) == 1
    assert calls[0]["kind"] == "task_failure_dedup_check_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_heartbeat_task_emits_finding_on_pool_error(monkeypatch):
    calls = _capture(monkeypatch)
    db = tasks_db_module.TasksDatabase.__new__(tasks_db_module.TasksDatabase)
    db.pool = _raising_pool_acquire(RuntimeError("db down"))
    result = await db.heartbeat_task("task-1")
    assert result is False
    assert len(calls) == 1
    assert calls[0]["kind"] == "task_heartbeat_failed"


# ---------------------------------------------------------------------------
# Loop-shaped sites (aggregate-count pattern)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_sample_summaries_aggregates_failures_into_one_finding(monkeypatch):
    calls = _capture(monkeypatch)
    dispatch_mock = AsyncMock(side_effect=[
        RuntimeError("llm down"), RuntimeError("llm down"),
        SimpleNamespace(text="a valid summary"),
    ])
    with patch(
        "services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
    ), patch(
        "services.llm_text.resolve_local_model", return_value="test-model",
    ):
        result = await _sample_summaries(
            topic="some topic", content="some content", n=3,
            temperature=0.7, site_config=SimpleNamespace(_pool=MagicMock()),
        )
    # Non-blocking — the one successful sample still comes through.
    assert result == ["a valid summary"]
    assert len(calls) == 1
    assert calls[0]["kind"] == "self_consistency_sample_failed"
    assert "2 of 3" in calls[0]["title"] or "sample" in calls[0]["body"]


@pytest.mark.asyncio(loop_scope="session")
async def test_web_research_search_aggregates_extract_failures(monkeypatch):
    calls = _capture(monkeypatch)
    researcher = WebResearcher.__new__(WebResearcher)
    researcher._site_config = SimpleNamespace(get=lambda k, d=None: d)

    async def _fake_ddg_search(query, num_results):
        return [
            {"title": "A", "url": "https://a.example", "snippet": "", "content": ""},
            {"title": "B", "url": "https://b.example", "snippet": "", "content": ""},
        ]

    call_count = {"n": 0}

    async def _fake_extract(url, *, failures=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            if failures is not None:
                failures.append(f"{url}: extraction boom")
            return ""
        return "real content"

    researcher._ddg_search = _fake_ddg_search
    researcher._extract_content = _fake_extract
    researcher._web_research_int = lambda key, default: default

    results = await researcher.search("some query", num_results=2)

    # Non-blocking — both results are kept (one with empty content).
    assert len(results) == 2
    assert len(calls) == 1
    assert calls[0]["kind"] == "web_research_extract_failed"
    assert "1 of 2" in calls[0]["title"]


@pytest.mark.asyncio(loop_scope="session")
async def test_validate_ollama_model_settings_aggregates_template_failures(
    monkeypatch,
):
    calls = _capture(monkeypatch)
    import services.integrations.operator_notify as operator_notify_module

    mgr = startup_manager_module.StartupManager.__new__(
        startup_manager_module.StartupManager,
    )

    class _FakeSiteConfig:
        def get(self, key, default=None):
            if key == "ollama_base_url":
                return "http://localhost:11434"
            if key == "ollama_model_validation_enabled":
                return "true"
            return default

    mgr._site_config = _FakeSiteConfig()

    # pool.acquire() must be a real async context manager yielding a conn
    # whose .fetch() returns the configured *_model rows.
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"key": "pipeline_writer_model", "value": "ollama/model-a"},
        {"key": "pipeline_qa_model", "value": "ollama/model-b"},
    ])
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)

    # The shared http_client's .get() (tags list) succeeds; .post()
    # (/api/show, what _fetch_template calls per model) always fails —
    # isolates the failure to the site under test without touching the
    # earlier tags-fetch step.
    tags_response = MagicMock()
    tags_response.raise_for_status = MagicMock(return_value=None)
    tags_response.json = MagicMock(return_value={
        "models": [{"name": "model-a"}, {"name": "model-b"}],
    })
    fake_shared_client = MagicMock()
    fake_shared_client.get = AsyncMock(return_value=tags_response)
    fake_shared_client.post = AsyncMock(side_effect=RuntimeError("show failed"))
    monkeypatch.setattr(operator_notify_module, "http_client", fake_shared_client)

    async def _fake_notify_operator(*_a, **_kw):
        return None

    monkeypatch.setattr(
        operator_notify_module, "notify_operator", _fake_notify_operator,
    )

    await mgr._validate_ollama_model_settings(pool)

    assert len(calls) == 1
    assert calls[0]["kind"] == "model_template_fetch_failed"
    assert "2 of" in calls[0]["title"] or "model-a" in calls[0]["body"]
