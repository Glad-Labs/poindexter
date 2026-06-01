"""DriveMediaGatesJob — drive the per-medium approval gate workflow.

Glad-Labs/poindexter#24. Replaces the retired ``IdleWorker`` tick: a
polling Job (default every 5 minutes) that, for every ``approved`` post,
walks ``advance_workflow`` and acts on the descriptor —

- a pending **medium** gate (podcast/video/short) with no artifact yet
  → fire that medium's generation (one medium per tick; the
  ``_artifact_exists`` guard keeps it single-fire / idempotent). Once the
  artifact lands, the gate stays ``pending`` for operator review.
- the ``final`` gate → auto-approve it (D2 / auto-advance), then publish.
- all gates decided (none rejected) → publish via
  ``publish_service.publish_now`` (text + media live together).

The driver generates media PRE-publish; publish fires only once the final
gate clears, so a post's media exists + has been reviewed before it goes
live. ``drive_once(pool)`` is the testable core; ``DriveMediaGatesJob`` is
the thin scheduler wrapper.
"""
from __future__ import annotations

import inspect
from typing import Any

from plugins.job import JobResult
from services.gates.post_approval_gates import (
    CANONICAL_GATE_NAMES,
    GATE_STATE_REVISING,
    MEDIUM_GATE_NAMES,
    advance_workflow,
    approve_gate,
    get_gates_for_post,
    reset_gate_to_pending,
)
from services.logger_config import get_logger

logger = get_logger(__name__)

# gate name -> media_assets.type (the 'short' gate stores 'video_short' assets;
# confirmed against podcast_service.py:869 / video_service.py:1102).
_GATE_TO_ASSET = {"podcast": "podcast", "video": "video", "short": "video_short"}


def _resolve_site_config() -> Any:
    """Resolve a SiteConfig from the process container (wired at startup),
    falling back to a fresh empty instance. The driver runs outside any
    request/DI scope, so it can't be handed one directly."""
    try:
        from services.container_registry import get_container
        return get_container().site_config
    except Exception:
        from services.site_config import SiteConfig
        return SiteConfig()


async def _approved_post_ids(pool: Any) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id FROM posts WHERE status = 'approved'"
        )
    return [r["id"] for r in rows]


async def _artifact_exists(pool: Any, post_id: str, medium: str) -> bool:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT 1 FROM media_assets WHERE post_id::text = $1 AND type = $2 LIMIT 1",
            str(post_id), _GATE_TO_ASSET[medium],
        ) is not None


async def _delete_media_asset(pool: Any, post_id: str, medium: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM media_assets WHERE post_id::text = $1 AND type = $2",
            str(post_id), _GATE_TO_ASSET[medium],
        )


async def _handle_revising_media(pool: Any, post_id: str, *, site_config: Any) -> int:
    """Handle operator-requested revisions (D1).

    For each medium gate the operator bounced back (state ``revising`` —
    set by ``revise_gate``), delete the stale ``media_assets`` row,
    re-trigger generation, and reset the gate toward ``pending`` for
    re-review. Returns the number of media regenerated.

    Necessary as a dedicated scan because ``advance_workflow`` only
    surfaces ``pending`` gates — a ``revising`` gate is invisible to it,
    so without this the driver would skip past it to ``final`` and
    publish a post that's mid-revision.
    """
    regenerated = 0
    for gate in await get_gates_for_post(pool, post_id):
        if gate["state"] == GATE_STATE_REVISING and gate["gate_name"] in MEDIUM_GATE_NAMES:
            medium = gate["gate_name"]
            await _delete_media_asset(pool, post_id, medium)
            await _trigger_media_gen(pool, post_id, medium, site_config=site_config)
            await reset_gate_to_pending(pool, post_id, medium)
            regenerated += 1
            logger.info(
                "[drive_media_gates] regenerated %s for post %s (revise → pending)",
                medium, post_id,
            )
    return regenerated


