"""
Social Media Auto-Posting Service

Generates platform-specific social media posts (X/Twitter, LinkedIn) when a
blog post is published, using the local Ollama LLM for copy generation.

Posts are logged and forwarded to Telegram + Discord via the existing
notification pipeline.  Actual API posting to X/LinkedIn will be added later.

Usage from task_executor or any post-publish hook:

    from services.social_poster import generate_and_distribute_social_posts

    await generate_and_distribute_social_posts(
        title="Why Local LLMs Beat Cloud APIs",
        slug="why-local-llms-beat-cloud-apis",
        excerpt="A deep dive into cost, latency, and privacy ...",
        keywords=["LLM", "Ollama", "self-hosting"],
    )
"""

from services.logger_config import get_logger
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from .ollama_client import OllamaClient

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from services.site_config import site_config as _sc
SITE_BASE_URL = _sc.get("site_url", "https://localhost:3000")

# Notification targets (mirrors task_executor._notify_openclaw)
from services.telegram_config import TELEGRAM_BOT_TOKEN as _TELEGRAM_BOT_TOKEN
from services.telegram_config import TELEGRAM_CHAT_ID as _TELEGRAM_CHAT_ID
_OPENCLAW_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://localhost:18789")
try:
    _OPENCLAW_TOKEN = _sc.get("openclaw_hooks_token") or os.getenv("OPENCLAW_HOOKS_TOKEN", "hooks-gladlabs")
except Exception:
    _OPENCLAW_TOKEN = os.getenv("OPENCLAW_HOOKS_TOKEN", "hooks-gladlabs")
_DISCORD_OPS_CHANNEL = "1487683559065125055"

# LLM defaults — social copy is a simple task, use the fast 8B model
try:
    _SOCIAL_MODEL = _sc.get("social_poster_model") or os.getenv("SOCIAL_POSTER_MODEL", "ollama/llama3:latest")
except Exception:
    _SOCIAL_MODEL = os.getenv("SOCIAL_POSTER_MODEL", "ollama/llama3:latest")

# Platform character limits (with safety margin)
TWITTER_CHAR_LIMIT = 280
LINKEDIN_CHAR_LIMIT = 700


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SocialPost:
    """A generated social media post for a specific platform."""

    platform: str  # "twitter" | "linkedin"
    text: str
    post_url: str  # the blog URL being promoted
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    posted: bool = False  # will flip to True once we add API posting


# ---------------------------------------------------------------------------
# LLM generation
# ---------------------------------------------------------------------------


def _build_twitter_prompt(title: str, slug: str, excerpt: str, keywords: List[str]) -> str:
    post_url = f"{SITE_BASE_URL}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    return (
        f"You are a social media copywriter for a tech company called {_sc.get('company_name', '')}.\n"
        "Write a single tweet to promote the following blog post.\n\n"
        "Rules:\n"
        f"- The tweet MUST be under {TWITTER_CHAR_LIMIT} characters including the URL and hashtags.\n"
        "- Include the exact URL below — do not shorten or modify it.\n"
        "- Include 2-3 relevant hashtags from the keywords provided.\n"
        "- Be punchy and engaging. No generic filler.\n"
        "- Output ONLY the tweet text. No quotes, labels, or commentary.\n\n"
        f"Blog title: {title}\n"
        f"Excerpt: {excerpt}\n"
        f"URL: {post_url}\n"
        f"Suggested hashtags: {hashtags}\n"
    )


def _build_linkedin_prompt(title: str, slug: str, excerpt: str, keywords: List[str]) -> str:
    post_url = f"{SITE_BASE_URL}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    return (
        f"You are a social media copywriter for a tech company called {_sc.get('company_name', '')}.\n"
        "Write a LinkedIn post to promote the following blog article.\n\n"
        "Rules:\n"
        f"- The post MUST be under {LINKEDIN_CHAR_LIMIT} characters including the URL and hashtags.\n"
        "- Use a professional but approachable tone.\n"
        "- Include the exact URL below — do not shorten or modify it.\n"
        "- Include 2-3 relevant hashtags from the keywords provided.\n"
        "- Structure: hook line, 1-2 sentence summary, call to read, URL, hashtags.\n"
        "- Output ONLY the post text. No quotes, labels, or commentary.\n\n"
        f"Blog title: {title}\n"
        f"Excerpt: {excerpt}\n"
        f"URL: {post_url}\n"
        f"Suggested hashtags: {hashtags}\n"
    )


