"""Canonical title generation + web-originality check.

Lifted from content_router_service.py during Phase E2. Two functions the
generate_content stage uses back-to-back:

- :func:`generate_canonical_title` — asks the writer LLM for an SEO-optimized
  title that avoids a list of recent/existing titles. Handles the
  thinking-model failure mode (``<think>…</think>`` wrappers, list markers,
  deliberation markers) via :func:`sanitize_generated_title`.

- :func:`check_title_originality` — web-searches the proposed title to see
  if it collides with existing published content. If it does, the stage
  regenerates with a stronger avoidance prompt. Threshold + enable flag
  come from ``app_settings`` (``qa_title_similarity_threshold`` +
  ``qa_title_originality_enabled``).
"""

from __future__ import annotations

import logging
import re
import string
from difflib import SequenceMatcher

from services.llm_providers.thinking_models import strip_think_blocks
from services.site_config import SiteConfig
from utils.text_utils import strip_title_label

logger = logging.getLogger(__name__)


# Phrases that indicate the LLM deliberated instead of just giving the title.
# If these appear anywhere in the output, we treat the response as unclean
# and return None so the caller falls back to a safer source (topic / seo_title).
TITLE_DELIBERATION_MARKERS: tuple[str, ...] = (
    "let's go with",
    "let me choose",
    "i'll pick",
    "i'd pick",
    "the most unique",
    "the best option",
    "here are",
    "here's a",
    "option 1",
    "option 2",
    "option a",
    "option b",
    "title 1:",
    "title 2:",
)


# QA test-batch tracking suffix, e.g. " (2026-05-10 06:43 #11)". Topics
# produced by the QA batch generator carry this tag through the pipeline;
# strip it before the topic is used as a canonical title so the suffix
# doesn't leak into sitemaps / OG cards / `<title>` tags.
#
# poindexter#471: every awaiting_approval post on 2026-05-10 had the
# suffix attached. The writer model produced a clean H1 inside the body;
# the DB column was set from the raw topic.
_QA_BATCH_SUFFIX_RE = re.compile(
    r"\s*\(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+#\d+\)\s*$"
)


def strip_qa_batch_suffix(title: str) -> str:
    """Remove the QA test-batch tracking suffix from a title or topic.

    The suffix has the shape ``(YYYY-MM-DD HH:MM #N)`` and is appended by
    the QA batch generator so we can correlate awaiting_approval posts
    back to a specific QA run. It is internal tracking only — never
    appropriate for canonical surfaces.

    Returns the input unchanged when no suffix is present. Operates on
    the trailing match only so legitimate parentheticals earlier in the
    title (``"… (2026)"``, ``"… (Part 2)"``, ``"… (v3)"``) are preserved.
    """
    if not title:
        return title
    return _QA_BATCH_SUFFIX_RE.sub("", title).rstrip()


# Markdown heading marker for the writer's H1. We accept H1 (`# `) and
# H2 (`## `) — the writer occasionally emits `## ` for the title line when
# its template uses a different outline convention. Anything deeper (H3+)
# is body structure, not the post title.
_H1_HEADING_RE = re.compile(r"^\s{0,3}#{1,2}\s+(.+?)\s*$")


def extract_h1_title(content: str) -> str | None:
    """Return the first markdown H1/H2 heading text from ``content``, or None.

    Handles Windows line endings, leading whitespace (up to 3 spaces per
    CommonMark), and the model occasionally emitting ``## `` instead of
    ``# `` for the title line. The trailing QA batch suffix is stripped
    from the extracted heading too (defense in depth — the suffix should
    never have reached the H1, but if it did we still clean it).

    Returns None when no H1/H2 is present, or when the heading text is
    empty after stripping.
    """
    if not content:
        return None
    for line in content.splitlines():
        match = _H1_HEADING_RE.match(line)
        if not match:
            continue
        heading = match.group(1).strip().strip('"').strip("'").strip()
        # Strip any wrapping bold markers — writer sometimes emits
        # ``# **Title**`` instead of ``# Title``.
        heading = re.sub(r"\*\*([^*]+)\*\*", r"\1", heading).strip()
        heading = strip_qa_batch_suffix(heading)
        if heading:
            return heading
    return None


# ---------------------------------------------------------------------------
# Junk-title guard (#1280)
# ---------------------------------------------------------------------------

# Patterns that identify LLM "thinking out loud" or instructional meta-text
# that slipped through as a title candidate (case-insensitive prefix or
# substring checks).  Real failures captured 2026-06-09:
#   - "Intent-Based: They signal to the reader exactly what they will get..."
#   - "SEO Keywords: They lead with high-volume terms like *"Mastering..."
_JUNK_TITLE_PREFIX_PATTERNS: tuple[str, ...] = (
    "intent-based:",
    "seo keywords:",
    "heading:",
    "they signal",
    "they lead with",
)

