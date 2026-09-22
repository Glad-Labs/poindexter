"""Shared YouTube upload-payload builders (description + tags).

Extracted from services/jobs/backfill_videos.py (glad-labs-stack#1460 PR1) so
the surviving distributor (media_distribute) no longer imports them from a job
that PR2 deletes. Pure string helpers — no DB, no heavy deps.

YouTube Data API v3 hard caps (NOT operator-tunable): description ≤ 5000 chars
(we compose to ≤ 4800 for headroom); tags ≤ 30 and ≤ 500 joined chars. The
adapter (services/publish_adapters/youtube.py) enforces the hard caps; we build
values that stay comfortably under them so the upload never 400s mid-stream.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from poindexter.services.distribution_ref import tag_for
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

_YOUTUBE_DESCRIPTION_BUDGET = 4800
_YOUTUBE_MAX_TAGS = 30
_YOUTUBE_TAGS_JOINED_LIMIT = 500


def _strip_markup(text: str) -> str:
    """Strip HTML tags and collapse ALL whitespace to single spaces.

    The right tool for one-line fields (the excerpt header). It is the wrong
    tool for a markdown body — it flattens every paragraph break, which is how
    the pre-#3508-follow-up descriptions became one 4,800-char wall — so the
    body path uses :func:`_markdown_to_plain` instead.
    """
    if not text:
        return ""
    stripped = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


# Markdown constructs that must not reach a YouTube viewer as raw syntax. Each
# pattern keeps the human-readable text and drops the machinery. Dep-free on
# purpose: this is a teaser renderer, not a markdown engine — anything it
# misses degrades to slightly odd punctuation, never to a broken upload.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|`{1,3})(?=\S)|(?<=\S)(\*{1,3}|_{1,3}|`{1,3})")
_MD_FENCE_RE = re.compile(r"^\s*```[^\n]*$", re.MULTILINE)


def _markdown_to_plain(text: str) -> str:
    """Render a markdown body to viewer-facing plain text.

    Keeps paragraph breaks (blank-line separated), collapses whitespace only
    WITHIN a paragraph, and strips the syntax the live videos were leaking
    verbatim: ``[text](url)`` becomes just the text (the URLs are relative
    ``/go/…`` affiliate and ``/posts/…`` internal paths — dead as description
    text, and the affiliate slugs are nobody's business), images vanish,
    ``## heading`` markers drop while the heading text survives as its own
    paragraph, and emphasis/fence markers go. HTML tags are stripped last.
    """
    if not text:
        return ""
    text = _MD_FENCE_RE.sub("", text)
    text = _MD_IMAGE_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    # Replace the marker with a paragraph break rather than nothing: the writer
    # often emits "## Heading" hard against the previous paragraph (single
    # newline), and plain removal glued the heading text onto that paragraph's
    # last sentence.
    text = _MD_HEADING_RE.sub("\n\n", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    text = re.sub(r"<[^>]+>", "", text)
    paragraphs = [
        re.sub(r"\s+", " ", para).strip()
        for para in re.split(r"\n\s*\n", text)
    ]
    return "\n\n".join(p for p in paragraphs if p)


def _trim_at_sentence(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` at a sentence end, else a word end.

    The live descriptions ended mid-word ("Every post gets a val") because the
    budget was applied as a bare slice. A teaser that stops at a full stop
    reads as chosen; one that stops mid-word reads as broken.
    """
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for stop in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        cut = head.rfind(stop)
        if cut > limit // 2:
            return head[: cut + 1].rstrip()
    cut = head.rfind(" ")
    if cut > limit // 2:
        return head[:cut].rstrip()
    return head.rstrip()


_YOUTUBE_TITLE_LIMIT = 100


_SHORT_TITLE_SOURCE_DEFAULT = "script_hook"
_SHORT_TITLE_MAX_DEFAULT = 60
_SHORT_HASHTAGS_MAX_DEFAULT = 3
_SHORT_HOOK_DESCRIPTION_MAX = 220
_HASHTAG_MAX_CHARS = 30
_SHORTS_WATCH_URL_FMT = "https://www.youtube.com/shorts/{video_id}"
_LONG_WATCH_URL_FMT = "https://www.youtube.com/watch?v={video_id}"
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def _setting(site_config: Any, key: str, default: Any) -> Any:
    if site_config is None:
        return default
    try:
        raw = site_config.get(key, default)
    except Exception as exc:  # noqa: BLE001 — must not block an upload
        logger.warning(
            "[YOUTUBE_PAYLOAD] %s read failed (%s) — using the default %r",
            key, describe_exception(exc), default,
        )
        return default
    return default if raw is None else raw


def _setting_int(site_config: Any, key: str, default: int) -> int:
    raw = _setting(site_config, key, default)
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        logger.warning(
            "[YOUTUBE_PAYLOAD] %s is not an integer (%r) — using %d", key, raw, default,
        )
        return default


def _cross_links_enabled(site_config: Any) -> bool:
    raw = str(_setting(site_config, "youtube_pair_cross_links", "true")).strip().lower()
    return raw not in ("false", "0", "no", "off")


def shorten_at_word(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` chars on a word boundary, trailing
    separators dropped — the same rule the suffix budget uses. A cut that
    already lands on a word boundary keeps the whole word before it."""
    clean = (text or "").strip()
    if limit <= 0 or len(clean) <= limit:
        return clean
    head = clean[:limit].rstrip()
    if not clean[limit].isspace():
        cut = head.rfind(" ")
        if cut > limit // 2:
            head = head[:cut]
    return head.rstrip(" ,;:-—").rstrip()


# Scene-setting run-ups a narration script sometimes opens with. Stripped from
# the TITLE and the description hook only — the narration audio is already
# rendered, and the words the presenter speaks are not ours to rewrite here.
# Each alternative must be followed by a COMMA: that is what makes it a
# throat-clearing clause rather than the sentence's own subject ("Let's talk
# about zero-click content" has no comma, and cutting it would leave a noun
# phrase, not a claim). The script prompt now asks for a flat claim outright
# (_build_scene_prompt); this catches the scripts already frozen into
# pipeline_versions, which is every Short on the channel today.
_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"in today's [\w' -]{2,30}"
    r"|in (?:the|this|an?) (?:world|age|era|day and age|modern era) of [\w' -]{2,40}"
    r"|in (?:the|this) (?:world|age|era|day and age|modern era)"
    r"|in this (?:article|video|post|short)"
    r"|these days|nowadays|as we all know|it's no secret"
    r"|as (?:you|we) (?:probably )?(?:know|might know)"
    r")\s*,\s*",
    re.I,
)


def strip_preamble(sentence: str) -> str:
    """Drop a leading scene-setting clause and re-capitalise what is left.

    ``"In today's digital age, zero-click content is the new standard."`` ->
    ``"Zero-click content is the new standard."`` Returns the input unchanged
    when nothing matches, or when the remainder would be too short to be a
    claim on its own (a strip that leaves two words has cut the sentence, not
    its run-up).
    """
    clean = (sentence or "").strip()
    stripped = _PREAMBLE_RE.sub("", clean, count=1).strip()
    if stripped == clean or len(stripped.split()) < 3:
        return clean
    return stripped[:1].upper() + stripped[1:]


def _sentences(text: str) -> list[str]:
    clean = _strip_markup(_markdown_to_plain(text or ""))
    return [p.strip() for p in _SENTENCE_END_RE.split(clean) if p.strip()] if clean else []


def first_sentences(text: str, *, max_sentences: int = 1, limit: int = 0) -> str:
    """The first ``max_sentences`` sentence(s) of ``text`` (markup stripped).

    With a ``limit``, whole sentences are taken while they fit; only when the
    very first one is longer than the budget is it shortened at a word
    boundary — a hook cut mid-sentence ("…they surface answers directly,
    reducing") reads worse than one sentence fewer.
    """
    parts = _sentences(text)
    if not parts:
        return ""
    take = parts[: max(1, max_sentences)]
    if limit <= 0:
        return " ".join(take)
    out = ""
    for sentence in take:
        candidate = f"{out} {sentence}".strip() if out else sentence
        if len(candidate) > limit:
            break
        out = candidate
    return out or shorten_at_word(take[0], limit)


def short_hook_title(script: str, *, site_config: Any) -> str:
    """A Short's own title: the first sentence of its narration — the cold-open
    line the short-form director writes to hook the viewer — kept whole when
    it is within ``youtube_short_title_max_chars`` plus a quarter of slack
    (the feed shows ~40 chars either way; the 100-char API cap is the hard
    limit and the suffix budget below handles it), else shortened at a word
    boundary. Trailing full stop dropped: a title is not a sentence."""
    limit = _setting_int(site_config, "youtube_short_title_max_chars", _SHORT_TITLE_MAX_DEFAULT)
    parts = _sentences(script)
    if not parts:
        return ""
    first = strip_preamble(parts[0])
    hook = first if len(first) <= limit + limit // 4 else shorten_at_word(first, limit)
    return hook.rstrip(".").strip()


def short_hook_line(script: str) -> str:
    """The Short's description opener: its first one or two whole sentences,
    with the same scene-setting run-up stripped off the first."""
    parts = _sentences(script)
    if not parts:
        return ""
    parts[0] = strip_preamble(parts[0])
    return first_sentences(" ".join(parts[:2]), max_sentences=2, limit=_SHORT_HOOK_DESCRIPTION_MAX)


def hashtags_for_short(keywords: list[str], *, site_config: Any) -> list[str]:
    """``#Shorts`` plus up to ``youtube_short_hashtags_max`` CamelCase tags
    from the post's keywords (letters/digits only, deduped, over-long
    keywords skipped)."""
    max_n = _setting_int(site_config, "youtube_short_hashtags_max", _SHORT_HASHTAGS_MAX_DEFAULT)
    out = ["#Shorts"]
    seen = {"shorts"}
    for kw in keywords or []:
        if len(out) - 1 >= max_n:
            break
        words = re.findall(r"[A-Za-z0-9]+", str(kw or ""))
        tag = "".join(w[:1].upper() + w[1:] for w in words)
        if not tag or len(tag) > _HASHTAG_MAX_CHARS or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append("#" + tag)
    return out


def twin_watch_url(video_id: str, *, twin_is_short: bool) -> str:
    """The public URL of the pair's other render: ``/shorts/<id>`` for a
    Short, ``/watch?v=<id>`` for the long form — the forms YouTube treats as
    Short-to-long and long-to-Short links."""
    vid = (video_id or "").strip()
    if not vid:
        return ""
    fmt = _SHORTS_WATCH_URL_FMT if twin_is_short else _LONG_WATCH_URL_FMT
    return fmt.format(video_id=vid)


def _build_youtube_title(
    title: str, *, shorts: bool, site_config: Any, hook: str = "",
) -> str:
    """Compose the video title, distinguishing a Short from its long-form twin.

    A post can produce BOTH a long-form video and a Short, and both took
    ``posts.title`` verbatim — so a channel showing both had two videos under
    one identical name, with nothing but the thumbnail to tell them apart.

    Two levers, both DB-configured:

    * ``youtube_short_title_source`` (default ``script_hook``): the Short's
      title is ``hook`` — its own narration's first sentence, shortened to
      ``youtube_short_title_max_chars`` — instead of the article title. The
      Shorts feed shows ~40 characters, so an article title plus a suffix
      was two identical stubs on the channel page anyway. ``post_title``
      keeps the article title; an empty ``hook`` falls back to it.
    * ``youtube_short_title_suffix`` (default ``" #Shorts"``) is appended
      either way: it separates the pair in every listing, and ``#Shorts`` is
      one of the markers YouTube itself keys off for Shorts classification.

    Budget-aware rather than a bare append: YouTube caps titles at 100 chars,
    so appending to a long title would push the suffix past the cap and the
    adapter's clamp would cut off the very thing that distinguishes it. The
    title is trimmed at a word boundary to make room first.

    Empty suffix = no suffix, an operator's explicit choice.
    """
    clean = (title or "").strip()
    if not shorts:
        return clean[:_YOUTUBE_TITLE_LIMIT]

    base = clean
    source = str(_setting(site_config, "youtube_short_title_source", _SHORT_TITLE_SOURCE_DEFAULT)).strip().lower()
    if source == "script_hook" and (hook or "").strip():
        base = (hook or "").strip() or clean

    suffix = str(_setting(site_config, "youtube_short_title_suffix", " #Shorts"))
    if not suffix.strip():
        return base[:_YOUTUBE_TITLE_LIMIT]

    # Idempotent: never stack a second marker on a title that already carries
    # one (an operator-written title, or a re-sync of an already-suffixed video).
    if suffix.strip().lower() in base.lower():
        return base[:_YOUTUBE_TITLE_LIMIT]

    room = _YOUTUBE_TITLE_LIMIT - len(suffix)
    if room <= 0:
        return base[:_YOUTUBE_TITLE_LIMIT]
    head = base[:room].rstrip()
    if len(base) > room:
        cut = head.rfind(" ")
        if cut > room // 2:
            head = head[:cut].rstrip()
    return f"{head}{suffix}"


def _parse_seo_keywords(seo_keywords: str) -> list[str]:
    """Parse the comma-separated ``posts.seo_keywords`` column into tags.

    Strips each keyword, drops empties, caps at 30 tags, and trims
    trailing tags until the comma-joined string fits YouTube's combined
    500-char tag limit. Returns ``[]`` when there are no usable keywords
    (caller converts that to ``tags=None``).
    """
    tags = [k.strip() for k in (seo_keywords or "").split(",") if k.strip()]
    tags = tags[:_YOUTUBE_MAX_TAGS]
    # Drop trailing tags until the joined string is under the limit.
    while tags and len(",".join(tags)) > _YOUTUBE_TAGS_JOINED_LIMIT:
        tags.pop()
    return tags


def _body_chars_budget(site_config: Any) -> int:
    """How many characters of article body the description may carry.

    ``youtube_description_body_chars`` — **0 (the default) means none**: the
    description is the excerpt hook plus the tagged back-link, and the article
    itself stays on the site (operator decision, 2026-08-31 — the previous
    behaviour dumped the whole stripped body to the 4,800-char cap, which
    produced a one-paragraph markdown-soup wall on every live video). A
    positive value opts a snippet back in, sentence-trimmed and rendered
    through :func:`_markdown_to_plain`, capped at the 4,800 composition budget.
    """
    if site_config is None:
        return 0
    try:
        raw = str(site_config.get("youtube_description_body_chars", "0") or "0")
        return max(0, min(int(raw), _YOUTUBE_DESCRIPTION_BUDGET))
    except (TypeError, ValueError):
        logger.warning(
            "[YOUTUBE_PAYLOAD] youtube_description_body_chars is not an "
            "integer — treating as 0 (no body snippet)"
        )
        return 0


def _build_youtube_description(
    *,
    seo_description: str,
    body: str,
    site_config: Any,
    slug: str,
    shorts: bool = False,
    hook: str = "",
    twin_url: str = "",
    hashtags: list[str] | None = None,
) -> str:
    """Compose the YouTube video description — one layout per render.

    Long form (``shorts=False``)::

        {seo_description}

        Read the full post: {site_url}/posts/{slug}?utm_source=youtube&utm_medium=video

        Watch the Short: https://www.youtube.com/shorts/{twin}     (only when the Short is live)

        {optional body snippet — youtube_description_body_chars}

    Short (``shorts=True``)::

        {hook — the Short's own first sentences, else the excerpt}

        Watch the full breakdown: https://www.youtube.com/watch?v={twin}   (only when the long form is live)

        Read the full post: {site_url}/posts/{slug}?utm_source=youtube&utm_medium=shorts

        #Shorts #KeywordOne #KeywordTwo

    Before 2026-09-22 both renders shipped one identical description: the
    excerpt and an article link tagged ``utm_medium=video`` — so a click from
    a Short was indistinguishable from a long-form click, the Short's own hook
    (``short_summary_script``) went unused, and the pair never linked to each
    other, which is the one lever YouTube gives for turning Shorts viewers
    into long-form viewers. ``twin_url`` is composed from what is actually
    live (``pipeline_distributions`` ``status='published'``) and recomposed
    when the twin lands later; empty means the line is simply omitted.

    ``seo_description`` comes from ``posts.excerpt`` (empty string when
    null). The "Read the full post" line is omitted gracefully (logged at
    info) when ``site_url`` can't be resolved or ``slug`` is missing — never
    raises. Total stays ≤ 4800 chars (YouTube hard cap 5000; the adapter
    re-clamps as a backstop).
    """
    # The excerpt occasionally carries inline <img> HTML or a stray markdown
    # link from the pipeline; render it to one clean line.
    seo_description = _strip_markup(_markdown_to_plain(seo_description or ""))
    opener = (hook or "").strip() if shorts else ""
    if not opener:
        opener = seo_description
    # Resolve the canonical back-link. Missing site_url / slug → omit the
    # line (the only deliberate graceful fallback here, per the #275
    # design); log it so the operator knows why it's absent.
    backlink = ""
    site_url = ""
    if site_config is not None:
        try:
            site_url = str(site_config.require("site_url") or "").rstrip("/")
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[YOUTUBE_PAYLOAD] site_url unavailable — omitting "
                "YouTube back-link: %s", describe_exception(exc),
            )
            site_url = ""
    if site_url and slug:
        # Tagged so a click from the description is attributable to YouTube
        # rather than landing in the "(direct)" bucket — a video description is
        # exactly the kind of link a browser sends no referrer for. A Short's
        # link says so (utm_medium=shorts) so the two renders stay separable.
        backlink = "Read the full post: " + tag_for(
            site_config, f"{site_url}/posts/{slug}", surface="youtube",
            medium="shorts" if shorts else None,
        )
    elif not slug:
        logger.info(
            "[YOUTUBE_PAYLOAD] slug missing — omitting YouTube back-link",
        )
    cross = ""
    if twin_url and _cross_links_enabled(site_config):
        cross = ("Watch the full breakdown: " if shorts else "Watch the Short: ") + twin_url

    if shorts:
        tag_line = " ".join(hashtags or [])
        header_parts = [p for p in (opener, cross, backlink, tag_line) if p]
    else:
        header_parts = [p for p in (opener, backlink, cross) if p]
    header = "\n\n".join(header_parts)
    body_budget = 0 if shorts else _body_chars_budget(site_config)
    body_snippet = ""
    if body_budget > 0:
        rendered = _markdown_to_plain(body)
        if seo_description:
            # posts.excerpt is the post's opening paragraph; drop the body's
            # first paragraph when it repeats the excerpt so the snippet
            # continues the story instead of restarting it. Prefix-compare
            # (either direction) because the excerpt may itself be a trim.
            paragraphs = rendered.split("\n\n")
            if paragraphs:
                first = paragraphs[0]
                if first.startswith(seo_description) or seo_description.startswith(first):
                    rendered = "\n\n".join(paragraphs[1:])
        room = _YOUTUBE_DESCRIPTION_BUDGET - len(header) - 2 if header else _YOUTUBE_DESCRIPTION_BUDGET
        body_snippet = _trim_at_sentence(rendered, min(body_budget, max(room, 0)))
    if not header:
        composed = body_snippet[:_YOUTUBE_DESCRIPTION_BUDGET]
    elif not body_snippet:
        composed = header[:_YOUTUBE_DESCRIPTION_BUDGET]
    else:
        composed = f"{header}\n\n{body_snippet}"
    # YouTube rejects any bare < or > (e.g. SQL WHERE x > 0, markdown arrows).
    # Strip them so the upload never 400s on invalidDescription.
    return composed.replace("<", "").replace(">", "")


__all__ = [
    "_build_youtube_description",
    "_build_youtube_title",
    "_markdown_to_plain",
    "_parse_seo_keywords",
    "_strip_markup",
    "_trim_at_sentence",
    "first_sentences",
    "hashtags_for_short",
    "short_hook_line",
    "short_hook_title",
    "strip_preamble",
    "shorten_at_word",
    "twin_watch_url",
]
