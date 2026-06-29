"""Atom: social.generate_drafts — create social post drafts after persist_task.

Reads social_draft_platforms + social_reddit_subreddits from app_settings.
Creates one social_post_drafts row per platform/subreddit.
No-op when social_drafts_enabled=false (old Telegram/Discord path stays active).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta
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
    site_config: SiteConfig | None = state.get("site_config")  # type: ignore[assignment]
    if not site_config:
        return {}
    if site_config.get("social_drafts_enabled", "false").lower() not in ("true", "1", "yes"):
        return {}

    pipeline_task_id: str = state.get("task_id") or ""
    if not pipeline_task_id:
        logger.warning("[social.generate_drafts] no task_id in state — skipping")
        return {}

    title: str = state.get("title") or state.get("topic") or ""
    slug: str = state.get("post_slug") or state.get("slug") or ""
    excerpt: str = state.get("excerpt") or state.get("seo_description") or ""
    keywords: list[str] = _parse_csv(state.get("seo_keywords") or "")
    pool = state.get("pool")

    platforms_raw = site_config.get("social_draft_platforms", "")
    platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]
    if not platforms:
        return {}

    text_platforms = [
        p for p in platforms if p in ("twitter", "linkedin", "mastodon", "bluesky")
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
            logger.error("[social.generate_drafts] text draft generation failed: %s", exc)

    if "reddit" in platforms:
        subreddits_raw = site_config.get("social_reddit_subreddits", "")
        subreddits = [s.strip() for s in subreddits_raw.split(",") if s.strip()]
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