async def _trigger_media_gen(pool: Any, post_id: str, medium: str, *, site_config: Any) -> None:
    """Fire the generator for ``medium`` with the same args publish_service
    used to pass (post title/content + reused SEO from the posts row).

    Generators are called via their module attribute (not a top-level
    import) so tests can monkeypatch them; the ``inspect.isawaitable`` guard
    keeps both the real ``async def`` generators and sync test stubs working.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT title, content, excerpt, seo_keywords FROM posts WHERE id::text = $1",
            str(post_id),
        )
    if row is None:
        logger.warning("[drive_media_gates] post %s vanished before media-gen", post_id)
        return

    title = row["title"] or ""
    content = row["content"] or ""
    seo_description = row["excerpt"] or ""
    seo_keywords = row["seo_keywords"] or ""

    from services import podcast_service, video_service

    if medium == "podcast":
        result = podcast_service.generate_podcast_episode(
            post_id, title, content, site_config=site_config,
            seo_description=seo_description, seo_keywords=seo_keywords,
        )
    elif medium == "video":
        result = video_service.generate_video_episode(
            post_id, title, content, site_config=site_config,
            seo_description=seo_description, seo_keywords=seo_keywords,
        )
    elif medium == "short":
        result = video_service.generate_short_video_for_post(
            post_id, title, content, site_config=site_config,
        )
    else:
        return

    if inspect.isawaitable(result):
        await result
    logger.info("[drive_media_gates] triggered %s generation for post %s", medium, post_id)


async def drive_once(pool: Any, *, site_config: Any = None) -> dict[str, int]:
    """Advance every approved post one step toward publish.

    Per post, take deterministic actions until a wait-state is hit:
    generate a pending medium (then wait for operator review), auto-approve
    ``final``, or publish when all gates are decided.
    """
    summary = {"generated": 0, "published": 0, "awaiting_operator": 0, "regenerated": 0}
    if site_config is None:
        site_config = _resolve_site_config()

    for post_id in await _approved_post_ids(pool):
        # D1: an operator-requested revision (gate 'revising') is invisible
        # to advance_workflow, so handle it first. If anything regenerated,
        # the post is mid-regen — skip the advance loop this tick (revisit
        # next tick once the fresh artifact is up for review).
        regenerated = await _handle_revising_media(pool, post_id, site_config=site_config)
        if regenerated:
            summary["regenerated"] += regenerated
            continue

        # Bounded advance loop — only ``final`` auto-approval loops (a
        # one-shot transition to ready_to_distribute), so 2 iterations
        # suffice; the bound is a defensive backstop against logic errors.
        for _ in range(len(CANONICAL_GATE_NAMES) + 2):
            adv = await advance_workflow(pool, post_id)
            if adv.finished:
                break
            if adv.ready_to_distribute:
                from services.publish_service import publish_now
                await publish_now(pool, post_id, site_config=site_config)
                summary["published"] += 1
                break

            name = (adv.next_gate or {}).get("gate_name")
            if name in MEDIUM_GATE_NAMES:
                if not await _artifact_exists(pool, post_id, name):
                    await _trigger_media_gen(pool, post_id, name, site_config=site_config)
                    summary["generated"] += 1
                else:
                    # Artifact present — it's the operator's turn to review.
                    summary["awaiting_operator"] += 1
                break  # one medium per tick; stop advancing this post
            elif name == "final":
                # D2 / auto-advance: no human-final gate configured, so the
                # driver clears it, then re-advances → ready_to_distribute.
                await approve_gate(pool, post_id, "final", approver="auto:driver")
                continue
            else:
                # Unknown / escalation gate (e.g. media_generation_failed) —
                # operator must decide. Leave it pending.
                summary["awaiting_operator"] += 1
                break

    logger.info(
        "[drive_media_gates] tick: generated=%d regenerated=%d published=%d awaiting_operator=%d",
        summary["generated"], summary["regenerated"], summary["published"],
        summary["awaiting_operator"],
    )
    return summary


class DriveMediaGatesJob:
    """Scheduler wrapper for :func:`drive_once`.

    Cadence is the class ``schedule`` string (every 5 minutes ≈ the old
    IdleWorker tick); operators override per-install via the
    ``plugin.job.drive_media_gates`` row's ``config.schedule``.
    """

    name = "drive_media_gates"
    description = "Drive per-medium gate workflow: generate media + publish on clear"
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        summary = await drive_once(pool, site_config=site_config)
        return JobResult(
            ok=True,
            detail=(
                f"generated {summary['generated']}, regenerated {summary['regenerated']}, "
                f"published {summary['published']}, "
                f"awaiting_operator {summary['awaiting_operator']}"
            ),
            changes_made=(
                summary["generated"] + summary["regenerated"] + summary["published"]
            ),
        )
