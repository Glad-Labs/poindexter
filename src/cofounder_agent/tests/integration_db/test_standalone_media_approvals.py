"""Gate-2 review for a video with no post (poindexter#1140), on the real schema.

A creative-studio cartoon persists as a task-keyed ``media_assets`` row and
never gets a post. Migration ``20261013_144053`` keys ``media_approvals`` on a
``subject_id`` (the post, else the task) so that video can be reviewed. These
run the service's real SQL: seed, list, decide and file lookup, plus the
``media_distribute`` query that seeds the row, against the migrated schema.
"""

import uuid

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(autouse=True)
def _no_real_notifier(monkeypatch):
    """The review ping would otherwise reach the operator's real Discord."""
    sent: list[str] = []

    async def _fake_notify(message, **_kw):
        sent.append(message)

    monkeypatch.setattr(
        "poindexter.services.integrations.operator_notify.notify_operator", _fake_notify,
    )
    return sent


async def _standalone_task(conn) -> str:
    task_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO pipeline_tasks (task_id, topic) VALUES ($1, $2)",
        task_id, "Why VRAM decides which models you can run",
    )
    await conn.execute(
        """
        INSERT INTO media_assets (type, source, storage_provider, storage_path, task_id,
                                  title, description, metadata)
        VALUES ('video', 'pipeline', 'local', $2, $1, $3, $4, '{"standalone": true}'::jsonb)
        """,
        task_id, f"/tmp/{task_id}.mp4", "Kai and Mira Explain VRAM", "Two friends, one GPU.",
    )
    return task_id


async def test_standalone_row_is_keyed_on_its_task(test_txn, _no_real_notifier):
    from poindexter.services import media_approval_service as mas

    task_id = await _standalone_task(test_txn)
    status = await mas.record_pending_standalone(test_txn, task_id, "video")
    assert status == "pending"
    # Idempotent: a second seed neither raises nor duplicates.
    await mas.record_pending_standalone(test_txn, task_id, "video")

    row = await test_txn.fetchrow(
        "SELECT post_id, task_id, subject_id FROM media_approvals WHERE task_id = $1", task_id,
    )
    assert row["post_id"] is None
    assert row["subject_id"] == task_id
    assert await test_txn.fetchval(
        "SELECT count(*) FROM media_approvals WHERE task_id = $1", task_id,
    ) == 1
    # The review ping names it standalone, with the screenplay's title.
    assert any("standalone" in m and "Kai and Mira" in m for m in _no_real_notifier)


async def test_standalone_row_lists_decides_and_resolves_its_file(test_txn):
    from poindexter.services import media_approval_service as mas

    task_id = await _standalone_task(test_txn)
    await mas.record_pending_standalone(test_txn, task_id, "video")

    pending = await mas.list_pending(test_txn, medium="video", limit=500)
    item = next(r for r in pending if r["subject_id"] == task_id)
    assert item["standalone"] is True
    assert item["post_id"] is None
    assert item["title"] == "Kai and Mira Explain VRAM"

    assert await mas.get_asset_storage_path(test_txn, task_id, "video") == f"/tmp/{task_id}.mp4"

    await mas.decide(test_txn, task_id, "video", approved=True, decided_by="operator:test")
    assert await mas.is_approved(test_txn, task_id, "video")
    await mas.record_dispatched(test_txn, task_id, "video", success=True)
    assert await test_txn.fetchval(
        "SELECT dispatch_success FROM media_approvals WHERE subject_id = $1", task_id,
    ) is True


async def test_post_rows_keep_their_old_keys(test_txn):
    """Existing post-keyed writes still find their arbiter and their row."""
    from poindexter.services import media_approval_service as mas

    post_id = await test_txn.fetchval(
        """
        INSERT INTO posts (id, title, slug, content, status)
        VALUES (gen_random_uuid(), 'Subject key post', $1, 'body', 'draft')
        RETURNING id::text
        """,
        f"subject-key-{uuid.uuid4().hex[:8]}",
    )
    await mas.record_pending(test_txn, post_id, "video")
    await mas.record_pending(test_txn, post_id, "video")  # ON CONFLICT (post_id, medium)
    await mas.decide(test_txn, post_id, "video", approved=False, decided_by="operator:test")
    assert await test_txn.fetchval(
        "SELECT status FROM media_approvals WHERE post_id = $1::uuid AND medium = 'video'", post_id,
    ) == "rejected"


async def test_distribute_seeds_standalone_once(test_txn):
    """``_UNLINKED_SQL`` offers the standalone asset until its row exists."""
    from poindexter.services import media_approval_service as mas
    from poindexter.services.jobs import media_distribute as md

    task_id = await _standalone_task(test_txn)
    rows = await test_txn.fetch(md._UNLINKED_SQL, list(md._TYPE_TO_MEDIUM.keys()), 500)
    mine = [r for r in rows if r["task_id"] == task_id]
    assert len(mine) == 1 and mine[0]["standalone"] is True

    await mas.record_pending_standalone(test_txn, task_id, "video")
    rows = await test_txn.fetch(md._UNLINKED_SQL, list(md._TYPE_TO_MEDIUM.keys()), 500)
    assert not [r for r in rows if r["task_id"] == task_id]