_JUNK_TITLE_SUBSTRING_PATTERNS: tuple[str, ...] = (
    " they signal to the reader",
    " they lead with high-volume",
)

# Rubric / reasoning leak.  The title-generation LLM periodically emits a
# rationale bullet *describing what good titles do* instead of an actual
# title.  Real prod captures (canonical_blog ``pipeline_versions.title``):
#   2026-06-10 (PUBLISHED): "Tone: They move away from the provocative/abstract…"
#   2026-06-10 (PUBLISHED): "No Provocative Tone: These avoid the \"X is Y\"…"
#   2026-06-13 (rejected) : "Neutral Tone: They are helpful and instructional…"
#   2026-06-09            : "Intent-Based: They signal to the reader exactly…"
#   2026-06-09            : "SEO Keywords: They lead with high-volume terms…"
#
# These share one grammatical shape the hardcoded prefix/substring lists
# above keep missing (each new label — "Tone:", "Neutral Tone:" — needs a
# fresh denylist entry, and short ones slip under the length cap): an
# optional short label, then the meta-pronoun "They"/"These" followed by a
# *lowercase* prose verb.  A real headline is title-cased after a subtitle
# colon ("The 32GB Threshold: How the RTX 5090…" / "RTX 5090: These Are…"),
# so the lowercase continuation is the discriminator — it means the model
# wrote a sentence about titles, not a title.  When this fires,
# ``choose_canonical_title`` falls back to the body H1 (the same source
# publish_service uses), so the stored title becomes the real headline.
_RUBRIC_REASONING_RE = re.compile(
    r"^(?:[^:]{1,40}:\s+)?(?:[Tt]hey|[Tt]hese)\s+[a-z]",
)

# Characters that represent a cleanly terminated title.
_CLEAN_END_CHARS: frozenset[str] = frozenset(
    string.ascii_letters + string.digits + '"' + "'" + "!" + "?" + "."
)

# Default maximum title length (SEO-safe cap).  Overrideable via the
# ``title_max_length`` app_settings key.
_DEFAULT_TITLE_MAX_LENGTH: int = 90


def _is_junk_title(title: str, max_length: int = _DEFAULT_TITLE_MAX_LENGTH) -> bool:
    """Return True when ``title`` looks like LLM meta-text rather than a real title.

    Checks (all case-insensitive):

    1. **Truncated ending** — last non-whitespace char is not a clean terminal
       character (letter, digit, ``"``, ``'``, ``!``, ``?``, ``.``).
       Covers tails like ``, o...`` or ``-style truncation``.
    2. **Length cap** — exceeds ``max_length`` characters (default 90, but
       callers pass the DB-configured ``title_max_length`` value).
    3. **Prefix patterns** — starts with any of the known instructional
       prefixes (e.g. ``Intent-Based:``, ``SEO Keywords:``, ``Heading:``).
    4. **Substring patterns** — contains any of the known instructional
       substrings (e.g. `` they signal to the reader``).
    5. **Rubric / reasoning leak** — matches ``_RUBRIC_REASONING_RE``: an
       optional short label then ``They``/``These`` followed by a lowercase
       prose verb (e.g. ``Tone: They move away…`` / ``These avoid…``). This
       generalises checks 3-4 to the shared *structural* shape so novel
       labels and sub-cap-length rubric bullets are still caught.
    6. **Topic verbatim** — intentionally NOT handled here; that case is
       already covered by the ``choose_canonical_title`` fallback logic.

    Pure function — no LLM calls, no I/O.
    """
    if not title or not title.strip():
        return False  # Empty titles are handled elsewhere; not "junk" per se.

    stripped = title.strip()

    # 1. Truncated ending.
    last_char = stripped[-1] if stripped else ""
    if last_char not in _CLEAN_END_CHARS:
        return True

    # 2. Length cap.
    if len(stripped) > max_length:
        return True

    lower = stripped.lower()

    # 3. Instructional prefix patterns.
    for prefix in _JUNK_TITLE_PREFIX_PATTERNS:
        if lower.startswith(prefix):
            return True

    # 4. Instructional substring patterns.
    for substr in _JUNK_TITLE_SUBSTRING_PATTERNS:
        if substr in lower:
            return True

    # 5. Rubric / reasoning leak. Operates on the case-preserved ``stripped``
    #    form (not ``lower``) because the lowercase prose-verb after the
    #    pronoun is the discriminator vs a title-cased subtitle.
    if _RUBRIC_REASONING_RE.match(stripped):
        return True

    return False


