"""Tests for ``services/jobs/topic_auto_resolve.py``.

Roundtrips against the real Postgres test DB via the ``db_pool`` fixture
(``tests/unit/conftest.py``); skipped automatically when no live Postgres
DSN is reachable. The job's batch-eligibility check is raw SQL, so a real
DB is required to catch the 2026-06-11 wedge: a batch whose candidates are
ALL internal-RAG (zero external) was invisible to the old
``EXISTS (SELECT 1 FROM topic_candidates ...)`` gate, so it was never
resolved and the niche silently stalled for ~2 days. A mocked pool can't
exercise the SQL, so these run on the real schema.

``_handoff_to_pipeline`` is monkeypatched so the tests don't need
``pipeline_tasks`` template config or downstream pipeline machinery — the
unit under test is the eligibility selection + ``operator_rank`` fix-up +
``resolve_batch`` wiring, which is exactly where the bug lived.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.jobs.topic_auto_resolve import TopicAutoResolveJob
from services.niche_service import NicheService
from services.site_config import SiteConfig

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _enable_auto_resolve(db_pool) -> None:
    """Flip the master switch on in the test DB (default is off)."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description)
            VALUES ('topic_auto_resolve_enabled', 'true', 'testing',
                    'enabled for topic_auto_resolve job tests')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
        )


async def _seed_open_batch(
    db_pool, niche_id, *, internal: int, external: int, expires_days: int = 7,
    external_title: str | None = None,
):
    """Insert one ``open`` batch with ``external`` external + ``internal``
    internal candidates. ``rank_in_batch`` descends from 1 across both
    pools combined (external first), mirroring how ``_write_batch`` lays
    them out. ``external_title`` overrides the default per-candidate
    title (used by the topic-sanity tests to seed a garbage winner).
    Returns the batch id.
    """
    expires = datetime.now(timezone.utc) + timedelta(days=expires_days)
    async with db_pool.acquire() as conn:
        batch_id = await conn.fetchval(
            "INSERT INTO topic_batches (niche_id, status, expires_at) "
            "VALUES ($1, 'open', $2) RETURNING id",
            niche_id, expires,
        )
        rank = 0
        for i in range(external):
            rank += 1
            await conn.execute(
                """
                INSERT INTO topic_candidates
                  (batch_id, niche_id, source_name, source_ref, title, summary,
                   score, score_breakdown, rank_in_batch, decay_factor)
                VALUES ($1, $2, 'external', $3, $4, $5, $6, '{}'::jsonb, $7, 1.0)
                """,
                batch_id, niche_id, f"ext-ref-{i}",
                external_title if external_title is not None
                else f"External Topic {i}",
                f"External summary {i}",
                90 - rank, rank,
            )
        for i in range(internal):
            rank += 1
            await conn.execute(
                """
                INSERT INTO internal_topic_candidates
                  (batch_id, niche_id, source_kind, primary_ref,
                   supporting_refs, distilled_topic, distilled_angle,
                   score, score_breakdown, rank_in_batch, decay_factor)
                VALUES ($1, $2, 'claude_session', $3, '[]'::jsonb, $4, $5,
                        $6, '{}'::jsonb, $7, 1.0)
                """,
                batch_id, niche_id, f"int-ref-{i}",
                f"Internal Topic {i}", f"Internal angle {i}",
                90 - rank, rank,
            )
    return batch_id


@pytest.fixture
def _no_queue_throttle(monkeypatch):
    """Pretend the approval queue is never full so the throttle gate
    doesn't short-circuit the job. ``is_queue_full`` is imported inside
    ``run()`` so we patch the source module."""
    async def _not_full(pool):
        return (False, 0, 100)

    monkeypatch.setattr("services.pipeline_throttle.is_queue_full", _not_full)


@pytest.fixture
def _fake_handoff(monkeypatch):
    """Capture ``_handoff_to_pipeline`` calls instead of inserting a real
    ``pipeline_tasks`` row. ``resolve_batch`` still flips the batch to
    ``resolved`` after the (faked) handoff, so the status assertions hold.
    Returns the list of recorded ``(winner_id, niche_slug, batch_id)``."""
    calls: list[tuple[str, str, str]] = []

    async def _handoff(self, winner, niche, batch_id):
        calls.append((winner.id, niche.slug, str(batch_id)))

    monkeypatch.setattr(
        "services.topic_batch_service.TopicBatchService._handoff_to_pipeline",
        _handoff,
    )
    return calls


@pytest.mark.unit
class TestMetadata:
    def test_name(self):
        assert TopicAutoResolveJob.name == "topic_auto_resolve"

    def test_schedule_every_2h(self):
        assert TopicAutoResolveJob.schedule == "0 */2 * * *"


