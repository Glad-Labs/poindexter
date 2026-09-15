"""qa.freshness's task-facts SQL vs the REAL schema.

The first shipped query read ``pipeline_tasks.metadata`` — a column that does
not exist — and every QA run logged "task lookup skipped (reduced coverage)",
so the news-source signal and the created_at fallback never loaded (prod,
2026-09-15). Same pin as test_chat_watch_schema / test_media_distribute_sql:
execute the real SQL.
"""
import json
import uuid

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_task_facts_sql_reads_created_at_and_discovered_by(test_pool):
    from poindexter.modules.content.atoms import qa_freshness

    task_id = f"freshtest-{uuid.uuid4().hex[:12]}"
    await test_pool.execute(
        "INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage) "
        "VALUES ($1, 'blog_post', 'freshness schema pin', 'in_progress', 'qa')",
        task_id,
    )
    await test_pool.execute(
        "INSERT INTO pipeline_versions (task_id, version, content, stage_data) "
        "VALUES ($1, 1, '', $2::jsonb)",
        task_id, json.dumps({"task_metadata": {"discovered_by": "hacker_news"}}),
    )
    try:
        created, discovered_by = await qa_freshness._task_facts(test_pool, task_id)
        assert created is not None
        assert discovered_by == "hacker_news"
    finally:
        await test_pool.execute("DELETE FROM pipeline_versions WHERE task_id = $1", task_id)
        await test_pool.execute("DELETE FROM pipeline_tasks WHERE task_id = $1", task_id)


async def test_task_facts_sql_without_a_version_row(test_pool):
    from poindexter.modules.content.atoms import qa_freshness

    task_id = f"freshtest-{uuid.uuid4().hex[:12]}"
    await test_pool.execute(
        "INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage) "
        "VALUES ($1, 'blog_post', 'freshness schema pin', 'in_progress', 'qa')",
        task_id,
    )
    try:
        created, discovered_by = await qa_freshness._task_facts(test_pool, task_id)
        assert created is not None and discovered_by == ""
    finally:
        await test_pool.execute("DELETE FROM pipeline_tasks WHERE task_id = $1", task_id)
