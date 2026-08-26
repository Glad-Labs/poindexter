"""
Social Media Copy Generation Service

Generates platform-specific social media copy (X/Twitter, LinkedIn, and the
X-style short-form variants Bluesky + Mastodon) for a published blog post using
the local Ollama LLM.

This module is a pure **copy generator**. Distribution is owned elsewhere:
``generate_social_posts`` returns ``SocialPost`` objects that the
``social.generate_drafts`` pipeline atom turns into ``social_post_drafts`` rows,
which are then approved and pushed to each platform through Postiz
(``services.social_drafts`` / ``services.integrations.postiz_client``).

    from services.social_poster import generate_social_posts

    posts = await generate_social_posts(
        title="Why Local LLMs Beat Cloud APIs",
        slug="why-local-llms-beat-cloud-apis",
        excerpt="A deep dive into cost, latency, and privacy ...",
        keywords=["LLM", "Ollama", "self-hosting"],
        site_config=site_config,
    )

The legacy direct ``social_adapters`` distribution path
(``generate_and_distribute_social_posts`` + the per-platform adapter dispatch +
Telegram/Discord "social post ready" notifications) was retired 2026-06-29 when
Postiz became the distribution mechanism.
"""

import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.integrations.operator_notify import notify_operator
from services.llm_providers.dispatcher import dispatch_complete
from services.llm_providers.thinking_models import strip_think_blocks
from services.logger_config import get_logger
from services.site_config import SiteConfig

# SiteConfig DI (#272 Phase-2e): the module-level ``site_config`` global +
# ``set_site_config`` setter were removed. Injection is mandatory — the
# public entry (``generate_social_posts``) takes a required ``site_config=``
# kwarg and threads it into every internal helper. Callers pass the run-bound
# instance (the ``social.generate_drafts`` atom builds one from the container).
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


def _site_base_url(*, site_config: SiteConfig) -> str:
    return site_config.get("site_url", "https://localhost:3000")


async def _resolve_social_model(*, site_config: SiteConfig) -> str:
    """Resolve the social-copy model from ``social_poster_fallback_model``.

    Reads the dedicated ``app_settings[social_poster_fallback_model]`` pin and
    fails loud (notify + raise) when unset, per ``feedback_no_silent_defaults.md``.
    The ``cost_tier.*`` tier fallback was removed.
    """
    _sc = site_config
    model = _sc.get("social_poster_fallback_model")
    if model:
        return str(model)

    await notify_operator(
        "social_poster: social_poster_fallback_model is empty — copy "
        "generation skipped (set social_poster_fallback_model)",
        critical=True,
        site_config=_sc,
    )
    raise RuntimeError(
        "social_poster: no model resolvable — set social_poster_fallback_model"
    )


def _twitter_char_limit(*, site_config: SiteConfig) -> int:
    # Defaults match current public platform limits; tune via app_settings
    # social_twitter_char_limit / social_linkedin_char_limit when platforms
    # change. (#198)
    return site_config.get_int("social_twitter_char_limit", 280)


def _linkedin_char_limit(*, site_config: SiteConfig) -> int:
    return site_config.get_int("social_linkedin_char_limit", 700)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SocialPost:
    """A generated social media post for a specific platform."""

    platform: str  # "twitter" | "linkedin" | "bluesky" | "mastodon"
    text: str
    post_url: str  # the blog URL being promoted
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    posted: bool = False  # flips to True once a Postiz draft for it is posted


# ---------------------------------------------------------------------------
# LLM generation
# ---------------------------------------------------------------------------