def choose_canonical_title(
    topic: str,
    content: str,
    llm_title: str | None = None,
    *,
    site_config: SiteConfig | None = None,
) -> str:
    """Pick the cleanest canonical title for ``pipeline_versions.title``.

    Preference order:

    1. ``llm_title`` when the title-generation LLM produced something
       (already passed through :func:`sanitize_generated_title`, which
       now also strips the QA batch suffix) AND it passes the junk-title
       guard (:func:`_is_junk_title`).  When the guard fires the LLM
       title is discarded and we fall through to (2) or (3) — no retry.
    2. The first H1/H2 heading from ``content`` — the writer commonly
       produces a cleanly-cased title there even when the LLM
       title-gen call fails.
    3. The topic with the QA batch suffix stripped, as a final fallback.

    poindexter#471: previously the fallback was just the raw topic. The
    QA batch suffix leaked into sitemaps / OG cards via this path.

    poindexter#1280: LLM instructional meta-text (e.g. "Intent-Based: They
    signal to the reader…" / "SEO Keywords: They lead with high-volume…")
    used to be accepted verbatim.  :func:`_is_junk_title` now rejects such
    candidates and we degrade gracefully to topic-derived fallback.

    Emits a WARN when the chosen title diverges sharply (>50% character
    distance) from the cleaned topic — that drift signals the writer
    model produced a wildly different angle than what was requested, and
    the operator should review the post before approval.

    ``site_config`` is optional; when supplied the DB-configured
    ``title_max_length`` key is used as the length cap for the junk
    guard (default 90 if absent or not set).
    """
    cleaned_topic = strip_qa_batch_suffix(topic or "").strip()

    # Read DB-configured max length when a site_config is available.
    max_title_length: int = _DEFAULT_TITLE_MAX_LENGTH
    if site_config is not None:
        try:
            max_title_length = site_config.get_int(
                "title_max_length", _DEFAULT_TITLE_MAX_LENGTH
            )
        except Exception:
            pass  # Silently fall back to module default; not a critical path.

    chosen: str
    source: str
    if llm_title and llm_title.strip():
        candidate = strip_qa_batch_suffix(llm_title).strip()
        if _is_junk_title(candidate, max_length=max_title_length):
            logger.warning(
                "[TITLE] Junk-guard rejected LLM title — falling back to H1/topic "
                "(title_max_length=%d): %r",
                max_title_length,
                candidate[:120],
            )
            # Fall through to H1 / topic paths below.
            llm_title = None
            chosen = ""  # will be overwritten
            source = "topic"  # placeholder; overwritten
        else:
            chosen = candidate
            source = "llm"

    if not (llm_title and llm_title.strip()):
        h1 = extract_h1_title(content or "")
        if h1 and h1 != cleaned_topic:
            chosen = h1
            source = "h1"
        else:
            chosen = cleaned_topic
            source = "topic"

    if cleaned_topic and chosen and chosen != cleaned_topic:
        distance = 1.0 - SequenceMatcher(
            None, chosen.lower(), cleaned_topic.lower(),
        ).ratio()
        if distance > 0.5:
            logger.warning(
                "[TITLE] Canonical title drifted >50%% from topic "
                "(source=%s, distance=%.2f): topic=%r → title=%r",
                source, distance, cleaned_topic, chosen,
            )

    # Defense in depth (#728): strip any ``Title:``/``Headline:`` label
    # regardless of which source (llm / h1 / topic) produced the title.
    return strip_title_label(chosen)


