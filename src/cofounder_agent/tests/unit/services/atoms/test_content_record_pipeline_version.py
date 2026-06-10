"""Unit tests: content.record_pipeline_version must persist the FULL
task_metadata blob (preview_token, pre_approve_content, …) to
pipeline_versions — never clobber it with an empty dict.

Regression guard for Glad-Labs/poindexter#693. On the canonical_blog
graph_def path content.persist_task assembles + writes the full metadata
one node earlier; content.record_pipeline_version then ran a SECOND
upsert reading an empty ``state["task_metadata"]`` channel. Because
``upsert_version`` merges via ``stage_data || EXCLUDED.stage_data`` (jsonb
shallow-merge, right side wins) and an empty ``{}`` is ``not None``, the
empty blob overwrote ``metadata->>'preview_token'`` back to ``{}`` for
every canonical_blog post — collapsing the Grafana approval-queue Preview
link (``NULL || … = NULL``) and dropping pre_approve_content /
video_shot_list / featured-image metadata too.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms.content_persist_task import run as persist_run
from modules.content.atoms.content_record_pipeline_version import run as record_run

PREVIEW_TOKEN = "abc123deadbeef0011223344"


def _capture_upserts() -> list[dict]:
    """Patch PipelineDB so every upsert_version(task_id, data) payload is recorded."""
    calls: list[dict] = []

    async def _upsert(task_id, data):
        calls.append(data)

    import services.pipeline_db as _pdb
    _pdb.PipelineDB = lambda *_a, **_k: SimpleNamespace(upsert_version=_upsert)
    return calls


def _make_db():
    async def _update_task(*, task_id, updates):
        pass

    return SimpleNamespace(
        pool=MagicMock(),
        update_task=_update_task,
        update_task_status_guarded=AsyncMock(return_value="ok"),
    )


def _finalized_state() -> dict:
    return {
        "task_id": "task-693",
        "content": "# Heading\n\nFinal body copy for the post.",
        "title": "Real Title",
        "database_service": _make_db(),
        "preview_token": PREVIEW_TOKEN,
        "seo_title": "SEO Title",
        "seo_description": "SEO description.",
        "seo_keywords": ["gpu", "ai"],
        "quality_score": 88,
        "featured_image_url": "https://img.example/x.png",
        "video_shot_list": {"version": 1, "shots": [{"idx": 0}]},
    }


@pytest.mark.asyncio
async def test_record_persists_preview_token_after_persist_task():
    """The graph runs persist_task → record_pipeline_version. record's
    upsert must carry the SAME non-empty metadata (preview_token +
    pre_approve_content), never an empty dict that clobbers the prior write."""
    calls = _capture_upserts()
    state = _finalized_state()

    persisted = await persist_run(state)
    # The graph threads persist_task's returned channels back onto shared state.
    state.update(persisted)
    await record_run(state)

    # calls[0] = persist_task's upsert; calls[1] = record's upsert.
    assert len(calls) == 2, f"expected 2 upserts, got {len(calls)}"
    record_data = calls[1]
    assert record_data["metadata"].get("preview_token") == PREVIEW_TOKEN
    assert record_data["task_metadata"].get("preview_token") == PREVIEW_TOKEN
    # The auto-publish edit-distance gate diffs against this snapshot.
    assert record_data["task_metadata"].get("pre_approve_content")


@pytest.mark.asyncio
async def test_persist_task_publishes_task_metadata_on_state():
    """content.persist_task must expose its assembled task_metadata on the
    returned state so the downstream record atom can re-assert it instead of
    reading an empty channel."""
    _capture_upserts()
    state = _finalized_state()

    result = await persist_run(state)

    assert result["task_metadata"]["preview_token"] == PREVIEW_TOKEN
    assert result["task_metadata"]["pre_approve_content"]


@pytest.mark.asyncio
async def test_record_does_not_clobber_with_empty_metadata():
    """When the task_metadata channel is empty, record must NOT write empty
    metadata blobs — an empty ``{}`` would shallow-merge-clobber the full
    metadata persist_task wrote one node earlier."""
    calls = _capture_upserts()
    state = _finalized_state()  # note: no task_metadata published on state

    await record_run(state)

    assert len(calls) == 1
    assert "metadata" not in calls[0]
    assert "task_metadata" not in calls[0]