_TWITTER_PROMPT_FALLBACK = (
    "You write the social account of {company_name} — an engineer-run publication sharing what it learns in public.\n"
    "Write a single tweet promoting the blog post below to technical readers.\n\n"
    "Method:\n"
    "1. Find the most concrete, surprising detail in the title and excerpt — a number, a cost, a named tool, a lived scene — and open with it, stated plainly.\n"
    "2. Add one sentence on what the article shows or why that detail matters.\n"
    "3. Close with the exact URL.\n\n"
    "Rules:\n"
    "- The tweet MUST be under {char_limit} characters including the URL and any hashtags. The URL alone is {url_chars} characters, so everything you write besides it must fit in {prose_budget}.\n"
    "- Include the exact URL below — do not shorten or modify it.\n"
    "- Specifics carry the post: use the article's own numbers, names, and scenes rather than adjectives, questions, or hype.\n"
    "- Vary the opener across posts — a detail, a claim, or a scene. Stock openers (“Stop guessing…”, “Ever wondered…?”) read as spam.\n"
    "- Hashtags: none by default; at most one from the suggested list, only when it reads naturally.\n"
    "- At most one emoji, only where it adds meaning; open with words.\n"
    "- Output ONLY the tweet text. No quotes, labels, or commentary.\n\n"
    "Blog title: {title}\n"
    "Excerpt: {excerpt}\n"
    "URL: {post_url}\n"
    "Suggested hashtags: {hashtags}\n"
)

_LINKEDIN_PROMPT_FALLBACK = (
    "You write the company page of {company_name} — an engineer-run publication sharing what it learns in public.\n"
    "Write a LinkedIn post promoting the blog article below to a technical professional audience.\n\n"
    "Method:\n"
    "1. Open with the article's most concrete, surprising detail — a number, a cost, a named tool, a lived scene — stated plainly in one line.\n"
    "2. Follow with two or three short sentences on what the article shows and who it helps.\n"
    "3. Invite the click in one plain sentence, then the exact URL.\n\n"
    "Rules:\n"
    "- The post MUST be under {char_limit} characters including the URL and any hashtags. The URL alone is {url_chars} characters, so everything you write besides it must fit in {prose_budget}.\n"
    "- Include the exact URL below — do not shorten or modify it.\n"
    "- Specifics carry the post: use the article's own numbers, names, and scenes rather than adjectives, questions, or hype.\n"
    "- Hashtags: at most two from the suggested list, placed at the very end, only when they read naturally.\n"
    "- Output ONLY the post text. No quotes, labels, or commentary.\n\n"
    "Blog title: {title}\n"
    "Excerpt: {excerpt}\n"
    "URL: {post_url}\n"
    "Suggested hashtags: {hashtags}\n"
)


def _resolve_social_prompt(key: str, *, fallback: str, **kwargs: Any) -> str:
    """Fetch a social-media prompt via UnifiedPromptManager with inline
    fallback. Mirrors the standard resolve-then-fallback prompt pattern per
    ``feedback_prompts_must_be_db_configurable``.
    """
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(key, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[social_poster] prompt_manager lookup for %r failed (%s) — "
            "using inline fallback",
            key, exc,
        )
        return fallback.format(**kwargs)


def _build_twitter_prompt(
    title: str,
    slug: str,
    excerpt: str,
    keywords: list[str],
    *,
    site_config: SiteConfig,
) -> str:
    _sc = site_config
    post_url = f"{_site_base_url(site_config=_sc)}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    char_limit = _twitter_char_limit(site_config=_sc)
    return _resolve_social_prompt(
        "social.twitter_promote",
        fallback=_TWITTER_PROMPT_FALLBACK,
        company_name=_sc.get("company_name", ""),
        char_limit=char_limit,
        # Slugged post URLs run ~90 chars — a third of the tweet budget — and
        # models are bad at deriving that subtraction themselves, so the
        # prompt hands them the prose budget _polish_social_copy will
        # actually enforce (limit − URL − 1 joining space). Before this,
        # roughly half of all drafts overran and got trimmed.
        url_chars=len(post_url),
        prose_budget=max(char_limit - len(post_url) - 1, 0),
        title=title,
        excerpt=excerpt,
        post_url=post_url,
        hashtags=hashtags,
    )