async def test_resolves_internal_only_batch(db_pool, _no_queue_throttle, _fake_handoff):
    """2026-06-11 wedge regression.

    A batch whose candidates are ALL internal-RAG (zero external) must
    still be selected and resolved. Before the two-table eligibility fix,
    ``EXISTS (SELECT 1 FROM topic_candidates ...)`` was false for such a
    batch, so the job reported "no open batches with candidates",
    ``changes_made`` was 0, and the niche sweep then refused to open a
    replacement while the dead batch stayed ``open`` — a silent stall.
    """
    await _enable_auto_resolve(db_pool)
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="auto-resolve-internal-only", name="IntOnly", batch_size=5,
    )
    batch_id = await _seed_open_batch(db_pool, n.id, internal=3, external=0)

    result = await TopicAutoResolveJob().run(db_pool, {"_site_config": SiteConfig()})

    assert result.changes_made == 1, result.detail
    assert len(_fake_handoff) == 1
    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM topic_batches WHERE id = $1", batch_id,
        )
        # The job transcribes rank_in_batch → operator_rank on the
        # internal table too (resolve_batch reads operator_rank=1).
        op_rank_1 = await conn.fetchval(
            "SELECT operator_rank FROM internal_topic_candidates "
            "WHERE batch_id = $1 AND rank_in_batch = 1",
            batch_id,
        )
    assert status == "resolved"
    assert op_rank_1 == 1


async def test_resolves_external_only_batch(db_pool, _no_queue_throttle, _fake_handoff):
    """Sanity / no-regression: the classic external-only path still
    resolves exactly as before."""
    await _enable_auto_resolve(db_pool)
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="auto-resolve-external-only", name="ExtOnly", batch_size=5,
    )
    batch_id = await _seed_open_batch(db_pool, n.id, internal=0, external=2)

    result = await TopicAutoResolveJob().run(db_pool, {"_site_config": SiteConfig()})

    assert result.changes_made == 1, result.detail
    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM topic_batches WHERE id = $1", batch_id,
        )
    assert status == "resolved"


async def test_skips_expired_batch(db_pool, _no_queue_throttle, _fake_handoff):
    """Expired open batches must NOT be auto-resolved.

    Resolving a batch past its review window would push stale content,
    and — critically — would resurrect a long-dead open batch from a
    niche that has since moved to a different content path (e.g.
    dev_diary, whose posts come from its own daily cron). Uses an
    external-candidate batch so the *only* reason it is skipped is the
    new ``expires_at > NOW()`` filter (before the fix, the eligibility
    query had no expiry guard and would have resolved it).
    """
    await _enable_auto_resolve(db_pool)
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="auto-resolve-expired", name="Expired", batch_size=5,
    )
    batch_id = await _seed_open_batch(
        db_pool, n.id, internal=0, external=2, expires_days=-1,
    )

    result = await TopicAutoResolveJob().run(db_pool, {"_site_config": SiteConfig()})

    assert result.changes_made == 0
    assert len(_fake_handoff) == 0
    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM topic_batches WHERE id = $1", batch_id,
        )
    # Untouched — neither resolved nor mutated by the job.
    assert status == "open"


async def test_contentless_winner_expires_batch_instead_of_wedging(
    db_pool, _no_queue_throttle,
):
    """2026-06-30 dots-topic incident, auto-resolve seam.

    A batch whose rank-1 winner fails the topic-sanity gate raises
    ``TopicSanityError`` inside ``_handoff_to_pipeline``. If the job
    treated that as a generic error, the batch would stay ``open`` and
    retry-fail every cycle while blocking new sweeps — the recurring
    "content dark" niche wedge. Instead the job must self-heal (per
    ``feedback_self_heal_not_suppress``): expire the batch, write a
    ``topic_batch_auto_expired`` audit row, create NO pipeline task.

    Deliberately does NOT use ``_fake_handoff`` — the gate under test
    lives inside the real ``_handoff_to_pipeline`` (and fires before
    any template/DB dependency it has).
    """
    await _enable_auto_resolve(db_pool)
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="auto-resolve-topic-sanity", name="Sanity Heal", batch_size=5,
    )
    # The real topic from pipeline_tasks 9921678f-9b5b-4d24-9f07-c9d0398cf793.
    batch_id = await _seed_open_batch(
        db_pool, n.id, internal=0, external=1,
        external_title=". .. . ... . .... . .... . ... .",
    )

    result = await TopicAutoResolveJob().run(
        db_pool, {"_site_config": SiteConfig()},
    )

    # The expiry counts as the cycle's change; it is not an error.
    assert result.ok is True, result.detail
    assert result.changes_made == 1, result.detail

    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM topic_batches WHERE id = $1", batch_id,
        )
        task_count = await conn.fetchval(
            "SELECT COUNT(*) FROM pipeline_tasks WHERE topic_batch_id = $1",
            batch_id,
        )
        audit_count = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE event_type = 'topic_batch_auto_expired' "
            "  AND details::jsonb ->> 'batch_id' = $1",
            str(batch_id),
        )
    assert status == "expired"
    assert task_count == 0
    assert audit_count == 1
