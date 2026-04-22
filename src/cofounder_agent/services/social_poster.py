"""
Social Media Auto-Posting Service

Generates platform-specific social media posts (X/Twitter, LinkedIn) when a
blog post is published, using the local Ollama LLM for copy generation.

Posts are logged and forwarded to Telegram + Discord via the existing
notification pipeline.  Actual API posting to X/LinkedIn will be added later.

Usage from task_executor or any post-publish hook:

    from services.social_poster import generate_and_distribute_social_posts
    from services.site_config import site_config

    await generate_and_distribute_social_posts(
        title="Why Local LLMs Beat Cloud APIs",
        slug="why-local-llms-beat-cloud-apis",
        excerpt="A deep dive into cost, latency, and privacy ...",
        keywords=["LLM", "Ollama", "self-hosting"],
        site_config=site_config,
    )
"""

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from services.bootstrap_defaults import DEFAULT_OPENCLAW_URL
from services.logger_config import get_logger
from services.telegram_config import (
    get_telegram_bot_token,
    get_telegram_chat_id,
)

from .ollama_client import OllamaClient

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Platform character limits (with safety margin). These are hard defaults
# matching the current public limits; callers that want DB-tuned limits
# can override via ``site_config.get_int("social_twitter_char_limit", ...)``
# at the call site. Keeping the constants module-level preserves the
# exported contract (the test suite asserts TWITTER_CHAR_LIMIT == 280).
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


def _build_twitter_prompt(
    title: str, slug: str, excerpt: str, keywords: list[str],
    site_config: Any,
) -> str:
    base_url = site_config.get("site_url", "https://localhost:3000")
    post_url = f"{base_url}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    return (
        f"You are a social media copywriter for a tech company called {site_config.get('company_name', '')}.\n"
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


