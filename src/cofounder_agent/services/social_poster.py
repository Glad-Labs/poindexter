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

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from services.bootstrap_defaults import DEFAULT_OPENCLAW_URL
from services.logger_config import get_logger
import services.site_config as _site_config_mod
_sc = _site_config_mod.site_config
from services.telegram_config import get_telegram_bot_token, get_telegram_chat_id

from .ollama_client import OllamaClient

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Runtime config accessors
# ---------------------------------------------------------------------------
#
# These were module-level constants until 2026-05-01 (Glad-Labs/poindexter#185
# docs review flagged it). The site_config singleton is reloaded every minute
# by the `reload_site_config` plugin job, but module-level captures bypass
# that — operators who tuned `social_twitter_char_limit` etc. wouldn't see
# the change until the worker process restarted. Per the "DB-first runtime-
# tunable" principle in CLAUDE.md, every consumer reads at call time.
#
# Pattern: thin helper functions per setting, read on every invocation.
# Cost is one in-memory dict lookup per call — negligible vs the LLM call
# the value gets passed into.


def _site_base_url() -> str:
    return _sc.get("site_url", "https://localhost:3000")


def _openclaw_url() -> str:
    return _sc.get("openclaw_gateway_url", DEFAULT_OPENCLAW_URL)


async def _openclaw_token() -> str:
    # Legacy OpenClaw key — task_executor._notify_openclaw used to use
    # this too before it was migrated to the outbound dispatcher
    # framework. Social poster still goes through OpenClaw directly
    # pending its own migration.
    # is_secret=true row — must use async get_secret to read plaintext.
    # Sync .get() would return enc:v1:<ciphertext> for is_secret rows
    # (#325 bug class), and the bearer-token comparison upstream would
    # silently 401 every webhook fire.
    return await _sc.get_secret("openclaw_webhook_token", "hooks-gladlabs")


def _get_discord_ops_channel() -> str:
    """Read the discord channel ID at call time, not module import time.

    require() needs site_config to be loaded, which doesn't happen until the
    worker boots and connects to the DB. Calling it at import time breaks
    pytest collection in any environment without a DB.
    """
    return _sc.require("discord_ops_channel_id")


def _social_model() -> str:
    # Default is a fast small model — social copy is a simple task.
    return _sc.get("social_poster_model", "ollama/llama3:latest")


def _twitter_char_limit() -> int:
    # Defaults match current public platform limits; tune via app_settings
    # social_twitter_char_limit / social_linkedin_char_limit when platforms
    # change. (#198)
    return _sc.get_int("social_twitter_char_limit", 280)


def _linkedin_char_limit() -> int:
    return _sc.get_int("social_linkedin_char_limit", 700)


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


def _build_twitter_prompt(title: str, slug: str, excerpt: str, keywords: list[str]) -> str:
    post_url = f"{_site_base_url()}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    return (
        f"You are a social media copywriter for a tech company called {_sc.get('company_name', '')}.\n"
        "Write a single tweet to promote the following blog post.\n\n"
        "Rules:\n"
        f"- The tweet MUST be under {_twitter_char_limit()} characters including the URL and hashtags.\n"
        "- Include the exact URL below — do not shorten or modify it.\n"
        "- Include 2-3 relevant hashtags from the keywords provided.\n"
        "- Be punchy and engaging. No generic filler.\n"
        "- Output ONLY the tweet text. No quotes, labels, or commentary.\n\n"
        f"Blog title: {title}\n"
        f"Excerpt: {excerpt}\n"
        f"URL: {post_url}\n"
        f"Suggested hashtags: {hashtags}\n"
    )


def _build_linkedin_prompt(title: str, slug: str, excerpt: str, keywords: list[str]) -> str:
    post_url = f"{_site_base_url()}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    return (
        f"You are a social media copywriter for a tech company called {_sc.get('company_name', '')}.\n"
        "Write a LinkedIn post to promote the following blog article.\n\n"
        "Rules:\n"
        f"- The post MUST be under {_linkedin_char_limit()} characters including the URL and hashtags.\n"
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
) -> str:
    """Call the local Ollama LLM and return the generated text, trimmed to limit."""
    # Track whether we own the client — only close clients we created here
    # so we don't shut down a pool the caller is still using.
    owns_client = ollama is None
    client = ollama or OllamaClient()
    model = _social_model().removeprefix("ollama/")  # OllamaClient expects bare model name

    try:
        result = await client.generate(
            prompt=prompt,
            model=model,
            temperature=0.8,
            max_tokens=_sc.get_int("social_poster_max_tokens", 300),
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


async def _notify(message: str) -> None:
    """Send social post notifications to Telegram and Discord."""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0)
        ) as client:
            logger.info("[social_poster] Notifying: %s", message[:80])
            # Telegram — direct bot API. Token is is_secret=true; must be
            # fetched via get_telegram_bot_token() (async, decrypted).
            _tg_token = await get_telegram_bot_token()
            _tg_chat = get_telegram_chat_id()
            if _tg_token and _tg_chat:
                await client.post(
                    f"https://api.telegram.org/bot{_tg_token}/sendMessage",
                    json={"chat_id": _tg_chat, "text": message},
                    timeout=10,
                )
            else:
                logger.warning(
                    "[social_poster] Telegram token or chat_id not set — skipping notification"
                )
            # Discord — via OpenClaw hooks
            await client.post(
                f"{_openclaw_url()}/hooks/agent",
                headers={"Authorization": f"Bearer {await _openclaw_token()}"},
                json={
                    "message": f"Post this to the #ops channel in Discord: {message}",
                    "channel": "discord",
                    "target": _get_discord_ops_channel(),
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
) -> list[SocialPost]:
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
    post_url = f"{_site_base_url()}/posts/{slug}"
    posts: list[SocialPost] = []

    # --- Twitter ---
    twitter_prompt = _build_twitter_prompt(title, slug, excerpt, keywords)
    twitter_text = await _generate_social_text(
        twitter_prompt, _twitter_char_limit(), "twitter", ollama
    )
    if twitter_text:
        posts.append(SocialPost(platform="twitter", text=twitter_text, post_url=post_url))
        logger.info("[social_poster] Twitter post generated (%d chars)", len(twitter_text))
    else:
        logger.error("[social_poster] Twitter post generation failed — empty result")

    # --- LinkedIn ---
    linkedin_prompt = _build_linkedin_prompt(title, slug, excerpt, keywords)
    linkedin_text = await _generate_social_text(
        linkedin_prompt, _linkedin_char_limit(), "linkedin", ollama
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
    keywords: list[str] | None = None,
    ollama: OllamaClient | None = None,
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

    Returns:
        List of generated SocialPost objects
    """
    logger.info("[social_poster] Generating social posts for: %s", title)

    posts = await generate_social_posts(title, slug, excerpt, keywords, ollama)

    # Determine which adapters are enabled
    enabled = set(
        _sc.get("social_distribution_platforms", "").split(",")
    ) - {""}

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

    # Post to enabled social platforms via adapters
    adapter_results = await _distribute_to_adapters(posts, enabled)
    for platform, result in adapter_results.items():
        if result.get("success"):
            logger.info("[social_poster] Posted to %s: %s", platform, result.get("post_id", ""))
        else:
            logger.warning("[social_poster] %s failed: %s", platform, result.get("error", "unknown"))

    if not posts:
        await _notify(f"[Social Poster] Failed to generate social posts for: {title}")

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