def _build_linkedin_prompt(
    title: str,
    slug: str,
    excerpt: str,
    keywords: list[str],
    *,
    site_config: SiteConfig,
) -> str:
    _sc = site_config
    post_url = f"{_site_base_url(site_config=_sc)}/posts/{slug}"
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    char_limit = _linkedin_char_limit(site_config=_sc)
    return _resolve_social_prompt(
        "social.linkedin_promote",
        fallback=_LINKEDIN_PROMPT_FALLBACK,
        company_name=_sc.get("company_name", ""),
        char_limit=char_limit,
        # Same prose-budget arithmetic as the twitter prompt — see there.
        url_chars=len(post_url),
        prose_budget=max(char_limit - len(post_url) - 1, 0),
        title=title,
        excerpt=excerpt,
        post_url=post_url,
        hashtags=hashtags,
    )


# ---------------------------------------------------------------------------
# Deterministic copy repair
# ---------------------------------------------------------------------------
#
# The prompt asks the model for clean, under-limit copy that carries the post
# URL, but a weak model can still ignore that: it trails off with a dangling
# ellipsis (the artifact the 3B fallback produced before the gemma-4-31B bump)
# or drops the link. These helpers are the deterministic net applied to every
# generated draft so neither reaches the operator's pre-approval preview.

# A run of >=2 ASCII dots or the unicode ellipsis (U+2026) left dangling at
# the end of the copy — filler the model appends when it runs out of thought.
_TRAILING_ELLIPSIS_RE = re.compile(r"\s*(?:\.{2,}|…)+\s*$")

# A sentence terminator (. ! ?), optionally wrapped by a closing quote or
# paren, followed by whitespace or end-of-text. The whitespace lookahead is
# what keeps decimals ("1.4 shipped") and dotted names (".map files",
# "Bun.WebView") from counting as sentence ends: their period has a non-space
# character on at least one side.
_SENTENCE_END_RE = re.compile(r"[.!?][\"'）)’”]*(?=\s|$)")


def _strip_trailing_ellipsis(text: str) -> str:
    """Drop a dangling ellipsis / dot-run trail-off from the end of *text*."""
    return _TRAILING_ELLIPSIS_RE.sub("", text).rstrip()