def _build_linkedin_prompt(
    title: str, slug: str, excerpt: str, keywords: list[str],
    site_config: Any,
) -> str:
    base_url = site_config.get("site_url", "https://localhost:3000")
    post_url = f"{base_url}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    return (
        f"You are a social media copywriter for a tech company called {site_config.get('company_name', '')}.\n"
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
    ollama: OllamaClient | None = None,
    site_config: Any | None = None,
) -> str:
    """Call the local Ollama LLM and return the generated text, trimmed to limit.

    site_config is optional for tests that exercise _generate_social_text
    directly — when omitted, the social model and max_tokens fall back to
    the hardcoded defaults that site_config.get_* would have returned for
    an unpopulated config.
    """
    # Track whether we own the client — only close clients we created here
    # so we don't shut down a pool the caller is still using.
    owns_client = ollama is None
    client = ollama or OllamaClient()
    if site_config is not None:
        model_raw = site_config.get("social_poster_model", "ollama/llama3:latest")
        max_tokens = site_config.get_int("social_poster_max_tokens", 300)
    else:
        model_raw = "ollama/llama3:latest"
        max_tokens = 300
    model = model_raw.removeprefix("ollama/")  # OllamaClient expects bare model name

    try:
        result = await client.generate(
            prompt=prompt,
            model=model,
            temperature=0.8,
            max_tokens=max_tokens,
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
    finally:
        if owns_client:
            with suppress(Exception):
                await client.close()


# ---------------------------------------------------------------------------
# Notification (Telegram + Discord via OpenClaw)
# ---------------------------------------------------------------------------


async def _notify(message: str, site_config: Any | None = None) -> None:
    """Send social post notifications to Telegram and Discord.

    site_config is optional for tests that call _notify directly with a
    mocked httpx client. When None, OpenClaw URL/token fall back to the
    defaults SiteConfig.get would have returned for an unpopulated
    config, and the Discord leg is skipped (``site_config.require()`` on
    the channel id can't run without a real config source). Telegram
    bot token + chat id also come from site_config; when unset they
    fall through as empty strings (matches legacy behavior where the
    module-level constants were "" without a populated config).
    """
    try:
        if site_config is not None:
            openclaw_url = site_config.get("openclaw_gateway_url", DEFAULT_OPENCLAW_URL)
            openclaw_token = site_config.get("openclaw_webhook_token", "hooks-gladlabs")
            discord_channel: str | None = site_config.require("discord_ops_channel_id")
            telegram_bot_token = get_telegram_bot_token(site_config)
            telegram_chat_id = get_telegram_chat_id(site_config)
        else:
            openclaw_url = DEFAULT_OPENCLAW_URL
            openclaw_token = "hooks-gladlabs"
            discord_channel = None
            telegram_bot_token = ""
            telegram_chat_id = ""

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0)
        ) as client:
            logger.info("[social_poster] Notifying: %s", message[:80])
            # Telegram — direct bot API
            await client.post(
                f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage",
                json={"chat_id": telegram_chat_id, "text": message},
                timeout=10,
            )
            # Discord — via OpenClaw hooks
            if discord_channel is not None:
                await client.post(
                    f"{openclaw_url}/hooks/agent",
                    headers={"Authorization": f"Bearer {openclaw_token}"},
                    json={
                        "message": f"Post this to the #ops channel in Discord: {message}",
                        "channel": "discord",
                        "target": discord_channel,
                    },
                    timeout=10,
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
    keywords: list[str] | None = None,
    ollama: OllamaClient | None = None,
    site_config: Any | None = None,
) -> list[SocialPost]:
    """
    Generate social media posts for X/Twitter and LinkedIn.

    Args:
        title: Blog post title
        slug: URL slug for the blog post
        excerpt: Short description / excerpt of the post
        keywords: List of relevant keywords for hashtags
        ollama: Optional OllamaClient instance (for testing / reuse)
        site_config: SiteConfig instance (DI — Phase H). Optional for
            test back-compat; when omitted, prompts use an empty company
            name and the default site_url — matching what the legacy
            module-level path produced against an unpopulated singleton.

    Returns:
        List of SocialPost objects (one per platform)
    """
    keywords = keywords or []
    base_url = (
        site_config.get("site_url", "https://localhost:3000")
        if site_config is not None
        else "https://localhost:3000"
    )
    post_url = f"{base_url}/posts/{slug}"
    posts: list[SocialPost] = []

    # Build prompts. When site_config is missing (test path), use a
    # lightweight shim so the prompt builders keep a single signature.
    sc_for_prompts = site_config if site_config is not None else _EmptySiteConfig()

    # --- Twitter ---
    twitter_prompt = _build_twitter_prompt(title, slug, excerpt, keywords, sc_for_prompts)
    twitter_text = await _generate_social_text(
        twitter_prompt, TWITTER_CHAR_LIMIT, "twitter", ollama, site_config,
    )
    if twitter_text:
        posts.append(SocialPost(platform="twitter", text=twitter_text, post_url=post_url))
        logger.info("[social_poster] Twitter post generated (%d chars)", len(twitter_text))
    else:
        logger.error("[social_poster] Twitter post generation failed — empty result")

    # --- LinkedIn ---
    linkedin_prompt = _build_linkedin_prompt(title, slug, excerpt, keywords, sc_for_prompts)
    linkedin_text = await _generate_social_text(
        linkedin_prompt, LINKEDIN_CHAR_LIMIT, "linkedin", ollama, site_config,
    )
    if linkedin_text:
        posts.append(SocialPost(platform="linkedin", text=linkedin_text, post_url=post_url))
        logger.info("[social_poster] LinkedIn post generated (%d chars)", len(linkedin_text))
    else:
        logger.error("[social_poster] LinkedIn post generation failed — empty result")

    return posts


class _EmptySiteConfig:
    """Tiny stand-in for SiteConfig used only when callers (and tests)
    don't pass one. Returns empty strings / passed defaults so the
    prompt builders keep a single parameterized signature."""

    def get(self, key: str, default: str = "") -> str:  # noqa: ARG002
        return default

    def get_int(self, key: str, default: int = 0) -> int:  # noqa: ARG002
        return default

    def require(self, key: str) -> str:
        raise RuntimeError(f"Required setting '{key}' has no site_config source")