def sanitize_generated_title(raw: str) -> str | None:
    """Clean an LLM title response, or return None if it's unsalvageable.

    Real-world failure mode (#198 follow-up): thinking-models sometimes
    return their reasoning trace instead of a clean title, e.g.:

        "*   Let's go with the **Question**. It is the most unique structure..."

    Steps:

    1. Strip ``<think>…</think>`` blocks (some models emit them literally).
    2. Walk from the bottom of the output for the first line that looks
       like an actual title (not bullet / deliberation / empty).
    3. Strip list markers (``*``, ``-``, ``+``, ``1.``), bold wrappers,
       leading ``#`` headers, and surrounding quotes.
    4. Strip the QA test-batch suffix (poindexter#471) — applied before
       the "looks like a title" short-circuit so a topic that already
       reads as a title still loses the trailing tag.
    5. Reject anything that still contains deliberation markers.
    6. Reject empty, too-short (<5 chars), or too-long (>120) results.
    7. Final length trim — SEO caps around 60, hard cap at 100.
    """
    if not raw:
        return None

    text = raw.strip()
    text = strip_think_blocks(text).strip()
    # Strip the QA test-batch suffix up-front so downstream length
    # checks and the "looks like a title" branch see the cleaned form.
    text = strip_qa_batch_suffix(text)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidate: str | None = None
    for line in reversed(lines):
        stripped = re.sub(r"^[\s\*\-\+\u2022]+|^\d+[\.\)]\s*", "", line).strip()
        stripped = re.sub(r"^#+\s+", "", stripped)
        stripped = stripped.strip('"').strip("'").strip()
        stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        # The balanced pass above misses unclosed emphasis the writer
        # commonly emits — e.g. "Cosine and Dot Products**" (trailing) or
        # "**Title" (leading). Strip any remaining asterisk runs plus edge
        # single markers so markdown never reaches a user-facing title.
        stripped = stripped.replace("**", "").strip("*").strip()
        # Defense in depth: re-strip the QA batch suffix after list/header
        # markers are gone, in case it survived the top-of-function pass
        # (e.g. the suffix arrived on a deeper line).
        stripped = strip_qa_batch_suffix(stripped)
        # Strip a leaked ``Title:`` / ``Headline:`` label (#728) so it
        # never reaches the persisted title or the slug derived from it.
        stripped = strip_title_label(stripped)
        if not stripped or len(stripped) < 5 or len(stripped) > 200:
            continue
        lower = stripped.lower()
        if any(marker in lower for marker in TITLE_DELIBERATION_MARKERS):
            continue
        candidate = stripped
        break

    if not candidate:
        return None

    if len(candidate) > 100:
        candidate = candidate[:97].rstrip() + "..."
    return candidate


async def generate_canonical_title(
    topic: str,
    primary_keyword: str,
    content_excerpt: str,
    existing_titles: str = "",
    *,
    site_config: SiteConfig,
) -> str | None:
    """Generate an SEO-optimized title via LLM, avoiding similarity to existing titles.

    Phase-2 DI (#272): ``site_config`` is a required keyword arg — the
    module global + ``set_site_config`` shim was retired.
    """
    _sc = site_config
    try:
        from plugins.registry import get_all_llm_providers
        from services.llm_providers.dispatcher import resolve_tier_model
        from services.prompt_manager import get_prompt_manager
        pm = get_prompt_manager()
        providers = {p.name: p for p in get_all_llm_providers()}
        provider = providers.get("ollama_native")
        if provider is None:
            logger.warning("[TITLE_GEN] ollama_native provider not registered; skipping")
            return None

        prompt = pm.get_prompt(
            "seo.generate_title",
            topic=topic,
            content=content_excerpt,
            primary_keyword=primary_keyword or topic,
        )
        if existing_titles:
            prompt += (
                f"\n\n⚠️ AVOID SIMILARITY to these recent titles:\n{existing_titles}\n\n"
                "Your title must be DISTINCTLY DIFFERENT in structure and wording."
            )

        # Cost-tier API (Lane B sweep). Operators tune the standard tier
        # via app_settings.cost_tier.standard.model — no code edit per
        # niche. Falls back to the legacy pipeline_writer_model setting
        # if the tier mapping isn't seeded; pages the operator and
        # aborts (no silent default) if both miss.
        pool = getattr(_sc, "_pool", None)
        model: str | None = None
        if pool is not None:
            try:
                model = (
                    await resolve_tier_model(pool, "standard")
                ).removeprefix("ollama/")
            except (RuntimeError, ValueError) as tier_err:
                logger.debug(
                    "[TITLE_GEN] cost_tier.standard.model unresolved (%s); "
                    "trying pipeline_writer_model fallback",
                    tier_err,
                )
        if not model:
            fallback = _sc.get("pipeline_writer_model") or ""
            if not fallback:
                from services.integrations.operator_notify import notify_operator
                await notify_operator(
                    "title_generation: cost_tier='standard' has no model "
                    "AND pipeline_writer_model is empty — title regen aborted",
                    critical=False,
                    site_config=_sc,
                )
                return None
            model = fallback.removeprefix("ollama/")
        result = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.7,
            max_tokens=_sc.get_int(
                "content_router_seo_title_max_tokens", 4000,
            ),
        )

        if result and result.text:
            title = sanitize_generated_title(result.text)
            if title:
                logger.debug("Generated title: %s", title)
                return title
            logger.warning(
                "[TITLE_GEN] Sanitizer rejected LLM output as unclean: %r",
                result.text[:100],
            )
        return None

    except Exception as e:
        logger.warning("Error generating canonical title: %s", e, exc_info=True)
        return None