def _fit_prose(text: str, limit: int) -> str:
    """Trim *text* to <= *limit* chars, preferring a sentence boundary.

    An over-limit draft is cut at the last sentence terminator that fits, so
    the survivor reads as finished copy — a complete short post beats a longer
    fragment ("…larger memories automatically create <URL>" is the 2026-08-26
    truncated-drafts report this exists to prevent). Only when no whole
    sentence fits (one long unbroken sentence) does it fall back to the last
    whole word, re-stripping any ellipsis the cut exposed so shortening copy
    never manufactures a trail-off.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    sentence_end = 0
    for m in _SENTENCE_END_RE.finditer(text):
        if m.end() > limit:
            break
        sentence_end = m.end()
    if sentence_end:
        return _strip_trailing_ellipsis(text[:sentence_end].rstrip())
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return _strip_trailing_ellipsis(cut)


def _polish_social_copy(text: str, *, post_url: str, char_limit: int) -> str:
    """Repair one generated social draft deterministically.

    1. Strip a trailing ellipsis trail-off (``…`` / ``..`` / ``...``) — it reads
       as broken, half-finished copy.
    2. Guarantee an absolute post URL is present so the operator's draft preview
       is complete and the promo carries its link. ``approve_draft`` →
       ``_ensure_post_url`` is still the final backstop (it rewrites to the real
       canonical slug at post time, correcting any predicted-slug drift), but
       appending here keeps the pre-approval preview honest and protects the
       link from step 3.
    3. Fit to ``char_limit`` by trimming PROSE, never the URL — a long draft
       loses filler, not its link. The trim prefers a sentence boundary
       (``_fit_prose``), so an overrunning draft loses its last sentence
       whole rather than surfacing a mid-clause fragment.

    ``post_url`` is honored only when absolute (``http(s)://``); an empty or
    relative value (``site_url`` unset -> ``/posts/slug``) is treated as
    "no URL" so a broken link is never injected. The model is handed the exact
    URL in the prompt, so an exact-substring presence check aligns with what a
    compliant model emits; a paraphrased duplicate is deduped by the canonical
    rewrite in ``_ensure_post_url`` at approve.
    """
    text = _strip_trailing_ellipsis(text.strip())
    if not text:
        return text

    url = post_url.strip()
    if url.startswith(("http://", "https://")):
        # Protect the link on BOTH paths. The model is handed the exact URL
        # and usually emits it inline — often with hashtags AFTER it — so the
        # old "URL already inline → just _fit_prose(text, limit)" branch cut
        # from the end and dropped the link itself whenever prose+URL+tags
        # overran the limit (Cosine-Similarity draft, 2026-08-16: 188 chars
        # of prose + 1 + a 91-char URL = exactly 280, the word-boundary drop
        # took the URL, and the operator saw a mid-sentence fragment with no
        # link). Lift the URL out wherever it sits, fit the remaining prose
        # to the reserved budget, and re-append the link LAST so it can never
        # be truncated or dropped.
        prose = " ".join(text.replace(url, " ").split())
        prose = _strip_trailing_ellipsis(prose)
        prose = _fit_prose(prose, char_limit - len(url) - 1)
        text = f"{prose} {url}".strip() if prose else url
    else:
        # URL unconfigured/relative — just enforce the platform limit.
        text = _fit_prose(text, char_limit)
    return text


async def _generate_social_text(
    prompt: str,
    char_limit: int,
    platform: str,
    ollama: OllamaClient | None = None,
    *,
    site_config: SiteConfig,
    post_url: str = "",
) -> str:
    """Call the LLM and return the generated text, trimmed to limit.

    Production path (pool available on site_config): routes through
    ``dispatch_complete`` for cost tracking, Langfuse traces, and
    provider-swappability.  Test / bootstrap fallback (no pool): delegates
    to the supplied ``OllamaClient`` instance (or creates a transient one)
    so the existing test suite continues to work without a live DB.
    """
    _sc = site_config
    # Per-step pin. Operators tune app_settings.social_poster_fallback_model
    # — no code edit per niche. _resolve_social_model reads it directly and
    # fails loud via notify_operator when unset, per
    # feedback_no_silent_defaults.md.
    try:
        resolved = await _resolve_social_model(site_config=_sc)
    except RuntimeError as exc:
        logger.error(
            "[social_poster] could not resolve model for %s: %s",
            platform, exc,
        )
        return ""
    model = resolved.removeprefix("ollama/")  # bare model name for both paths

    pool = getattr(_sc, "_pool", None)

    try:
        if pool is not None and ollama is None:
            # Production path — dispatch through the configured LLM provider.
            # Social copy is short — disable reasoning phase so a thinking
            # model emits the post directly rather than burning its whole
            # token budget on analysis (think=False propagated via kwargs).
            max_tokens = _sc.get_int("social_poster_max_tokens", 300)
            messages = [{"role": "user", "content": prompt}]
            completion = await dispatch_complete(
                pool=pool,
                messages=messages,
                model=model,
                tier="standard",
                phase="social_poster",
                think=False,
                options={"num_predict": max_tokens, "temperature": 0.8},
            )
            text = (getattr(completion, "text", "") or "").strip()
        else:
            # Test / bootstrap fallback — delegate to OllamaClient.
            owns_client = ollama is None
            client = ollama or OllamaClient()
            try:
                result = await client.generate(
                    prompt=prompt,
                    model=model,
                    temperature=0.8,
                    max_tokens=_sc.get_int("social_poster_max_tokens", 300),
                    # Social copy is short — disable the model's reasoning phase. A
                    # thinking model (e.g. the 'standard' tier glm-4.7) otherwise
                    # spends the whole token budget thinking, never emits the post,
                    # and OllamaClient salvages the raw thinking trace (analysis that
                    # reads like QA results) as the "draft". think=False makes the
                    # model emit the post directly.
                    think=False,
                )
                text = result.get("text", "").strip()
            finally:
                if owns_client:
                    with suppress(Exception):  # silent-ok: best-effort client close in finally
                        await client.close()

        # Defense in depth: strip any residual <think>...</think> reasoning
        # block in case a model emits one inline despite think=False — the
        # social draft must never surface the model's analysis.
        text = strip_think_blocks(text).strip()

        # Strip wrapping quotes if the LLM added them
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()

        if len(text) > char_limit:
            logger.warning(
                "[social_poster] %s text exceeded %d chars, trimming", platform, char_limit
            )

        # Deterministic copy repair: strip a trailing ellipsis trail-off,
        # guarantee the post URL is present, and fit to the platform limit by
        # trimming prose (never the link). Fixes the "…"-trail-off and the
        # dropped/mangled URL a weak model can still emit despite the prompt.
        return _polish_social_copy(text, post_url=post_url, char_limit=char_limit)

    except Exception as e:
        logger.error("[social_poster] LLM generation failed for %s: %s", platform, e, exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_social_posts(
    title: str,
    slug: str,
    excerpt: str,
    keywords: list[str] | None = None,
    ollama: OllamaClient | None = None,
    *,
    site_config: SiteConfig,
) -> list[SocialPost]:
    """
    Generate social media copy for X/Twitter, LinkedIn, Bluesky, and Mastodon.

    Args:
        title: Blog post title
        slug: URL slug for the blog post
        excerpt: Short description / excerpt of the post
        keywords: List of relevant keywords for hashtags
        ollama: Optional OllamaClient instance (for testing / reuse)
        site_config: Injected SiteConfig (required — #272 Phase-2e). Threaded
            into every internal helper.

    Returns:
        List of SocialPost objects. The ``social.generate_drafts`` atom filters
        these down to whatever ``social_draft_platforms`` actually requests.
    """
    _sc = site_config
    keywords = keywords or []
    post_url = f"{_site_base_url(site_config=_sc)}/posts/{slug}"
    posts: list[SocialPost] = []

    # --- Twitter ---
    twitter_prompt = _build_twitter_prompt(title, slug, excerpt, keywords, site_config=_sc)
    twitter_text = await _generate_social_text(
        twitter_prompt,
        _twitter_char_limit(site_config=_sc),
        "twitter",
        ollama,
        site_config=_sc,
        post_url=post_url,
    )
    if twitter_text:
        posts.append(SocialPost(platform="twitter", text=twitter_text, post_url=post_url))
        logger.info("[social_poster] Twitter post generated (%d chars)", len(twitter_text))
        # Bluesky (300 chars) and Mastodon (500) are X-style short-form, so the
        # ≤280-char tweet copy fits both — reuse it instead of authoring a
        # separate prompt + spending another LLM call. The draft atom filters
        # these down to whatever social_draft_platforms actually requests.
        posts.append(SocialPost(platform="bluesky", text=twitter_text, post_url=post_url))
        posts.append(SocialPost(platform="mastodon", text=twitter_text, post_url=post_url))
    else:
        logger.error("[social_poster] Twitter post generation failed — empty result")

    # --- LinkedIn ---
    linkedin_prompt = _build_linkedin_prompt(title, slug, excerpt, keywords, site_config=_sc)
    linkedin_text = await _generate_social_text(
        linkedin_prompt,
        _linkedin_char_limit(site_config=_sc),
        "linkedin",
        ollama,
        site_config=_sc,
        post_url=post_url,
    )
    if linkedin_text:
        posts.append(SocialPost(platform="linkedin", text=linkedin_text, post_url=post_url))
        logger.info("[social_poster] LinkedIn post generated (%d chars)", len(linkedin_text))
    else:
        logger.error("[social_poster] LinkedIn post generation failed — empty result")

    return posts
