"""``trace_read`` — the assembling read layer for the console task-trace.

One service stitches a request's story from the tables that already hold it:
``atom_runs`` (node spine + QA rail verdicts + per-node cost), ``pipeline_tasks``
(live progress), ``pipeline_versions.stage_data`` (corpus + final content),
``pipeline_gate_history`` (HITL decisions), and ``cost_logs`` (spend rollup).

The load-bearing seam is ``runs_for_request`` — the ONE place "gather a request's
1..N runs" lives (spec R7): retries and composed media DAGs are both just "more
runs" under the same ``task_id``, so the arbitrary-composition roadmap needs no
redesign. Everything is read-only and guards to honest-empty on error — the UI
never fabricates a spine or a number.

Runs against the disposable ``integration_db`` DB (real schema, incl. the
``output_preview`` column). Uses ``test_pool``.
"""

from __future__ import annotations

import pytest

from services import trace_read

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _seed_run(conn, *, task_id, run_id, template_slug, nodes, minutes_ago):
    """Insert one run's atom_runs rows. ``nodes`` = [(seq, atom, status), ...]."""
    for seq, atom, status in nodes:
        await conn.execute(
            """
            INSERT INTO atom_runs
              (run_id, task_id, template_slug, seq, atom, node_id, status,
               latency_ms, cost, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now() - ($10 || ' minutes')::interval)
            """,
            run_id, task_id, template_slug, seq, atom, atom.replace(".", "_"),
            status, 100 + seq, 0.01, str(minutes_ago),
        )


async def test_runs_for_request_groups_retries_and_media(test_pool):
    task_id = "trace-read-T1"
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM atom_runs WHERE task_id = $1", task_id)
        # R1: older canonical_blog run that halted (a retry once R2 supersedes it)
        await _seed_run(c, task_id=task_id, run_id="R1", template_slug="canonical_blog",
                        nodes=[(0, "content.generate_draft", "ok"), (1, "qa.aggregate", "halted")],
                        minutes_ago=20)
        # R2: newer canonical_blog run that completed — the default content run
        await _seed_run(c, task_id=task_id, run_id="R2", template_slug="canonical_blog",
                        nodes=[(0, "content.generate_draft", "ok"), (1, "qa.aggregate", "ok"),
                               (2, "content.persist_task", "ok")],
                        minutes_ago=10)
        # R3: a media DAG under the same task_id
        await _seed_run(c, task_id=task_id, run_id="R3", template_slug="media_pipeline",
                        nodes=[(0, "stage.render_video", "ok")],
                        minutes_ago=5)

    runs = await trace_read.runs_for_request(test_pool, task_id)

    by_id = {r["run_id"]: r for r in runs}
    assert {r["run_id"]: r["kind"] for r in runs} == {
        "R1": "retry", "R2": "content", "R3": "media",
    }
    assert runs[0]["run_id"] == "R2"  # latest content run sorts first (default selection)
    assert by_id["R2"]["node_count"] == 3
    assert by_id["R2"]["status"] == "ok" and by_id["R2"]["halted_at"] is None
    assert by_id["R1"]["status"] == "halted" and by_id["R1"]["halted_at"] == 1


async def test_runs_for_request_unknown_task_is_empty(test_pool):
    assert await trace_read.runs_for_request(test_pool, "no-such-task") == []


async def test_get_active_returns_running_and_recent(test_pool):
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM pipeline_tasks WHERE task_id LIKE 'trace-active-%'")
        await c.execute(
            """
            INSERT INTO pipeline_tasks
              (task_id, task_type, topic, status, stage, percentage, message,
               template_slug, started_at, last_progress_at)
            VALUES ('trace-active-run', 'blog_post', 'A live topic', 'in_progress',
                    'content.generate_draft', 42, 'drafting…', 'canonical_blog',
                    now() - interval '2 min', now())
            """
        )
        await c.execute(
            """
            INSERT INTO pipeline_tasks
              (task_id, task_type, topic, status, stage, template_slug,
               started_at, completed_at)
            VALUES ('trace-active-done', 'blog_post', 'A finished topic', 'published',
                    'done', 'canonical_blog', now() - interval '1 hour',
                    now() - interval '50 min')
            """
        )

    out = await trace_read.get_active(test_pool, recent_limit=10)

    assert isinstance(out, dict) and "runs" in out and "recent" in out
    running = {r["task_id"]: r for r in out["runs"]}
    assert "trace-active-run" in running
    assert running["trace-active-run"]["percentage"] == 42
    assert running["trace-active-run"]["stage"] == "content.generate_draft"
    assert any(r["task_id"] == "trace-active-done" for r in out["recent"])


async def test_get_summary_shape(test_pool):
    out = await trace_read.get_summary(test_pool)
    # Honest-empty-safe: always a dict with the KPI keys, values may be None/0.
    assert isinstance(out, dict)
    for key in ("window_hours", "tasks", "by_status"):
        assert key in out
    assert isinstance(out["by_status"], dict)


async def test_get_trace_assembles_spine_and_corpus(test_pool):
    task_id = "trace-read-deepdive"
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM atom_runs WHERE task_id = $1", task_id)
        await c.execute("DELETE FROM pipeline_tasks WHERE task_id = $1", task_id)
        await c.execute("DELETE FROM pipeline_versions WHERE task_id = $1", task_id)
        await _seed_run(c, task_id=task_id, run_id=task_id, template_slug="canonical_blog",
                        nodes=[(0, "content.generate_draft", "ok"), (1, "qa.aggregate", "ok")],
                        minutes_ago=5)
        await c.execute(
            """
            INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage, template_slug)
            VALUES ($1, 'blog_post', 'Deep-dive topic', 'awaiting_approval', 'done', 'canonical_blog')
            """,
            task_id,
        )
        await c.execute(
            """
            INSERT INTO pipeline_versions (task_id, version, title, content, quality_score, stage_data)
            VALUES ($1, 1, 'Deep-dive title', 'body', 88,
                    jsonb_build_object('task_metadata',
                      jsonb_build_object('research_context', '- Source A -> https://a.example')))
            """,
            task_id,
        )

    trace = await trace_read.get_trace(test_pool, task_id, None)

    assert trace["task_id"] == task_id
    assert trace["run_id"] == task_id  # default selection = the (only) content run
    assert [n["seq"] for n in trace["nodes"]] == [0, 1]
    assert [n["atom"] for n in trace["nodes"]] == ["content.generate_draft", "qa.aggregate"]
    assert "Source A" in trace["corpus"]
    assert trace["task"]["topic"] == "Deep-dive topic"
    assert trace["final"]["title"] == "Deep-dive title"
    assert trace["halt"] is None  # nothing halted


async def test_get_trace_unknown_task_is_honest_empty(test_pool):
    trace = await trace_read.get_trace(test_pool, "no-such-task", None)
    assert trace["task_id"] == "no-such-task"
    assert trace["nodes"] == [] and trace["runs"] == []
    assert trace["corpus"] == "" and trace["task"] is None