async def check_title_originality(
    title: str,
    *,
    site_config: SiteConfig,
) -> dict:
    """Web-search the title; return similarity summary.

    Return shape::

        {
            "is_original": bool,
            "similar_titles": list[str],
            "max_similarity": float,  # 0.0..1.0
            # GH-87 additions:
            "external_verbatim_match": bool,
            "external_near_match": bool,
            "external_penalty": int,     # points to subtract from QA score
            "external_matches": list[dict],  # [{"title": str, "url": str}, ...]
            "external_fail_open": bool,  # true if the external check couldn't run
        }

    Threshold defaults to 0.6 and comes from
    ``qa_title_similarity_threshold``. Set
    ``qa_title_originality_enabled=false`` to bypass the check (returns
    "is_original": True with empty similar_titles).

    GH-87: also runs :func:`services.title_originality_external.check_external_title_duplicates`
    which hits the DuckDuckGo HTML endpoint directly for the exact quoted
    title. Verbatim matches surface as ``external_verbatim_match=True``
    with a non-zero ``external_penalty`` the QA stage can subtract from
    the final score; near-matches set ``external_near_match=True`` so
    the approver sees a warning without the post being rejected. The
    external check fails OPEN — if DDG is rate-limiting us or the
    network is down, ``external_fail_open=True`` and the pipeline
    continues as if nothing matched.
    """
    # Phase-2 DI (#272): ``site_config`` is a required keyword arg — the
    # module global + ``set_site_config`` shim was retired.
    _sc = site_config

    result: dict = {
        "is_original": True,
        "similar_titles": [],
        "max_similarity": 0.0,
        "external_verbatim_match": False,
        "external_near_match": False,
        "external_penalty": 0,
        "external_matches": [],
        "external_fail_open": False,
    }

    try:
        threshold = _sc.get_float("qa_title_similarity_threshold", 0.6)
        enabled = _sc.get_bool("qa_title_originality_enabled", True)
        if not enabled:
            return result
    except Exception:
        threshold = 0.6

    try:
        from services.web_research import WebResearcher
        researcher = WebResearcher(site_config=_sc)
        search_results = await researcher.search_simple(
            f'"{title}"', num_results=8,
        )
        if not search_results:
            # Broader search without quotes.
            search_results = await researcher.search_simple(title, num_results=8)

        title_lower = title.lower().strip()
        for r in search_results:
            ext_title = (r.get("title") or "").lower().strip()
            if not ext_title:
                continue
            sim = SequenceMatcher(None, title_lower, ext_title).ratio()
            if sim > result["max_similarity"]:
                result["max_similarity"] = sim
            if sim >= threshold:
                result["similar_titles"].append(r.get("title", ""))

        result["is_original"] = len(result["similar_titles"]) == 0
        if not result["is_original"]:
            logger.warning(
                "[TITLE] Originality check FAILED (%.0f%% similar): '%s' vs '%s'",
                result["max_similarity"] * 100,
                title,
                result["similar_titles"][0] if result["similar_titles"] else "?",
            )
        else:
            logger.info(
                "[TITLE] Originality check passed (max %.0f%% similarity)",
                result["max_similarity"] * 100,
            )

    except Exception as e:
        logger.warning("[TITLE] Originality check skipped (non-fatal): %s", e)

    # GH-87: external-article duplicate check. Isolated from the block
    # above so a WebResearcher failure doesn't short-circuit the DDG HTML
    # path (and vice versa).
    try:
        from services.title_originality_external import (
            TitleOriginalityExternalChecker,
        )
        ext = await TitleOriginalityExternalChecker(
            site_config=_sc
        ).check_external_title_duplicates(title)
        result["external_verbatim_match"] = ext.verbatim_match
        result["external_near_match"] = ext.near_match
        result["external_penalty"] = ext.penalty
        result["external_matches"] = ext.matches
        result["external_fail_open"] = ext.fail_open
        # Verbatim external match should flip ``is_original`` so the
        # regenerate-title path in the generate_content stage kicks in
        # the same way it does for our-own-corpus duplicates.
        if ext.verbatim_match:
            result["is_original"] = False
            if ext.matches:
                # Surface the external title so the avoid-list in the
                # regeneration prompt includes it.
                result["similar_titles"].extend(
                    m.get("title", "") for m in ext.matches if m.get("title")
                )
    except Exception as e:
        logger.warning("[TITLE] External originality check skipped (non-fatal): %s", e)

    return result