async def generate_and_distribute_social_posts(
    title: str,
    slug: str,
    excerpt: str,
    keywords: list[str] | None = None,
    ollama: OllamaClient | None = None,
    site_config: Any | None = None,
) -> list[SocialPost]:
    """
    End-to-end: generate social posts and distribute them via notifications.

    Call this from the task executor after a blog post is successfully published.

    Args:
        title: Blog post title
        slug: URL slug for the blog post
        excerpt: Short description / excerpt of the post
        keywords: List of relevant keywords for hashtags
        ollama: Optional OllamaClient instance (for testing / reuse)
        site_config: SiteConfig instance (DI — Phase H). Optional for
            test back-compat.

    Returns:
        List of generated SocialPost objects
    """
    logger.info("[social_poster] Generating social posts for: %s", title)

    posts = await generate_social_posts(
        title, slug, excerpt, keywords, ollama, site_config,
    )

    # Determine which adapters are enabled
    enabled_raw = (
        site_config.get("social_distribution_platforms", "")
        if site_config is not None
        else ""
    )
    enabled = set(enabled_raw.split(",")) - {""}

    for post in posts:
        header = "Twitter/X" if post.platform == "twitter" else "LinkedIn"
        notification = (
            f"[Social Post Ready - {header}]\n\n"
            f"{post.text}\n\n"
            f"--- blog: {post.post_url} ---"
        )
        await _notify(notification, site_config)
        logger.info(
            "[social_poster] Distributed %s post to Telegram + Discord", post.platform
        )

    # Post to enabled social platforms via adapters
    adapter_results = await _distribute_to_adapters(posts, enabled)
    for platform, result in adapter_results.items():
        if result.get("success"):
            logger.info("[social_poster] Posted to %s: %s", platform, result.get("post_id", ""))
        else:
            logger.warning("[social_poster] %s failed: %s", platform, result.get("error", "unknown"))

    if not posts:
        await _notify(
            f"[Social Poster] Failed to generate social posts for: {title}", site_config,
        )

    return posts


async def _safe_call_adapter(platform: str, coro_factory) -> dict:
    """Run one adapter call and never let it crash the distribution loop.

    Adapters promise a ``{"success", "post_id", "error"}`` dict on the
    happy path AND on graceful-skip paths. But a bug, a missing
    dependency, or a stubbed adapter (LinkedIn/Reddit/YouTube — see
    GH-40) can raise instead. This wrapper turns any exception —
    including ``NotImplementedError`` from the stubs — into a
    well-formed failure dict so the rest of the social posting
    continues.

    Metric: increments ``social_adapter_errors_total{platform=...}``
    when the adapter raises. ``social_adapter_posts_total`` is bumped
    with ``outcome={success,failure,error}`` so dashboards can tell
    "platform refused us" from "we crashed".

    GH-36.
    """
    try:
        result = await coro_factory()
        # Adapters that return a dict with success=False are a graceful
        # skip (missing creds, platform rejected the post) — not a
        # crash. Distinguish in metrics.
        if isinstance(result, dict):
            outcome = "success" if result.get("success") else "failure"
        else:
            outcome = "failure"
            result = {"success": False, "post_id": None, "error": "adapter returned non-dict"}
        _bump_metric("social_adapter_posts_total", platform=platform, outcome=outcome)
        return result
    except NotImplementedError as e:
        # Known-stub path (GH-40). Log INFO and keep going — the other
        # platforms shouldn't pay for LinkedIn/Reddit/YouTube being off.
        logger.info("[social_poster] %s adapter is a stub: %s", platform, e)
        _bump_metric("social_adapter_posts_total", platform=platform, outcome="skipped")
        return {"success": False, "post_id": None, "error": f"stub: {e}"}
    except Exception as e:  # noqa: BLE001 — adapter boundary, never crash the loop
        logger.exception("[social_poster] %s adapter crashed: %s", platform, e)
        _bump_metric("social_adapter_errors_total", platform=platform)
        _bump_metric("social_adapter_posts_total", platform=platform, outcome="error")
        return {"success": False, "post_id": None, "error": str(e)}


