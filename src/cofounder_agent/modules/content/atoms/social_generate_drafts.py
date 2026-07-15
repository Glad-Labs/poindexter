"""Atom: social.generate_drafts — create social post drafts after persist_task.

Reads social_draft_platforms + social_reddit_subreddits from app_settings.
Creates one social_post_drafts row per platform/subreddit.
No-op when social_drafts_enabled=false (old Telegram/Discord path stays active).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta
from services.integrations.operator_notify import notify_operator
from services.social_drafts import SocialDraftsService
from services.social_poster import generate_social_posts

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="social.generate_drafts",
    type="atom",
    version="1.0.0",
    description="Generate social post drafts for Postiz distribution",
    requires=("task_id", "title", "post_slug"),
    produces=(),
)

_svc = SocialDraftsService()


async def run(state: dict[str, Any]) -> dict[str, Any]:
    site_config: Any = state.get("site_config")
    if not site_config:
        return {}
    if site_config.get("social_drafts_enabled", "false").lower() not in ("true", "1", "yes"):
        return {}

    pipeline_task_id: str = state.get("task_id") or ""
    if not pipeline_task_id:
        logger.warning("[social.generate_drafts] no task_id in state — skipping")
        return {}

    # Reach the DB pool through the graph_def seam: content_router_service
    # seeds ``database_service`` (with a ``.pool``), never a bare ``pool`` key.
    # Fall back to ``state["pool"]`` only for ad-hoc callers. Without a pool
    # there is nowhere to persist a draft, so skip before spending LLM calls —
    # mirrors media.persist's no-pool guard. Fixes #855: the atom previously
    # read ``state.get("pool")`` alone (always None on the canonical_blog
    # path), so ``create_draft`` got ``pool=None`` → ``'NoneType' object has
    # no attribute 'acquire'`` for every post.
    database_service = state.get("database_service")
    pool = (
        getattr(database_service, "pool", None)
        if database_service is not None
        else state.get("pool")
    )
    if pool is None:
        logger.warning(
            "[social.generate_drafts] no DB pool for task %s — skipping",
            pipeline_task_id,
        )
        return {}

    title: str = state.get("title") or state.get("topic") or ""
    # On canonical_blog the posts row does not exist yet — persist_task
    # deliberately produces post_slug=None (posts are created on approve).
    # Predict the FINAL publish slug through the same derive_publish_identity
    # chain publish_post_from_task runs at approve time, so the URL baked
    # into the copy is the URL the post actually goes live under. A real
    # post_slug in state (existing-post flows) still wins. Before this,
    # slug resolved to "" and every draft promoted the dead
    # ``{site_url}/posts/`` index URL (social-drafts linking bug).
    slug: str = state.get("post_slug") or ""
    if not slug:
        from services.publish_service import derive_publish_identity

        _title, _content, slug = derive_publish_identity(
            state.get("content") or "",
            state.get("title") or "",
            state.get("topic") or "",
            pipeline_task_id,
        )
    excerpt: str = state.get("excerpt") or state.get("seo_description") or ""
    keywords: list[str] = _parse_csv(state.get("seo_keywords") or "")

    platforms_raw = site_config.get("social_draft_platforms", "")
    platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]
    if not platforms:
        return {}

    # Idempotency pre-filter (poindexter#833): finalize re-runs (preview_gate
    # regen loops, checkpoint restore, task retry) must not stack duplicate
    # drafts — task 511012cc posted the same Bluesky promo three times. Keys
    # that already carry an active (pending/failed) or posted draft are
    # dropped BEFORE any copy is generated, so a replay spends zero LLM calls;
    # create_draft enforces the same rule DB-side (partial unique index +
    # NOT EXISTS guard) as the backstop.
    taken = await _svc.existing_draft_keys(pipeline_task_id, pool)
    if taken:
        logger.info(
            "[social.generate_drafts] task %s already has drafts for %s — "
            "generating only missing platforms",
            pipeline_task_id[:8],
            sorted(f"{p}/{s}" if s else p for p, s in taken),
        )

    text_platforms = [
        p
        for p in platforms
        if p in ("twitter", "linkedin", "mastodon", "bluesky")
        and (p, "") not in taken
    ]
    if text_platforms:
        try:
            posts = await generate_social_posts(
                title=title,
                slug=slug,
                excerpt=excerpt,
                keywords=keywords,
                site_config=site_config,
            )
            for post in posts:
                if post.platform in text_platforms:
                    await _svc.create_draft(
                        pipeline_task_id=pipeline_task_id,
                        platform=post.platform,
                        content=post.text,
                        platform_config={},
                        pool=pool,
                    )
        except Exception as exc:
            # Root-cause fix (poindexter#863): a log-only catch here used to
            # leave zero drafts with no alert and nothing for
            # RetryFailedSocialDraftsJob to pick up (it only retries EXISTING
            # 'failed' rows; this path never creates one). Stay non-fatal —
            # a social-copy hiccup must not block the post from publishing —
            # but notify_operator so the gap is visible instead of silent.
            # BackfillMissingSocialDraftsJob provides the actual retry.
            logger.error("[social.generate_drafts] text draft generation failed: %s", exc)
            await notify_operator(
                f"[social] draft generation failed for task "
                f"{pipeline_task_id[:8]} (platforms: {', '.join(text_platforms)}): "
                f"{exc}",
                critical=False,
                site_config=site_config,
            )

    if "reddit" in platforms:
        subreddits_raw = site_config.get("social_reddit_subreddits", "")
        subreddits = [
            s.strip()
            for s in subreddits_raw.split(",")
            if s.strip() and ("reddit", s.strip()) not in taken
        ]
        reddit_errors: list[str] = []
        for subreddit in subreddits:
            try:
                copy = await _generate_reddit_copy(
                    title=title,
                    slug=slug,
                    excerpt=excerpt,
                    subreddit=subreddit,
                    site_config=site_config,
                    pool=pool,
                )
                if copy:
                    await _svc.create_draft(
                        pipeline_task_id=pipeline_task_id,
                        platform="reddit",
                        content=copy,
                        platform_config={"subreddit": subreddit},
                        pool=pool,
                    )
            except Exception as exc:
                logger.error(
                    "[social.generate_drafts] reddit %s draft failed: %s", subreddit, exc
                )
                reddit_errors.append(f"{subreddit}: {exc}")
        # One batched notification per run, not one per failing subreddit —
        # a shared root cause (e.g. the LLM backend being down) would
        # otherwise page once per subreddit.
        if reddit_errors:
            await notify_operator(
                f"[social] reddit draft generation failed for task "
                f"{pipeline_task_id[:8]} ({len(reddit_errors)} subreddit(s)): "
                f"{'; '.join(reddit_errors)}",
                critical=False,
                site_config=site_config,
            )

    return {}


async def _generate_reddit_copy(
    title: str,
    slug: str,
    excerpt: str,
    subreddit: str,
    site_config: Any,
    pool: Any,
) -> str:
    from services.llm_text import ollama_chat_text
    from services.prompt_manager import UnifiedPromptManager

    model = site_config.get("social_poster_fallback_model", "")
    if not model:
        return ""

    post_url = f"{site_config.get('site_url', 'https://gladlabs.io')}/posts/{slug}"
    try:
        pm = UnifiedPromptManager(site_config=site_config)
        prompt = pm.get_prompt(
            "social.reddit_promote",
            subreddit=subreddit,
            title=title,
            excerpt=excerpt,
            post_url=post_url,
        )
    except (KeyError, Exception):
        prompt = (
            f"Write a Reddit post for {subreddit} promoting this article.\n"
            f"Title: {title}\nSummary: {excerpt}\nURL: {post_url}\n"
            "Rules: be conversational, match the subreddit culture, "
            "no spammy self-promotion, include genuine value first.\n"
            "Output only the post text."
        )

    text = await ollama_chat_text(
        prompt,
        model=model,
        site_config=site_config,
        pool=pool,
        tier="budget",
        phase="social_reddit_copy",
    )
    return (text or "").strip()


def _parse_csv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []
