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

    from poindexter.services.social_poster import generate_social_posts

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
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from poindexter.services.distribution_ref import tag_for
from poindexter.services.integrations.operator_notify import notify_operator
from poindexter.services.llm_providers.dispatcher import dispatch_complete
from poindexter.services.llm_providers.thinking_models import strip_think_blocks
from poindexter.services.logger_config import get_logger
from poindexter.services.site_config import SiteConfig
from poindexter.services.social_drafts import platform_char_limit, postiz_counted_length

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


def _char_limit(platform: str, *, site_config: SiteConfig) -> int:
    # social_<platform>_char_limit, with the defaults held in one place
    # (social_drafts._CHAR_LIMIT_SETTINGS) so edit_draft's gate and this
    # generator can never disagree about a platform's limit. (#198)
    limit = platform_char_limit(platform, site_config=site_config)
    if limit is None:
        raise ValueError(f"social_poster: no char limit configured for {platform!r}")
    return limit


def _twitter_char_limit(*, site_config: SiteConfig) -> int:
    return _char_limit("twitter", site_config=site_config)


def _linkedin_char_limit(*, site_config: SiteConfig) -> int:
    return _char_limit("linkedin", site_config=site_config)


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


def _resolve_social_prompt(key: str, **kwargs: Any) -> str:
    """A social-copy prompt from the SKILL.md pack. A missing key raises: prompts live only in the SKILL.md packs (no in-code copy since 2026-10-09)."""
    from poindexter.services.prompt_manager import get_prompt_manager

    return get_prompt_manager().get_prompt(key, **kwargs)


def _post_url_for(slug: str, *, platform: str, site_config: SiteConfig) -> str:
    """The promoted blog URL, carrying this platform's attribution tag.

    Every caller that needs the URL goes through here — the prompt (which hands
    the model the exact string), the prose-budget arithmetic, and the
    deterministic repair — so the three can never disagree about how long the
    link is. That matters: ``_polish_social_copy`` reserves ``char_limit −
    (the URL's counted length) − 1`` for prose, so a URL that grows after the
    budget was computed is prose the operator loses.

    ``approve_draft`` re-tags with the draft's own platform at post time, which
    is what corrects the short-form copy Bluesky and Mastodon inherit from the
    tweet. The tags are within a character of each other in length, so that
    swap cannot meaningfully overrun a limit this budget already reserved for.
    """
    base = f"{_site_base_url(site_config=site_config)}/posts/{slug}"
    return tag_for(site_config, base, surface=platform)


def _build_twitter_prompt(
    title: str,
    slug: str,
    excerpt: str,
    keywords: list[str],
    *,
    site_config: SiteConfig,
) -> str:
    _sc = site_config
    post_url = _post_url_for(slug, platform="twitter", site_config=_sc)
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    char_limit = _twitter_char_limit(site_config=_sc)
    return _resolve_social_prompt(
        "social.twitter_promote",
        company_name=_sc.get("company_name", ""),
        char_limit=char_limit,
        # Slugged post URLs run ~90 chars — a third of the tweet budget — and
        # models are bad at deriving that subtraction themselves, so the
        # prompt hands them a prose budget (limit − URL − 1 joining space).
        # Before this, roughly half of all drafts overran and got trimmed.
        #
        # This uses the URL's FULL length on purpose, though X itself counts
        # a link as 23 (and _polish_social_copy enforces that looser X
        # budget). The tweet copy is reused for Bluesky, which counts every
        # character of the link, so the tighter budget is what lets one copy
        # fit all three platforms without a second trim.
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
    post_url = _post_url_for(slug, platform="linkedin", site_config=_sc)
    hashtags = " ".join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
    char_limit = _linkedin_char_limit(site_config=_sc)
    return _resolve_social_prompt(
        "social.linkedin_promote",
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


def _fit_prose(text: str, limit: int, measure: Callable[[str], int] = len) -> str:
    """Trim *text* to ``measure(text) <= limit``, preferring a sentence boundary.

    An over-limit draft is cut at the last sentence terminator that fits, so
    the survivor reads as finished copy — a complete short post beats a longer
    fragment ("…larger memories automatically create <URL>" is the 2026-08-26
    truncated-drafts report this exists to prevent). Only when no whole
    sentence fits (one long unbroken sentence) does it fall back to the last
    whole word, re-stripping any ellipsis the cut exposed so shortening copy
    never manufactures a trail-off.

    *measure* is the length the platform will count. It defaults to ``len``;
    ``_polish_social_copy`` passes Postiz's count, under which a ``&`` costs
    five and an emoji two, so a prefix's measured length can exceed its
    character count.
    """
    if limit <= 0:
        return ""
    if measure(text) <= limit:
        return text
    sentence_end = 0
    for m in _SENTENCE_END_RE.finditer(text):
        if measure(text[: m.end()]) > limit:
            break
        sentence_end = m.end()
    if sentence_end:
        return _strip_trailing_ellipsis(text[:sentence_end].rstrip())
    # Longest prefix whose measured length fits. measure() never counts a
    # character as less than one, so the answer is at most ``limit`` chars.
    end = min(len(text), limit)
    while end > 0 and measure(text[:end]) > limit:
        end -= 1
    cut = text[:end].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return _strip_trailing_ellipsis(cut)


def _polish_social_copy(
    text: str, *, post_url: str, char_limit: int, platform: str
) -> str:
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

    Every length here is the one Postiz counts for *platform*
    (``postiz_counted_length``), not ``len()``: a ``&`` in the UTM link costs
    five characters on Bluesky, and a link costs 23 on X however long it is.
    Budgeting with ``len()`` is how a 297-character Bluesky draft got refused
    as 301 (2026-10-04).

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

    def measure(s: str) -> int:
        return postiz_counted_length(s, platform)

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
        prose = _fit_prose(prose, char_limit - measure(url) - 1, measure)
        text = f"{prose} {url}".strip() if prose else url
    else:
        # URL unconfigured/relative — just enforce the platform limit.
        text = _fit_prose(text, char_limit, measure)
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
    # bare-model: the no-pool fallback hands this to OllamaClient as-is
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

        if postiz_counted_length(text, platform) > char_limit:
            logger.warning(
                "[social_poster] %s text exceeded %d chars, trimming", platform, char_limit
            )

        # Deterministic copy repair: strip a trailing ellipsis trail-off,
        # guarantee the post URL is present, and fit to the platform limit by
        # trimming prose (never the link). Fixes the "…"-trail-off and the
        # dropped/mangled URL a weak model can still emit despite the prompt.
        return _polish_social_copy(
            text, post_url=post_url, char_limit=char_limit, platform=platform
        )

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
    # One URL per platform, each carrying its own attribution tag, so a click
    # from Bluesky is distinguishable from a click from X. Built here as well
    # as inside the prompt builders because the deterministic repair
    # (_polish_social_copy) has to append the SAME string the model was handed.
    twitter_url = _post_url_for(slug, platform="twitter", site_config=_sc)
    linkedin_url = _post_url_for(slug, platform="linkedin", site_config=_sc)
    posts: list[SocialPost] = []

    # --- Twitter ---
    twitter_prompt = _build_twitter_prompt(title, slug, excerpt, keywords, site_config=_sc)
    twitter_text = await _generate_social_text(
        twitter_prompt,
        _twitter_char_limit(site_config=_sc),
        "twitter",
        ollama,
        site_config=_sc,
        post_url=twitter_url,
    )
    if twitter_text:
        posts.append(SocialPost(platform="twitter", text=twitter_text, post_url=twitter_url))
        logger.info("[social_poster] Twitter post generated (%d chars)", len(twitter_text))
        # Bluesky and Mastodon are X-style short-form, so reuse the tweet copy
        # instead of authoring a separate prompt + spending another LLM call.
        # The draft atom filters these down to whatever social_draft_platforms
        # actually requests.
        #
        # The copy is shared but the ATTRIBUTION must not be: swap the tweet's
        # tag for each sibling's before the draft is stored, so the operator's
        # preview shows the link that will actually go out and a Bluesky click
        # is never counted as an X click. _polish_social_copy guarantees the
        # exact twitter_url is present (it re-appends it last), so the
        # substring swap is exact rather than a regex guess.
        #
        # Fitting the tweet does NOT make it fit the siblings: X counts the
        # link as 23, while Bluesky counts every character of it and five for
        # each & (Postiz's escape), so a tweet at its limit is ~70 over on
        # Bluesky. Each sibling is re-fitted to its own limit and count; for
        # copy that already fits, the polish leaves it byte-identical.
        for sibling in ("bluesky", "mastodon"):
            sibling_url = _post_url_for(slug, platform=sibling, site_config=_sc)
            posts.append(
                SocialPost(
                    platform=sibling,
                    text=_polish_social_copy(
                        twitter_text.replace(twitter_url, sibling_url),
                        post_url=sibling_url,
                        char_limit=_char_limit(sibling, site_config=_sc),
                        platform=sibling,
                    ),
                    post_url=sibling_url,
                )
            )
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
        post_url=linkedin_url,
    )
    if linkedin_text:
        posts.append(SocialPost(platform="linkedin", text=linkedin_text, post_url=linkedin_url))
        logger.info("[social_poster] LinkedIn post generated (%d chars)", len(linkedin_text))
    else:
        logger.error("[social_poster] LinkedIn post generation failed — empty result")

    return posts
