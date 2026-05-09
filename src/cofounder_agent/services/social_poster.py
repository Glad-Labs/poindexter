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
from services.integrations import registry
from services.integrations.operator_notify import notify_operator
from services.llm_providers.dispatcher import resolve_tier_model
from services.logger_config import get_logger
from services.publishing_adapters_db import load_enabled_publishers
from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc
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
    return site_config.get("site_url", "https://localhost:3000")


def _openclaw_url() -> str:
    return site_config.get("openclaw_gateway_url", DEFAULT_OPENCLAW_URL)


async def _openclaw_token() -> str:
    # Legacy OpenClaw key — task_executor._notify_openclaw used to use
    # this too before it was migrated to the outbound dispatcher
    # framework. Social poster still goes through OpenClaw directly
    # pending its own migration.
    # is_secret=true row — must use async get_secret to read plaintext.
    # Sync .get() would return enc:v1:<ciphertext> for is_secret rows
    # (#325 bug class), and the bearer-token comparison upstream would
    # silently 401 every webhook fire.
    return await site_config.get_secret("openclaw_webhook_token", "hooks-gladlabs")


def _get_discord_ops_channel() -> str:
    """Read the discord channel ID at call time, not module import time.

    require() needs site_config to be loaded, which doesn't happen until the
    worker boots and connects to the DB. Calling it at import time breaks
    pytest collection in any environment without a DB.
    """
    return site_config.require("discord_ops_channel_id")


async def _resolve_social_model() -> str:
    """Bridge ``cost_tier='standard'`` -> concrete model id for social copy.

    Lane B batch 2 sweep migration. Order:

    1. ``resolve_tier_model(pool, 'standard')`` — operator-tuned tier mapping
       (``app_settings.cost_tier.standard.model``).
    2. ``app_settings[social_poster_fallback_model]`` — per-call-site backstop
       (seeded with ``ollama/llama3:latest`` by migration
       ``20260509_220000_seed_lane_b_misc_keys``).
    3. Operator-notify + raise — per ``feedback_no_silent_defaults.md``.

    Pool is read off the lifespan-bound ``site_config._pool`` attribute set
    by ``main.py``'s lifespan. When unavailable (tests, legacy paths) the
    tier resolution is skipped and the fallback path is taken directly.
    """
    pool = getattr(site_config, "_pool", None)
    if pool is not None:
        try:
            return await resolve_tier_model(pool, "standard")
        except (RuntimeError, ValueError, AttributeError) as exc:
            tier_exc: Exception | None = exc
        else:
            tier_exc = None
    else:
        tier_exc = RuntimeError("no asyncpg pool available")

    fallback = site_config.get("social_poster_fallback_model")
    if fallback:
        await notify_operator(
            f"social_poster: cost_tier='standard' resolution failed "
            f"({tier_exc}); falling back to social_poster_fallback_model={fallback!r}",
            critical=False,
            site_config=site_config,
        )
        return str(fallback)

    await notify_operator(
        f"social_poster: cost_tier='standard' has no model AND "
        f"social_poster_fallback_model is empty — copy generation skipped: {tier_exc}",
        critical=True,
        site_config=site_config,
    )
    raise RuntimeError(
        "social_poster: no model resolvable via tier or "
        "social_poster_fallback_model setting"
    ) from tier_exc


def _twitter_char_limit() -> int:
    # Defaults match current public platform limits; tune via app_settings
    # social_twitter_char_limit / social_linkedin_char_limit when platforms
    # change. (#198)
    return site_config.get_int("social_twitter_char_limit", 280)


