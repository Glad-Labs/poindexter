"""integration_db: one task can distribute two renders to the same target.

``pipeline_distributions`` was keyed ``UNIQUE (task_id, target)``, which says
"one task delivers one artifact per platform". The video lane makes that false:
a post produces BOTH a long-form ``video`` and a ``video_short``, both dispatched
to ``target='youtube'`` under the same ``task_id``, so the second upsert
overwrote the first and one YouTube handle was silently lost. Five of the twelve
prod rows were in that state, and the orphaned Shorts were invisible to
``services/youtube_metadata_sync.py`` — which reads this table — so they kept a
pre-#3517 4,800-char description and never got the ``#Shorts`` title suffix.

The harness applies the full migration chain, so this asserts the end state
against a real Postgres: the key admits both renders, and it still collapses a
re-dispatch of the SAME render onto the row it supersedes (the property a
``(task_id, target, external_id)`` key would have lost, since a re-upload mints
a new video id).
"""

from __future__ import annotations

import pytest

# The real dispatcher statement, so this test fails if the upsert and the key
# ever drift apart.
from services.jobs.dispatch_handles import _RECORD_DISTRIBUTION_SQL
from services.pipeline_db import SITE_TARGET as _SITE_TARGET

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_TASK = "task-medium-key-test"


async def _seed_task(conn) -> None:
    """pipeline_distributions.task_id is a FK to pipeline_tasks."""
    await conn.execute(
        "INSERT INTO pipeline_tasks (task_id, topic, status) "
        "VALUES ($1, 'medium key test', 'completed') "
        "ON CONFLICT (task_id) DO NOTHING",
        _TASK,
    )