async def _generate_social_text(
    prompt: str,
    char_limit: int,
    platform: str,
    ollama: Optional[OllamaClient] = None,
) -> str:
    """Call the local Ollama LLM and return the generated text, trimmed to limit."""
    client = ollama or OllamaClient()
    model = _SOCIAL_MODEL.removeprefix("ollama/")  # OllamaClient expects bare model name

    try:
        result = await client.generate(
            prompt=prompt,
            model=model,
            temperature=0.8,
            max_tokens=300,
        )
        text = result.get("text", "").strip()

        # Strip wrapping quotes if the LLM added them
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()

        # Hard-truncate as a safety net (should rarely trigger with good prompts)
        if len(text) > char_limit:
            text = text[: char_limit - 3].rsplit(" ", 1)[0] + "..."
            logger.warning(
                "[social_poster] %s text exceeded %d chars, truncated", platform, char_limit
            )

        return text

    except Exception as e:
        logger.error("[social_poster] LLM generation failed for %s: %s", platform, e, exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Notification (Telegram + Discord via OpenClaw)
# ---------------------------------------------------------------------------


async def _notify(message: str) -> None:
    """Send social post notifications to Telegram and Discord."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            logger.info("[social_poster] Notifying: %s", message[:80])
            # Telegram — direct bot API
            await client.post(
                f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": _TELEGRAM_CHAT_ID, "text": message},
            )
            # Discord — via OpenClaw hooks
            await client.post(
                f"{_OPENCLAW_URL}/hooks/agent",
                headers={"Authorization": f"Bearer {_OPENCLAW_TOKEN}"},
                json={
                    "message": f"Post this to the #ops channel in Discord: {message}",
                    "channel": "discord",
                    "target": _DISCORD_OPS_CHANNEL,
                },
            )
    except Exception as e:
        logger.warning("[social_poster] Notification failed: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_social_posts(
    title: str,
    slug: str,
    excerpt: str,
    keywords: Optional[List[str]] = None,
    ollama: Optional[OllamaClient] = None,
) -> List[SocialPost]:
    """
    Generate social media posts for X/Twitter and LinkedIn.

    Args:
        title: Blog post title
        slug: URL slug for the blog post
        excerpt: Short description / excerpt of the post
        keywords: List of relevant keywords for hashtags
        ollama: Optional OllamaClient instance (for testing / reuse)

    Returns:
        List of SocialPost objects (one per platform)
    """
    keywords = keywords or []
    post_url = f"{SITE_BASE_URL}/posts/{slug}"
    posts: List[SocialPost] = []

    # --- Twitter ---
    twitter_prompt = _build_twitter_prompt(title, slug, excerpt, keywords)
    twitter_text = await _generate_social_text(
        twitter_prompt, TWITTER_CHAR_LIMIT, "twitter", ollama
    )
    if twitter_text:
        posts.append(SocialPost(platform="twitter", text=twitter_text, post_url=post_url))
        logger.info("[social_poster] Twitter post generated (%d chars)", len(twitter_text))
    else:
        logger.error("[social_poster] Twitter post generation failed — empty result")

    # --- LinkedIn ---
    linkedin_prompt = _build_linkedin_prompt(title, slug, excerpt, keywords)
    linkedin_text = await _generate_social_text(
        linkedin_prompt, LINKEDIN_CHAR_LIMIT, "linkedin", ollama
    )
    if linkedin_text:
        posts.append(SocialPost(platform="linkedin", text=linkedin_text, post_url=post_url))
        logger.info("[social_poster] LinkedIn post generated (%d chars)", len(linkedin_text))
    else:
        logger.error("[social_poster] LinkedIn post generation failed — empty result")

    return posts


async def generate_and_distribute_social_posts(
    title: str,
    slug: str,
    excerpt: str,
    keywords: Optional[List[str]] = None,
    ollama: Optional[OllamaClient] = None,
) -> List[SocialPost]:
    """
    End-to-end: generate social posts and distribute them via notifications.

    Call this from the task executor after a blog post is successfully published.

    Args:
        title: Blog post title
        slug: URL slug for the blog post
        excerpt: Short description / excerpt of the post
        keywords: List of relevant keywords for hashtags
        ollama: Optional OllamaClient instance (for testing / reuse)

    Returns:
        List of generated SocialPost objects
    """
    logger.info("[social_poster] Generating social posts for: %s", title)

    posts = await generate_social_posts(title, slug, excerpt, keywords, ollama)

    for post in posts:
        header = "Twitter/X" if post.platform == "twitter" else "LinkedIn"
        notification = (
            f"[Social Post Ready - {header}]\n\n"
            f"{post.text}\n\n"
            f"--- blog: {post.post_url} ---"
        )
        await _notify(notification)
        logger.info(
            "[social_poster] Distributed %s post to Telegram + Discord", post.platform
        )

    if not posts:
        await _notify(f"[Social Poster] Failed to generate social posts for: {title}")

    return posts
