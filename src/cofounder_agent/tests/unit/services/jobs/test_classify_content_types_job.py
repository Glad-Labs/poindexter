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


@pytest.mark.asyncio
async def test_one_row_llm_failure_does_not_abort_the_batch():
    """A transient per-row LLM error (e.g. the litellm/Ollama connection
    blip that produced scheduler.classify_content_types:job_failure findings)
    must not fail the whole run — the failing post just stays unlabeled and
    is retried on the next 6-hourly fire (idempotent query), matching the
    per-item resilience every sibling LLM-calling job already has
    (affiliate_import, retention_summarize_to_table, retention_embeddings_collapse).
    """
    rows = [
        _row(pid="11111111-1111-1111-1111-111111111111", title="post A"),
        _row(pid="22222222-2222-2222-2222-222222222222", title="post B"),
    ]
    pool, conn = _pool(rows)
    with patch(f"{_MODULE}.get_prompt_manager") as gpm, patch(
        f"{_MODULE}.ollama_chat_text",
        new=AsyncMock(
            side_effect=[
                Exception(
                    'litellm.APIConnectionError: OllamaException - Post '
                    '"http://127.0.0.1:19195/tokenize": dial tcp '
                    '127.0.0.1:19195: connectex: ...'
                ),
                '{"labels":[{"label":"ai-ml","confidence":0.9}]}',
            ]
        ),
    ):
        gpm.return_value.get_prompt = MagicMock(return_value="PROMPT")
        result = await ClassifyContentTypesJob().run(pool, _cfg())
    # The whole run still reports success — one bad row must not page the operator.
    assert result.ok
    # Only post B (the second call) got labeled; post A stays unlabeled and will
    # be re-selected by _UNLABELED_SQL on the next run.
    assert result.changes_made == 1
    inserts = _inserts(conn)
    assert len(inserts) == 1
    assert "22222222-2222-2222-2222-222222222222" in str(inserts[0])
    assert result.metrics["posts_failed"] == 1
    assert result.metrics["posts_labeled"] == 1