async def test_unique_key_includes_medium(test_pool) -> None:
    async with test_pool.acquire() as conn:
        keys = await conn.fetch(
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
              FROM pg_constraint
             WHERE conrelid = 'pipeline_distributions'::regclass
               AND contype = 'u'
            """
        )
    defs = [k["def"] for k in keys]
    assert any("UNIQUE (task_id, target, medium)" in d for d in defs), defs
    assert not any(d.strip() == "UNIQUE (task_id, target)" for d in defs), (
        "the two-column key must be gone — while it exists a post's Short "
        "overwrites its long-form YouTube row"
    )


async def test_medium_defaults_so_existing_writers_need_no_change(test_txn) -> None:
    """Every site-publish row predates the column, and that path delivers one
    undifferentiated artifact. The default has to make those rows correct
    without the caller naming a medium.

    The target is the own-site sentinel — what ``PipelineDB.add_distribution``
    is actually called with on publish.
    """
    await _seed_task(test_txn)
    await test_txn.execute(
        "INSERT INTO pipeline_distributions (task_id, target, status) "
        "VALUES ($1, $2, 'published')",
        _TASK, _SITE_TARGET,
    )
    medium = await test_txn.fetchval(
        "SELECT medium FROM pipeline_distributions "
        " WHERE task_id = $1 AND target = $2",
        _TASK, _SITE_TARGET,
    )
    assert medium == "default"


async def test_long_form_and_short_coexist_under_one_target(test_txn) -> None:
    """The regression itself, through the real dispatcher SQL."""
    await _seed_task(test_txn)
    for medium, vid in (("video", "LONGvid123"), ("video_short", "SHORTvid12")):
        await test_txn.execute(
            _RECORD_DISTRIBUTION_SQL,
            _TASK, "youtube", medium, vid,
            f"https://www.youtube.com/watch?v={vid}", None,
        )

    rows = await test_txn.fetch(
        "SELECT medium, external_id FROM pipeline_distributions "
        " WHERE task_id = $1 AND target = 'youtube' ORDER BY medium",
        _TASK,
    )
    assert [(r["medium"], r["external_id"]) for r in rows] == [
        ("video", "LONGvid123"),
        ("video_short", "SHORTvid12"),
    ]


async def test_redispatch_of_the_same_render_updates_in_place(test_txn) -> None:
    """A re-dispatch mints a NEW video id but is the SAME render, so it must
    refresh its row rather than append a second one claiming the superseded
    upload is still live. This is why the key is the medium and not the
    external_id."""
    await _seed_task(test_txn)
    for vid in ("FIRSTvid12", "SECONDvid1"):
        await test_txn.execute(
            _RECORD_DISTRIBUTION_SQL,
            _TASK, "youtube", "video", vid,
            f"https://www.youtube.com/watch?v={vid}", None,
        )

    rows = await test_txn.fetch(
        "SELECT external_id, external_url FROM pipeline_distributions "
        " WHERE task_id = $1 AND target = 'youtube'",
        _TASK,
    )
    assert len(rows) == 1
    assert rows[0]["external_id"] == "SECONDvid1"
    assert rows[0]["external_url"].endswith("SECONDvid1")


# ---------------------------------------------------------------------------
# The recovery half: re-running the migration reinstates a clobbered render.
# ---------------------------------------------------------------------------
#
# The schema half above is what stops the bug recurring; this is what undoes the
# damage it already did, and it is the only part that touches real lost data. The
# migration is idempotent by construction (ADD COLUMN IF NOT EXISTS / DROP
# CONSTRAINT IF EXISTS / ON CONFLICT DO NOTHING), so seeding a prod-shaped
# collision and calling ``up()`` again exercises the backfill exactly as it ran.

class _TxnPool:
    """Hand the migration the open test transaction, so its writes roll back."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _load_migration():
    """Load the migration by path — its module name starts with a digit, so it
    is not importable by name. Same spec_from_file_location the runner uses."""
    import importlib.util
    from pathlib import Path

    path = next(
        (Path(__file__).resolve().parents[2] / "services" / "migrations").glob(
            "20260901_173133_*.py"
        )
    )
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RECOVERY_TASK = "task-clobbered-short"
_LONG = "LONGvidAAAA"
_SHORT = "SHORTvidBBB"


async def _seed_prod_shaped_collision(conn) -> str:
    """Reproduce what prod looked like on 2026-09-01 for one colliding task.

    Two media_assets rows, each holding its own YouTube handle (that table never
    collided), but only ONE pipeline_distributions row — the long form, which
    overwrote the Short's row seconds after it was written.
    """
    post_id = await conn.fetchval(
        "INSERT INTO posts (title, slug, content, status) "
        "VALUES ('Clobbered short', 'clobbered-short', 'body', 'published') "
        "RETURNING id"
    )
    await conn.execute(
        "INSERT INTO pipeline_tasks (task_id, topic, status) "
        "VALUES ($1, 'clobbered short', 'completed')",
        _RECOVERY_TASK,
    )
    for asset_type, vid in (("video", _LONG), ("video_short", _SHORT)):
        await conn.execute(
            """
            INSERT INTO media_assets
                (type, source, task_id, post_id, storage_path,
                 platform_video_ids, updated_at)
            VALUES ($1, 'pipeline', $2, $3, '/tmp/x.mp4', $4::jsonb,
                    TIMESTAMPTZ '2026-08-31 17:34:16.680893+00')
            """,
            asset_type, _RECOVERY_TASK, post_id, f'{{"youtube": "{vid}"}}',
        )
    # The survivor: the long form, with medium still at the pre-migration default
    # (nothing recorded which render a row described — that was the bug).
    await conn.execute(
        """
        INSERT INTO pipeline_distributions
            (task_id, target, medium, status, external_id, external_url, post_id)
        VALUES ($1, 'youtube', 'default', 'published', $2, $3, $4)
        """,
        _RECOVERY_TASK, _LONG, f"https://www.youtube.com/watch?v={_LONG}", post_id,
    )
    return str(post_id)


async def test_backfill_recovers_the_overwritten_short(test_txn) -> None:
    mig = _load_migration()
    post_id = await _seed_prod_shaped_collision(test_txn)

    await mig.up(_TxnPool(test_txn))

    rows = await test_txn.fetch(
        "SELECT medium, external_id, external_url, post_id::text AS post_id, "
        "       published_at "
        "  FROM pipeline_distributions "
        " WHERE task_id = $1 AND target = 'youtube' ORDER BY medium",
        _RECOVERY_TASK,
    )
    assert [(r["medium"], r["external_id"]) for r in rows] == [
        # The survivor was typed from its handle — leaving it 'default' would
        # make sync-metadata rebuild a Short's title as long-form.
        ("video", _LONG),
        # …and the render that had no row at all is back.
        ("video_short", _SHORT),
    ]

    short = rows[1]
    assert short["external_url"] == f"https://www.youtube.com/watch?v={_SHORT}"
    assert short["post_id"] == post_id
    # media_assets.updated_at is stamped at handle capture, so it is the honest
    # publish time for a row being reconstructed after the fact.
    assert short["published_at"].isoformat().startswith("2026-08-31T17:34:16")


async def test_backfill_is_idempotent(test_txn) -> None:
    """The migration runner skips applied files, but a restored backup or a
    re-run must not duplicate the rows it recovered."""
    await _seed_prod_shaped_collision(test_txn)
    mig = _load_migration()

    await mig.up(_TxnPool(test_txn))
    await mig.up(_TxnPool(test_txn))

    count = await test_txn.fetchval(
        "SELECT count(*) FROM pipeline_distributions "
        " WHERE task_id = $1 AND target = 'youtube'",
        _RECOVERY_TASK,
    )
    assert count == 2
