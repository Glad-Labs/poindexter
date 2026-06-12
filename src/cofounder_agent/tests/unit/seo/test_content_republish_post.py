"""Tests for content.republish_post atom.

Verifies:
- meta_only invariant (UPDATE posts SET must NOT touch content column)
- R2 export is called
- ISR revalidation is called
- seo_opportunities baseline stamp is issued
- the source pipeline_tasks row is marked terminal (so the stale-sweep can't
  re-claim and re-refresh it — #763 defect #3)
- missing post_id raises RuntimeError
- missing task_id raises RuntimeError (fail-loud: the task would otherwise be
  left in_progress forever)
"""

import pytest

from modules.content.atoms import content_republish_post as atom


@pytest.mark.asyncio
async def test_republish_updates_meta_exports_and_stamps(monkeypatch):
    calls = {"update": None, "export": None, "reval": None, "stamp": None, "task_done": None}

    class _Conn:
        async def execute(self, sql, *args):
            if "UPDATE posts" in sql:
                calls["update"] = (sql, args)
                assert "content" not in sql.lower().split("set", 1)[1]  # body untouched
            elif "UPDATE seo_opportunities" in sql:
                calls["stamp"] = args
            elif "UPDATE pipeline_tasks" in sql:
                calls["task_done"] = (sql, args)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    class _DB:
        pool = _Pool()

    async def fake_export(pool, slug, *, site_config):
        calls["export"] = slug
        return True

    async def fake_reval(slug, paths=None, tags=None, *, site_config):
        calls["reval"] = slug
        return True

    monkeypatch.setattr(atom, "export_post", fake_export)
    monkeypatch.setattr(atom, "trigger_isr_revalidate", fake_reval)

    state = {
        "task_id": "task-abc-123",
        "post_id": "11111111-1111-1111-1111-111111111111",
        "post_slug": "old-title",
        "seo_title": "New SEO",
        "seo_description": "New desc",
        "seo_keywords": "a, b",
        "seo_opportunity_id": "22222222-2222-2222-2222-222222222222",
        "database_service": _DB(),
        "site_config": object(),
    }
    out = await atom.run(state)
    assert calls["export"] == "old-title"
    assert calls["reval"] == "old-title"
    assert out["status"] == "refreshed"
    assert calls["stamp"] is not None  # baseline + status stamped

    # The source task must be marked terminal — keyed by task_id, flipped off
    # in_progress so reclaim_stale_inprogress_tasks can't re-run the refresh.
    assert calls["task_done"] is not None, "pipeline_tasks was never marked terminal"
    done_sql, done_args = calls["task_done"]
    assert "status" in done_sql.lower()
    assert "'completed'" in done_sql.lower()  # terminal, not a re-publish
    assert done_args[0] == "task-abc-123"  # keyed on task_id


@pytest.mark.asyncio
async def test_missing_post_id_raises():
    with pytest.raises(RuntimeError):
        await atom.run(
            {
                "task_id": "task-abc-123",
                "post_slug": "x",
                "database_service": type("D", (), {"pool": object()})(),
            }
        )


@pytest.mark.asyncio
async def test_missing_task_id_raises():
    # Fail loud: without a task_id the atom can't mark the task terminal, so it
    # must not silently complete and leave the row in_progress (#763 defect #3).
    with pytest.raises(RuntimeError):
        await atom.run(
            {
                "post_id": "11111111-1111-1111-1111-111111111111",
                "post_slug": "x",
                "database_service": type("D", (), {"pool": object()})(),
            }
        )