def _bump_metric(name: str, **labels: str) -> None:
    """Fire-and-forget Prometheus counter increment. Never raises.

    Uses ``prometheus_client`` registry directly so the metric is picked
    up by ``metrics_exporter`` on the next ``/metrics`` scrape. Metrics
    are strictly best-effort — if prometheus_client isn't available or
    the counter definition fails, posting still proceeds.

    Counters used:

    * ``poindexter_social_adapter_posts_total{platform,outcome}`` —
      one increment per adapter call. ``outcome`` is
      ``success|failure|error|skipped``.
    * ``poindexter_social_adapter_errors_total{platform}`` — bumped
      only when an adapter raises an unexpected exception.
    """
    try:
        from prometheus_client import Counter

        # Map our two metric names to singleton Counters (lazy).
        counter = _COUNTERS.get(name)
        if counter is None:
            if name == "social_adapter_posts_total":
                counter = Counter(
                    "poindexter_social_adapter_posts_total",
                    "Social adapter posting attempts (GH-36)",
                    ["platform", "outcome"],
                )
            elif name == "social_adapter_errors_total":
                counter = Counter(
                    "poindexter_social_adapter_errors_total",
                    "Social adapter unexpected exceptions (GH-36)",
                    ["platform"],
                )
            else:
                return
            _COUNTERS[name] = counter

        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:
        pass  # metrics are best-effort — never break posting


# Counter singletons — prometheus_client refuses duplicate registration.
_COUNTERS: dict[str, object] = {}


async def _distribute_to_adapters(posts: list, enabled: set) -> dict:
    """Post to each enabled social platform adapter.

    Order of operations (GH-36):

    1. Pick the best text for the generic adapters (Twitter copy by
       default — it's the shortest, so fits everywhere).
    2. For each enabled platform, call the adapter through
       :func:`_safe_call_adapter` — that wrapper guarantees a single
       failing adapter never takes down the distribution job.
    3. Stub adapters (LinkedIn/Reddit/YouTube) raise
       ``NotImplementedError``; the wrapper logs INFO + "skipped"
       metric and moves on.
    """
    results: dict[str, dict] = {}

    if not posts or not enabled:
        return results

    twitter_post = next((p for p in posts if p.platform == "twitter"), None)
    linkedin_post = next((p for p in posts if p.platform == "linkedin"), None)
    generic_post = twitter_post or linkedin_post
    if not generic_post:
        return results

    text = generic_post.text
    url = generic_post.post_url

    if "bluesky" in enabled:
        from services.social_adapters.bluesky import post_to_bluesky
        results["bluesky"] = await _safe_call_adapter(
            "bluesky", lambda: post_to_bluesky(text, url)
        )

    if "mastodon" in enabled:
        from services.social_adapters.mastodon import post_to_mastodon
        results["mastodon"] = await _safe_call_adapter(
            "mastodon", lambda: post_to_mastodon(text, url)
        )

    if "linkedin" in enabled:
        # Stub per GH-40 — kept so operators can still flip the flag on
        # once they wire up OAuth; wrapper catches NotImplementedError.
        from services.social_adapters.linkedin import post_to_linkedin
        ln_text = linkedin_post.text if linkedin_post else text
        results["linkedin"] = await _safe_call_adapter(
            "linkedin", lambda: post_to_linkedin(ln_text, url)
        )

    if "reddit" in enabled:
        from services.social_adapters.reddit import post_to_reddit
        title = generic_post.text.split("\n")[0][:300] if generic_post else ""
        results["reddit"] = await _safe_call_adapter(
            "reddit", lambda: post_to_reddit(title, url)
        )

    return results
