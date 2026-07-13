"""Unit tests for ClassifyContentTypesJob (mocked pool + LLM + prompt)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.classify_content_types import ClassifyContentTypesJob

_MODULE = "services.jobs.classify_content_types"


class _FakeSC:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _cfg(enabled="true", labels="ai-ml,pc-hardware,gaming", **extra):
    base = {
        "_site_config": _FakeSC(
            {
                "content_type_labels": labels,
                "content_type_classifier_model": "test-model",
                "classify_content_types_enabled": enabled,
            }
        )
    }
    base.update(extra)
    return base


def _pool(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _row(pid="11111111-1111-1111-1111-111111111111", title="GPU for LLMs", tags="gpu,llm"):
    return {"id": pid, "title": title, "content": "body text", "tags": tags}


def _inserts(conn):
    return [c for c in conn.execute.call_args_list if "post_content_types" in str(c).lower()]


@pytest.mark.asyncio
async def test_disabled_is_noop():
    pool, conn = _pool([])
    result = await ClassifyContentTypesJob().run(pool, _cfg(enabled="false"))
    assert result.ok and result.changes_made == 0
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_labels_configured_is_noop():
    pool, conn = _pool([])
    result = await ClassifyContentTypesJob().run(pool, _cfg(labels="   "))
    assert result.ok and result.changes_made == 0
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_unlabeled_posts():
    pool, conn = _pool([])
    result = await ClassifyContentTypesJob().run(pool, _cfg())
    assert result.ok and result.changes_made == 0
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_classifies_and_inserts_only_valid_labels():
    pool, conn = _pool([_row()])
    with patch(f"{_MODULE}.get_prompt_manager") as gpm, patch(
        f"{_MODULE}.ollama_chat_text",
        new=AsyncMock(
            return_value='{"labels":[{"label":"ai-ml","confidence":0.9},'
            '{"label":"crypto","confidence":1}]}'
        ),
    ):
        gpm.return_value.get_prompt = MagicMock(return_value="PROMPT")
        result = await ClassifyContentTypesJob().run(pool, _cfg())
    assert result.ok and result.changes_made == 1
    inserts = _inserts(conn)
    assert len(inserts) == 1  # only ai-ml; crypto not in allowed set
    assert "ai-ml" in str(inserts[0])


@pytest.mark.asyncio
async def test_multi_label_inserts_each():
    pool, conn = _pool([_row()])
    with patch(f"{_MODULE}.get_prompt_manager") as gpm, patch(
        f"{_MODULE}.ollama_chat_text",
        new=AsyncMock(
            return_value='{"labels":[{"label":"ai-ml","confidence":0.9},'
            '{"label":"pc-hardware","confidence":0.8}]}'
        ),
    ):
        gpm.return_value.get_prompt = MagicMock(return_value="PROMPT")
        result = await ClassifyContentTypesJob().run(pool, _cfg())
    assert result.ok and result.changes_made == 1  # one post labeled
    assert len(_inserts(conn)) == 2  # two content-type rows


@pytest.mark.asyncio
async def test_no_valid_labels_inserts_nothing():
    pool, conn = _pool([_row()])
    with patch(f"{_MODULE}.get_prompt_manager") as gpm, patch(
        f"{_MODULE}.ollama_chat_text", new=AsyncMock(return_value="junk not json")
    ):
        gpm.return_value.get_prompt = MagicMock(return_value="PROMPT")
        result = await ClassifyContentTypesJob().run(pool, _cfg())
    assert result.ok and result.changes_made == 0
    assert _inserts(conn) == []


@pytest.mark.asyncio
async def test_batch_size_passed_to_query():
    pool, conn = _pool([])
    await ClassifyContentTypesJob().run(pool, _cfg(batch_size=25))
    # fetch called with the configured batch size as the LIMIT param
    args = conn.fetch.call_args.args
    assert 25 in args
