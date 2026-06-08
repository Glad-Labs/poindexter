"""Integration: run the graph_def pipeline path end-to-end with a fake writer.

Regression guard for the #572 class of bug — "graph_def writer returns
empty content". That bug lived in the graph_def → compile → invoke →
state-propagation chain: a node produced ``content`` but it never
survived to the terminal ``finalize_task`` node (LangGraph state-channel
drop). These tests exercise the REAL machinery — the real
``build_graph_from_spec`` compiler and the real ``TemplateRunner.run``
invocation path — with stub stages (no Ollama, no live DB) so the
content-propagation contract is asserted without external services.

Two tests:

1. ``test_canonical_blog_spec_compiles`` — compile the ACTUAL prod
   ``CANONICAL_BLOG_GRAPH_DEF`` via ``build_graph_from_spec`` + LangGraph
   ``.compile()``. Guards against the prod spec drifting into a
   non-compileable shape (every stage.* / qa.* / seo.* node must resolve
   to a registered callable).

2. ``test_graphdef_run_propagates_content_to_finalize`` — build a
   minimal ``verify_task -> generate_content -> finalize_task`` graph_def
   from REAL stage names, swap in stub stage implementations (the
   generate_content stub yields non-empty ``content`` from a fake LLM),
   and run it through a real ``TemplateRunner`` with
   ``pipeline_use_graph_def=true``. Assert the writer's ``content``
   survives to ``finalize_task`` (the #572 invariant) and the graph
   reaches the terminal node.

Marked ``@pytest.mark.integration``. Skips if langgraph isn't installed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Gate the whole module on the heavy graph deps so a slim env skips
# cleanly instead of erroring at collection.
langgraph = pytest.importorskip("langgraph", reason="langgraph not installed")

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fakes — a pool that satisfies the runner's best-effort DB touches, a
# SiteConfig with the graph_def flag on, and stub stages.
# ---------------------------------------------------------------------------


def _make_fake_pool() -> Any:
    """asyncpg pool double.

    The graph_def path touches the pool for best-effort observability:
    ``PluginConfig.load`` (fetchval → None = stage enabled),
    ``_mark_stage_column`` / capability_outcomes / atom_runs (acquire →
    execute). All are wrapped in try/except or tolerate None, so a pool
    that returns benign values keeps the run going without a real DB.
    """
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="OK")
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value="OK")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_site_config() -> Any:
    """Real SiteConfig with the graph_def flag on + Postgres checkpointer
    off (so the runner uses a transient in-memory MemorySaver, no DB)."""
    from services.site_config import SiteConfig

    return SiteConfig(
        initial_config={
            "pipeline_use_graph_def": "true",
            "template_runner_use_postgres_checkpointer": "false",
            "template_runner_progress_streaming": "false",
            "atom_runs_capture_enabled": "false",
        }
    )


# ---------------------------------------------------------------------------
# Test 1 — the real prod spec compiles
# ---------------------------------------------------------------------------


def test_canonical_blog_spec_compiles():
    """The prod CANONICAL_BLOG_GRAPH_DEF compiles via the real compiler.

    Every stage.* / qa.* / seo.* node must resolve to a registered
    callable and the wiring must form a valid LangGraph. A spec that
    references a deleted stage or a typo'd atom name fails here.
    """
    from services import pipeline_architect
    from services.atom_registry import discover
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    discover()  # surface stage.* + register qa.* / seo.* atoms (idempotent)

    ok, errors = pipeline_architect._validate_spec(CANONICAL_BLOG_GRAPH_DEF)
    assert ok, f"prod spec failed validation: {errors}"

    graph = pipeline_architect.build_graph_from_spec(
        CANONICAL_BLOG_GRAPH_DEF, pool=_make_fake_pool(), record_sink=[],
    )
    compiled = graph.compile()
    assert compiled is not None


def test_media_pipeline_spec_compiles():
    """The Stage-2 media_pipeline spec (#689) compiles via the real compiler.

    The media.load_scripts atom must resolve to a registered callable and the
    wiring must form a valid LangGraph. Guards against the media_pipeline spec
    drifting into a non-compileable shape (e.g. a renamed/removed atom).
    """
    from services import pipeline_architect
    from services.atom_registry import discover
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF

    discover()  # register media.* atoms (auto-discovered under modules.content.atoms)

    ok, errors = pipeline_architect._validate_spec(MEDIA_PIPELINE_GRAPH_DEF)
    assert ok, f"media_pipeline spec failed validation: {errors}"

    graph = pipeline_architect.build_graph_from_spec(
        MEDIA_PIPELINE_GRAPH_DEF, pool=_make_fake_pool(), record_sink=[],
    )
    compiled = graph.compile()
    assert compiled is not None


# ---------------------------------------------------------------------------
# Test 2 — content survives the graph_def run to finalize_task
# ---------------------------------------------------------------------------

FAKE_WRITER_CONTENT = (
    "# A Real Post About Testing\n\n"
    "This is non-empty body content produced by the fake writer. "
    "It must survive every downstream node and arrive intact at "
    "finalize_task — that is the #572 invariant.\n"
)


@pytest.mark.asyncio
async def test_graphdef_run_propagates_content_to_finalize(monkeypatch):
    """Run a minimal real graph_def through a real TemplateRunner with a
    FAKE writer atom and assert content reaches finalize_task.

    The graph: ``verify_task -> generate_content -> finalize_task``,
    authored as a graph_def with the SAME ``stage.*`` atom names the
    prod spec uses. We swap the registry's runner for each of those
    three stage atoms with a stub (the generate_content stub yields
    non-empty ``content`` — no Ollama). The finalize_task stub records
    the ``content`` it OBSERVED in state, so we can assert the writer's
    output propagated through LangGraph's state channels to the terminal
    node — the exact link that broke in #572.

    This exercises the REAL ``build_graph_from_spec`` compiler and the
    REAL ``TemplateRunner.run`` path (graph_def branch, MemorySaver
    checkpointer), so it is a true regression guard for the empty-content
    class of bug — only the stage bodies are stubbed.
    """
    from services import atom_registry
    from services.atom_registry import discover
    from services.template_runner import TemplateRunner

    discover()  # ensure stage.* virtual atoms exist before we override them

    # finalize_task stub captures what content it observed in state.
    seen: dict[str, Any] = {}

    async def _verify_runner(state):
        return {"verified": True}

    async def _generate_runner(state):
        # The fake "writer": produces non-empty content + title, no LLM.
        return {
            "content": FAKE_WRITER_CONTENT,
            "title": "A Real Post About Testing",
            "content_length": len(FAKE_WRITER_CONTENT),
        }

    async def _finalize_runner(state):
        seen["content"] = state.get("content")
        seen["title"] = state.get("title")
        seen["reached"] = True
        return {"status": "awaiting_approval"}

    # Override the registry runners for exactly these three stage atoms.
    # build_graph_from_spec resolves stage.* nodes through
    # get_atom_callable() -> _RUNNERS, so swapping these routes the nodes
    # to our stubs while the rest of the compile/invoke machinery is real.
    runners = dict(atom_registry._RUNNERS)
    runners["stage.verify_task"] = _verify_runner
    runners["stage.generate_content"] = _generate_runner
    runners["stage.finalize_task"] = _finalize_runner
    monkeypatch.setattr(atom_registry, "_RUNNERS", runners)

    minimal_spec = {
        "name": "canonical_blog",
        "description": "minimal graph_def for the #572 content-propagation guard",
        "entry": "verify_task",
        "nodes": [
            {"id": "verify_task", "atom": "stage.verify_task"},
            {"id": "generate_content", "atom": "stage.generate_content"},
            {"id": "finalize_task", "atom": "stage.finalize_task"},
        ],
        "edges": [
            {"from": "verify_task", "to": "generate_content"},
            {"from": "generate_content", "to": "finalize_task"},
            {"from": "finalize_task", "to": "END"},
        ],
    }

    # load_active_graph_def is what TemplateRunner.run reads to get the
    # graph_def for the slug — return our minimal spec (no DB).
    async def _fake_load_active_graph_def(_pool, _slug):
        return minimal_spec

    monkeypatch.setattr(
        "services.pipeline_templates.load_active_graph_def",
        _fake_load_active_graph_def,
    )

    pool = _make_fake_pool()
    runner = TemplateRunner(pool=pool, site_config=_make_site_config())

    summary = await runner.run(
        "canonical_blog",
        {"task_id": "t-graphdef-1", "topic": "Testing"},
    )

    # The run completed without halting.
    assert summary.ok, f"graph_def run halted at {summary.halted_at}"

    # The #572 invariant: the writer's content propagated to finalize_task.
    assert seen.get("reached") is True, "finalize_task node never ran"
    assert seen.get("content"), (
        "finalize_task saw EMPTY content — this is the #572 regression "
        "(graph_def state channel dropped the writer's output)"
    )
    assert seen["content"] == FAKE_WRITER_CONTENT

    # And the final state carries the content too.
    assert summary.final_state.get("content") == FAKE_WRITER_CONTENT
    # The terminal node ran (recorded in the run summary by node_id).
    node_ids = {r.node_id for r in summary.records}
    assert "finalize_task" in node_ids
    assert "generate_content" in node_ids


# ---------------------------------------------------------------------------
# Test 3 — media-artifact channels survive graph_def state merge (#674)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graphdef_media_artifacts_survive_to_terminal(monkeypatch):
    """#674 guard: media artifacts returned by the media stages must reach
    the terminal node via LangGraph state channels. Fails if any of the
    five media keys is an undeclared PipelineState channel (LangGraph drops
    undeclared keys on the graph_def path)."""
    from services import atom_registry
    from services.atom_registry import discover
    from services.template_runner import TemplateRunner

    discover()
    seen: dict[str, Any] = {}

    async def _verify_runner(state):
        return {"verified": True}

    async def _media_scripts_runner(state):
        return {
            "podcast_script": "PODCAST",
            "video_scenes": ["scene-a", "scene-b"],
            "short_summary_script": "SHORT",
            "video_ambient_audio_path": "/tmp/ambient.wav",
            "podcast_audio_path": "/tmp/podcast_tts.wav",
            "podcast_intro_audio_path": "/tmp/intro.wav",
        }

    async def _shot_list_runner(state):
        return {"video_shot_list": {"version": 1, "shots": [{"idx": 0}]}}

    async def _finalize_runner(state):
        seen["podcast_script"] = state.get("podcast_script")
        seen["video_scenes"] = state.get("video_scenes")
        seen["short_summary_script"] = state.get("short_summary_script")
        seen["video_ambient_audio_path"] = state.get("video_ambient_audio_path")
        seen["video_shot_list"] = state.get("video_shot_list")
        seen["podcast_audio_path"] = state.get("podcast_audio_path")
        seen["podcast_intro_audio_path"] = state.get("podcast_intro_audio_path")
        return {"status": "awaiting_approval"}

    runners = dict(atom_registry._RUNNERS)
    runners["stage.verify_task"] = _verify_runner
    runners["stage.generate_media_scripts"] = _media_scripts_runner
    runners["stage.generate_video_shot_list"] = _shot_list_runner
    runners["stage.finalize_task"] = _finalize_runner
    monkeypatch.setattr(atom_registry, "_RUNNERS", runners)

    minimal_spec = {
        "name": "canonical_blog",
        "description": "#674 media-artifact propagation guard",
        "entry": "verify_task",
        "nodes": [
            {"id": "verify_task", "atom": "stage.verify_task"},
            {"id": "generate_media_scripts", "atom": "stage.generate_media_scripts"},
            {"id": "generate_video_shot_list", "atom": "stage.generate_video_shot_list"},
            {"id": "finalize_task", "atom": "stage.finalize_task"},
        ],
        "edges": [
            {"from": "verify_task", "to": "generate_media_scripts"},
            {"from": "generate_media_scripts", "to": "generate_video_shot_list"},
            {"from": "generate_video_shot_list", "to": "finalize_task"},
            {"from": "finalize_task", "to": "END"},
        ],
    }

    async def _fake_load_active_graph_def(_pool, _slug):
        return minimal_spec

    monkeypatch.setattr(
        "services.pipeline_templates.load_active_graph_def",
        _fake_load_active_graph_def,
    )

    runner = TemplateRunner(pool=_make_fake_pool(), site_config=_make_site_config())
    summary = await runner.run("canonical_blog", {"task_id": "t-media-1", "topic": "Testing"})

    assert summary.ok, f"graph_def run halted at {summary.halted_at}"
    assert seen.get("podcast_script") == "PODCAST"
    assert seen.get("video_scenes") == ["scene-a", "scene-b"]
    assert seen.get("short_summary_script") == "SHORT"
    assert seen.get("video_ambient_audio_path") == "/tmp/ambient.wav"
    assert seen.get("video_shot_list") == {"version": 1, "shots": [{"idx": 0}]}
    assert seen.get("podcast_audio_path") == "/tmp/podcast_tts.wav"
    assert seen.get("podcast_intro_audio_path") == "/tmp/intro.wav"