def _linkedin_char_limit() -> int:
    return site_config.get_int("social_linkedin_char_limit", 700)


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
        f"You are a social media copywriter for a tech company called {site_config.get('company_name', '')}.\n"
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
        f"You are a social media copywriter for a tech company called {site_config.get('company_name', '')}.\n"
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
    # Cost-tier API (Lane B sweep). Operators tune the standard tier via
    # app_settings.cost_tier.standard.model — no code edit per niche. The
    # social_poster_fallback_model setting remains the per-call-site
    # backstop; _resolve_social_model fails loud via notify_operator if
    # both are missing, per feedback_no_silent_defaults.md.
    try:
        resolved = await _resolve_social_model()
    except RuntimeError as exc:
        logger.error(
            "[social_poster] could not resolve model for %s: %s",
            platform, exc,
        )
        if owns_client:
            with suppress(Exception):
                await client.close()
        return ""
    model = resolved.removeprefix("ollama/")  # OllamaClient expects bare model name

    try:
        result = await client.generate(
            prompt=prompt,
            model=model,
            temperature=0.8,
            max_tokens=site_config.get_int("social_poster_max_tokens", 300),
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
    *,
    pool=None,
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
        pool: Optional asyncpg pool. When provided, the publishing
            dispatcher loads enabled rows from ``publishing_adapters``
            and updates per-row counters after each call.

    Returns:
        List of generated SocialPost objects
    """
    logger.info("[social_poster] Generating social posts for: %s", title)

    posts = await generate_social_posts(title, slug, excerpt, keywords, ollama)

    # Determine which adapters are enabled
    enabled = set(
        site_config.get("social_distribution_platforms", "").split(",")
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
    adapter_results = await _distribute_to_adapters(posts, enabled, pool=pool)
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
    happy path AND on graceful-skip paths. A bug or a missing
    dependency can raise instead — this wrapper turns any exception
    into a well-formed failure dict so the rest of the social posting
    continues.

    ``NotImplementedError`` is treated as a "skipped" outcome rather
    than an "error" so future stub adapters (or operators flipping a
    flag on a platform we haven't shipped yet) don't trip the error
    counter.

    Metric: increments ``social_adapter_errors_total{platform=...}``
    when the adapter raises. ``social_adapter_posts_total`` is bumped
    with ``outcome={success,failure,error,skipped}`` so dashboards can
    tell "platform refused us" from "we crashed".

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
        # Defensive — adapters we ship today don't raise this, but a
        # future stub flipped on by mistake shouldn't take down the
        # rest of the distribution loop.
        logger.info("[social_poster] %s adapter is unavailable: %s", platform, e)
        _bump_metric("social_adapter_posts_total", platform=platform, outcome="skipped")
        return {"success": False, "post_id": None, "error": f"unavailable: {e}"}
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


async def _distribute_to_adapters(
    posts: list,
    enabled: set,
    *,
    pool=None,
) -> dict:
    """Post to each enabled publisher row from the ``publishing_adapters`` table.

    Row-driven dispatch (poindexter#112) — replaces the hardcoded
    ``if "bluesky" in enabled`` / ``if "mastodon" in enabled`` branches
    with a single loop over enabled rows from the declarative table.
    Adding a new platform = insert a row + register a handler under the
    ``publishing`` surface; no edit here.

    The ``enabled`` set is treated as **advisory** — when callers still
    pass ``enabled = {"bluesky"}``, it intersects with the DB-loaded
    rows so an operator who disabled the row in the DB sees no posts
    even if legacy code still lists the platform name in
    ``social_distribution_platforms``. When ``enabled`` is empty the
    advisory filter is skipped (DB rows are the single source of truth).

    site_config is passed through every dispatched call — that's the
    contract :mod:`services.social_adapters.bluesky` short-circuits on
    when missing (the 30-day distribution-dark bug fix from 2026-05-09).
    """
    results: dict[str, dict] = {}

    if not posts:
        return results

    twitter_post = next((p for p in posts if p.platform == "twitter"), None)
    linkedin_post = next((p for p in posts if p.platform == "linkedin"), None)
    generic_post = twitter_post or linkedin_post
    if not generic_post:
        return results

    text = generic_post.text
    url = generic_post.post_url

    db_rows = await load_enabled_publishers(pool)
    if not db_rows:
        logger.info("[social_poster] publishing dispatch: no enabled adapters")
        # No silent default — still warn about advisory platforms that
        # don't have a wired DB row, so a stale config doesn't hide.
        for name in sorted(enabled):
            logger.warning(
                "[social_poster] platform %r listed in social_distribution_platforms "
                "but no enabled publishing_adapters row exists — skipping", name,
            )
        return results

    rows_by_platform = {r.platform: r for r in db_rows}

    # Advisory filter: when the legacy ``enabled`` set is non-empty,
    # require a DB row's platform to also appear in it. When empty,
    # trust the DB rows alone.
    if enabled:
        for legacy_name in sorted(enabled):
            if legacy_name not in rows_by_platform:
                logger.warning(
                    "[social_poster] platform %r listed in "
                    "social_distribution_platforms but no enabled "
                    "publishing_adapters row exists — skipping",
                    legacy_name,
                )
        active = [r for r in db_rows if r.platform in enabled]
    else:
        active = list(db_rows)

    payload = {"text": text, "url": url}
    for row in active:
        result = await _safe_call_adapter(
            row.platform,
            lambda r=row: registry.dispatch(
                "publishing", r.handler_name, payload,
                site_config=site_config, row=r.as_dict(), pool=pool,
            ),
        )
        results[row.platform] = result
        await _record_publisher_outcome(pool, row, result)

    return results


async def _record_publisher_outcome(pool, row, result: dict) -> None:
    """Update per-row counters after a dispatch attempt.

    Mirrors what :func:`services.integrations.tap_runner._record_success`
    / ``_record_failure`` do for taps — counter writes live inline in
    the runner rather than a separate writer module so the dispatch
    site stays the single owner of state transitions.

    No-ops when ``pool`` is ``None`` (test harness, callers without a
    DB) — the caller has already logged the result by then.
    """
    if pool is None:
        return
    success = bool(result.get("success"))
    error = None if success else (result.get("error") or "unknown")
    status = "success" if success else "failed"
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE publishing_adapters
                   SET last_run_at = now(),
                       last_run_status = $2,
                       last_error = $3,
                       total_runs = total_runs + 1,
                       total_failures = total_failures + CASE WHEN $4 THEN 0 ELSE 1 END
                 WHERE id = $1
                """,
                row.id, status, error, success,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[social_poster] failed to record publisher outcome for %s: %s",
            row.name, exc,
        )
