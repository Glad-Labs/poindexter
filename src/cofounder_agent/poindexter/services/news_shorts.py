"""News Shorts: a YouTube Short made from a news story, with no blog post behind it.

Earned 2026-10-18. YouTube is the channel that grows, and until now every
video on it was made from a blog post: a Short exists only when a post ran the
whole canonical_blog pipeline, so the channel's output was capped at the blog's
cadence (about one post a day) and lagged the news by the time a post takes.
The topic pool already holds the day's stories (Hacker News and the RSS feeds,
ingested hourly, judged against the niche's scope by the batch sweep). A Short
needs a 30 to 45 second script, which is far less than a post, so it can be
made from a story on the day it breaks.

The flow, one task per Short (``template_slug='news_short'``,
``services/news_short_spec.py``):

1. **Pick.** :func:`pick_story` takes the freshest pooled Hacker News / RSS
   items for the niche (``news_shorts_*`` settings), drops any a news Short
   already used, asks the niche scope check (``services/topic_scope.py``)
   which are in scope and takes the best-scored one. Unlike the batch sweep it
   fails CLOSED: a story the check could not judge is not used, because a
   wrong pick costs a GPU render. :func:`enqueue_news_short` queues the task;
   ``EnqueueNewsShortsJob`` does both on a schedule.
2. **Research** (``news_short.research``). The story's own page is fetched
   (``WebResearcher.fetch_page_text``) and is the whole corpus: a story whose
   page cannot be read is not made, because a script written from a headline
   and a 200-character RSS summary invents the rest. The most related
   published post (cosine over the post embeddings) becomes the upload's
   article link, when one clears ``news_shorts_related_post_min_similarity``.
3. **Script** (``news_short.write_script``). The director model writes the
   narration from the ``news_short.script`` prompt and :func:`clean_script`
   checks it: spoken prose only, a word count sized from
   ``video_short_target_seconds``, an opener that makes a claim, and EVERY
   number present in the source page (``numeric_fidelity`` in strict mode,
   where an unattributed number is still checked, because the source is the
   complete ground truth here). One corrective retry, then the task fails.
4. **Render** with the ordinary Short steps: a Short-only shot list
   (``stage.generate_video_shot_list`` with ``short_only``), its review,
   narration, captions, audio QA, the 9:16 render, the YouTube package and
   video QA, then ``media.persist``.
5. **Release** (``news_short.finalize``). The ``video_short`` row is marked
   standalone (``metadata.standalone``, poindexter#1140), so
   ``media_distribute`` seeds a review row for it instead of waiting for a
   post: Matt approves or rejects it like any other video, and an approved
   Short uploads in the next Shorts slot with a source credit and the related
   article link (``youtube_payload``).

Off by default (``news_shorts_enabled``). See
``docs/architecture/news-shorts.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

#: ``pipeline_tasks.task_type`` and ``template_slug`` of a news Short run.
TASK_TYPE = "news_short"
TEMPLATE_SLUG = "news_short"

#: ``media_assets.metadata`` keys written on the Short's row at finalize.
METADATA_KEY = "news_short"
SOURCE_URL_KEY = "news_source_url"
RELATED_SLUG_KEY = "related_post_slug"

DEFAULT_SOURCES = ("hackernews", "rss")
DEFAULT_NICHE = "starter-blog"
DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_CANDIDATE_LIMIT = 20
DEFAULT_MIN_SOURCE_CHARS = 800
DEFAULT_SOURCE_MAX_CHARS = 6000
DEFAULT_MIN_WORDS = 55
DEFAULT_RELATED_MIN_SIMILARITY = 0.63
_WORDS_PER_SECOND_DEFAULT = 2.1
_MAX_WORDS_SLACK = 1.15


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _sc(site_config: Any, key: str, default: Any) -> Any:
    if site_config is None:
        return default
    try:
        value = site_config.get(key, default)
    except Exception:  # noqa: BLE001  # silent-ok: a settings read falls back to its default
        return default
    return default if value is None or value == "" else value


def _sc_int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(float(_sc(site_config, key, default)))
    except (TypeError, ValueError):
        return default


def _sc_float(site_config: Any, key: str, default: float) -> float:
    try:
        return float(_sc(site_config, key, default))
    except (TypeError, ValueError):
        return default


def _sc_bool(site_config: Any, key: str, default: bool) -> bool:
    return str(_sc(site_config, key, "true" if default else "false")).strip().lower() in (
        "true", "1", "yes", "on",
    )


def enabled(site_config: Any) -> bool:
    """``news_shorts_enabled`` (default off)."""
    return _sc_bool(site_config, "news_shorts_enabled", False)


def sources(site_config: Any) -> list[str]:
    """``topic_pool.source`` values a news Short may come from."""
    raw = str(_sc(site_config, "news_shorts_sources", ",".join(DEFAULT_SOURCES)))
    return [s.strip() for s in raw.split(",") if s.strip()]


def niche_slug(site_config: Any) -> str:
    return str(_sc(site_config, "news_shorts_niche", DEFAULT_NICHE)).strip()


def word_bounds(site_config: Any) -> tuple[int, int, int]:
    """``(min_words, target_words, max_words)`` for the narration.

    The target comes from the same two settings the blog's Short lane uses
    (``video_short_target_seconds`` x ``media_narration_words_per_second``), so
    the script, the shot list's duration estimate and the render agree.
    """
    seconds = max(15, _sc_int(site_config, "video_short_target_seconds", 45))
    wps = _sc_float(site_config, "media_narration_words_per_second", _WORDS_PER_SECOND_DEFAULT)
    if wps <= 0:
        wps = _WORDS_PER_SECOND_DEFAULT
    target = max(20, round(seconds * wps))
    minimum = min(target, max(20, _sc_int(site_config, "news_shorts_script_min_words", DEFAULT_MIN_WORDS)))
    return minimum, target, max(minimum, round(target * _MAX_WORDS_SLACK))


def _model(site_config: Any) -> str:
    return str(
        _sc(site_config, "news_shorts_script_model", "")
        or _sc(site_config, "video_director_model", "")
    ).strip()


# ---------------------------------------------------------------------------
# Picking a story
# ---------------------------------------------------------------------------


@dataclass
class NewsStory:
    """One topic_pool row a news Short can be made from."""

    pool_id: str
    source: str
    title: str
    url: str
    summary: str = ""
    score: float = 0.0
    # How the story was chosen: "hook" (the hook pick) or "score" (best feed
    # score, the fallback). Kept on the task so views can be compared by
    # picker. Empty on tasks queued before 2026-10-27.
    picked_by: str = ""
    pick_reason: str = ""

    def as_metadata(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_metadata(cls, raw: Any) -> NewsStory | None:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return None
        if not isinstance(raw, dict):
            return None
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not title or not url:
            return None
        try:
            score = float(raw.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        return cls(
            pool_id=str(raw.get("pool_id") or ""), source=str(raw.get("source") or ""),
            title=title, url=url, summary=str(raw.get("summary") or ""), score=score,
            picked_by=str(raw.get("picked_by") or ""),
            pick_reason=str(raw.get("pick_reason") or ""),
        )


# A story is "used" once ANY news_short task carries its pool row or its URL,
# whatever became of that task: a failed render is not retried by picking the
# story again the next hour, and the same article reached through two feeds is
# one story.
_CANDIDATES_SQL = """
    SELECT tp.id::text AS pool_id, tp.source, tp.title, tp.url, tp.summary, tp.score
      FROM topic_pool tp
      JOIN niches n ON n.id = tp.niche_id
     WHERE n.slug = $1
       AND tp.source = ANY($2::text[])
       AND tp.status = 'pooled'
       AND tp.url <> ''
       AND tp.ingested_at > NOW() - ($3 * INTERVAL '1 hour')
       AND NOT EXISTS (
           SELECT 1
             FROM pipeline_tasks t
             JOIN pipeline_versions v ON v.task_id = t.task_id
            WHERE t.task_type = $4
              AND (v.stage_data -> 'task_metadata' -> 'news_short' ->> 'pool_id' = tp.id::text
                   OR v.stage_data -> 'task_metadata' -> 'news_short' ->> 'url' = tp.url)
       )
     ORDER BY tp.score DESC, tp.ingested_at DESC
     LIMIT $5
"""

_MADE_SINCE_SQL = """
    SELECT COUNT(*) FROM pipeline_tasks
     WHERE task_type = $1 AND created_at >= $2
"""


async def candidate_stories(pool: Any, site_config: Any) -> list[NewsStory]:
    """Fresh, unused pooled stories for the configured niche, best first."""
    rows = await pool.fetch(
        _CANDIDATES_SQL,
        niche_slug(site_config),
        sources(site_config),
        max(1, _sc_int(site_config, "news_shorts_max_age_hours", DEFAULT_MAX_AGE_HOURS)),
        TASK_TYPE,
        max(1, _sc_int(site_config, "news_shorts_candidate_limit", DEFAULT_CANDIDATE_LIMIT)),
    )
    return [
        NewsStory(
            pool_id=r["pool_id"], source=r["source"], title=r["title"], url=r["url"],
            summary=r["summary"] or "", score=float(r["score"] or 0.0),
        )
        for r in rows
    ]


@dataclass
class PickResult:
    story: NewsStory | None = None
    considered: int = 0
    in_scope: int = 0
    note: str = ""
    errors: list[str] = field(default_factory=list)


async def pick_story(pool: Any, site_config: Any) -> PickResult:
    """The best fresh, unused, in-scope story, or why there is none.

    Fails closed on scope: only a story the check judged IN scope is used.
    A niche with no ``topic_subject`` has no scope to judge against, and no
    story is picked; set one with ``poindexter topics niche set-scope``.
    """
    from poindexter.services.niche_service import NicheService
    from poindexter.services.topic_scope import ScopeItem, check_scope

    stories = await candidate_stories(pool, site_config)
    result = PickResult(considered=len(stories))
    if not stories:
        result.note = "no fresh unused story in the pool"
        return result
    niche = await NicheService(pool).get_by_slug(niche_slug(site_config))
    if niche is None:
        result.note = f"niche {niche_slug(site_config)!r} not found"
        return result
    if not niche.has_topic_scope:
        result.note = f"niche {niche.slug!r} has no topic_subject to judge stories against"
        return result
    items = [
        ScopeItem(f"n{i}", f"{s.title} — {s.summary[:300]}" if s.summary else s.title)
        for i, s in enumerate(stories)
    ]
    scope = await check_scope(items, niche, site_config=site_config)
    result.in_scope = len(scope.in_scope)
    result.errors = list(scope.errors)
    in_scope = [story for i, story in enumerate(stories) if f"n{i}" in scope.in_scope]
    if in_scope:
        result.story = await _pick_by_hook(in_scope, niche, site_config, result)
        return result
    result.note = (
        f"none of {len(stories)} stories judged in scope"
        + (f" ({len(scope.unjudged)} unjudged)" if scope.unjudged else "")
    )
    return result


async def _pick_by_hook(
    stories: list[NewsStory], niche: Any, site_config: Any, result: PickResult,
) -> NewsStory:
    """The in-scope story with the most concrete hook; never raises.

    ``stories`` arrive in feed-score order, and the first is the fallback for
    every failure: the pick turned off, a single story, no model, a model
    error, or a reply that names no offered story. The chosen story carries
    ``picked_by`` ("hook" or "score") and the model's one-line reason.
    """
    from dataclasses import replace

    fallback = replace(stories[0], picked_by="score", pick_reason="")
    if len(stories) < 2 or not _sc_bool(site_config, "news_shorts_hook_pick_enabled", True):
        return fallback
    offered = stories[: max(2, _sc_int(site_config, "news_shorts_hook_pick_max_stories", 8))]
    labels = {f"s{i}": story for i, story in enumerate(offered, start=1)}
    lines = "\n".join(
        f"[{label}] {' '.join(story.title.split())}"
        + (f" — {' '.join(story.summary.split())[:300]}" if story.summary.strip() else "")
        for label, story in labels.items()
    )
    model = str(site_config.get("news_shorts_hook_pick_model", "") or "").strip()
    try:
        from poindexter.services.prompt_manager import get_prompt_manager
        from poindexter.services.topic_ranking import _ollama_chat_json
        from poindexter.utils.json_extract import extract_json_object

        if not model:
            from poindexter.services.llm_text import resolve_structured_model

            model = resolve_structured_model(site_config=site_config)
        prompt = get_prompt_manager().get_prompt(
            "news_short.hook_pick",
            subject=str(getattr(niche, "topic_subject", "") or "").strip() or "technology",
            stories=lines,
        )
        reply = extract_json_object(
            await _ollama_chat_json(prompt, model=model, site_config=site_config) or ""
        ) or {}
    except Exception as exc:  # noqa: BLE001 — the feed-score pick is a working fallback
        from poindexter.utils.exception_format import describe_exception

        result.errors.append(f"hook pick failed: {describe_exception(exc)}")
        logger.warning("[news_shorts] hook pick failed (%s); using the best feed score",
                       describe_exception(exc))
        return fallback
    label = str(reply.get("pick") or "").strip().strip("[]").strip()
    chosen = labels.get(label)
    if chosen is None:
        result.errors.append(f"hook pick named no offered story: {label[:40]!r}")
        logger.warning("[news_shorts] hook pick named no offered story (%r); using the best "
                       "feed score", label[:40])
        return fallback
    return replace(
        chosen, picked_by="hook", pick_reason=str(reply.get("reason") or "").strip()[:300],
    )


async def made_since(pool: Any, since: Any) -> int:
    """How many news Short tasks were created at or after ``since``."""
    return int(await pool.fetchval(_MADE_SINCE_SQL, TASK_TYPE, since) or 0)


async def enqueue_news_short(pool: Any, story: NewsStory, *, niche: str) -> str:
    """Queue a news Short run for ``story``; return the task id."""
    from poindexter.services.tasks_db import TasksDatabase

    task_id = await TasksDatabase(pool).add_task({
        "task_type": TASK_TYPE,
        "template_slug": TEMPLATE_SLUG,
        "topic": story.title,
        "status": "pending",
        "niche_slug": niche or None,
        "task_metadata": {METADATA_KEY: story.as_metadata()},
    })
    logger.info(
        "[news_shorts] queued task %s for %s story %r (%s)",
        task_id, story.source, story.title[:80], story.url,
    )
    return str(task_id)


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


# Cosine of the story's headline to each published post's closest chunk.
# Plain pgvector, not MemoryClient.search: that path fuses BM25 with the
# vectors and reranks, so a shared word lets a weak match through the floor
# (an RTX 5090 theft "matched" a post about training a transformer).
_RELATED_SQL = """
    SELECT p.slug, p.title, MAX(1 - (e.embedding <=> $1::vector)) AS cosine
      FROM embeddings e
      JOIN posts p ON p.id::text = e.source_id
     WHERE e.source_table = 'posts'
       AND p.status = 'published'
     GROUP BY p.slug, p.title
     ORDER BY cosine DESC
     LIMIT 1
"""


async def related_post(pool: Any, site_config: Any, text: str) -> dict[str, str] | None:
    """The published post closest to ``text``, as ``{slug, title, similarity}``, or ``None``.

    ``text`` is the story's headline. Measured 2026-10-18 on five live
    stories: a real match scored 0.649 (a data-centre story against the
    data-centre post), while posts sharing only a name or a part scored
    0.58-0.61, hence ``news_shorts_related_post_min_similarity`` 0.63. Fails
    open: no link is better than no Short, and better than a wrong link.
    """
    floor = _sc_float(
        site_config, "news_shorts_related_post_min_similarity", DEFAULT_RELATED_MIN_SIMILARITY,
    )
    if floor <= 0 or pool is None or not (text or "").strip():
        return None
    from poindexter.utils.exception_format import describe_exception

    try:
        from poindexter.services.topic_ranking import embed_text

        vector = await embed_text(text[:2000], site_config=site_config)
        row = await pool.fetchrow(_RELATED_SQL, "[" + ",".join(f"{x:.6f}" for x in vector) + "]")
    except Exception as exc:  # noqa: BLE001 — a missing link must not cost the Short
        logger.warning("[news_shorts] related-post search failed: %s", describe_exception(exc))
        return None
    if row is None or row["cosine"] is None or float(row["cosine"]) < floor:
        return None
    return {
        "slug": str(row["slug"]),
        "title": str(row["title"] or ""),
        "similarity": f"{float(row['cosine']):.3f}",
    }


async def _niche_subject(pool: Any, slug: str) -> str:
    """The niche's ``topic_subject`` (who the channel is for), or ``""``."""
    if pool is None or not slug:
        return ""
    from poindexter.services.niche_service import NicheService

    try:
        niche = await NicheService(pool).get_by_slug(slug)
    except Exception as exc:  # noqa: BLE001 — the prompt falls back to a generic audience
        from poindexter.utils.exception_format import describe_exception

        logger.warning("[news_shorts] niche lookup failed for %r: %s", slug, describe_exception(exc))
        return ""
    return (niche.topic_subject or "").strip() if niche is not None else ""


async def research(
    pool: Any, site_config: Any, story: NewsStory, *, niche: str = "",
) -> dict[str, Any]:
    """The story's own page text, the related post and the niche's subject.

    Raises when the page is unreadable. The page is the whole corpus: a Short
    written from a headline and a feed summary has to invent everything else,
    so a page shorter than ``news_shorts_min_source_chars`` stops the run.
    """
    from poindexter.services.web_research import WebResearcher

    max_chars = max(1000, _sc_int(site_config, "news_shorts_source_max_chars", DEFAULT_SOURCE_MAX_CHARS))
    text = await WebResearcher(site_config=site_config).fetch_page_text(story.url, max_chars=max_chars)
    min_chars = max(0, _sc_int(site_config, "news_shorts_min_source_chars", DEFAULT_MIN_SOURCE_CHARS))
    if len(text) < min_chars:
        raise ValueError(
            f"news_short: the story page {story.url} gave {len(text)} readable "
            f"character(s), fewer than news_shorts_min_source_chars={min_chars}; "
            "a Short is not written from a headline alone"
        )
    related = await related_post(pool, site_config, story.title)
    subject = await _niche_subject(pool, niche or niche_slug(site_config))
    return {"source_text": text, "related_post": related, "subject": subject}


# ---------------------------------------------------------------------------
# The script
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_MARKUP_RE = re.compile(r"[#*`\[\]{}<>|]|^\s*[-•]\s", re.MULTILINE)
_LABEL_RE = re.compile(r"^\s*(?:narration|script|short|voiceover|vo)\s*:\s*", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


# A number as written: digits with optional thousands commas and decimals,
# not fused to a letter before it ("phi4", "H100" name a thing, not a count).
_NUMBER_RE = re.compile(r"(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?")
_NUMBER_WORDS = {
    w: str(i) for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)


def _number_tokens(text: str, *, spelled: bool = False) -> set[str]:
    """Every number in ``text``, normalised (``470,000`` -> ``470000``, ``2.0`` -> ``2``).

    ``spelled`` also reads ``zero`` to ``twenty`` as numbers, for the source:
    a page that says "two pallets" supports a script's "2 pallets".
    """
    if spelled:
        text = _NUMBER_WORD_RE.sub(lambda m: _NUMBER_WORDS[m.group(1).lower()], text)
    out: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group().rstrip(",.").replace(",", "")
        if "." in token:
            token = token.rstrip("0").rstrip(".")
        if token:
            out.add(token)
    return out


def _first_sentence(text: str) -> str:
    parts = _SENTENCE_RE.split(text.strip(), maxsplit=1)
    return parts[0] if parts else ""


def clean_script(
    raw: str, *, corpus: list[str], min_words: int, max_words: int,
) -> tuple[str, str]:
    """``(script, "")`` when ``raw`` can be narrated as is, else ``("", reason)``.

    The reason is what the corrective retry is told, so it names the fault
    (the numbers that are not in the source, the word count against the
    range) rather than restating the rules.
    """
    from poindexter.services.numeric_fidelity import verify
    from poindexter.services.short_hook import CONTENT_DEFECTS, hook_defects, strip_scaffold

    text = strip_scaffold((raw or "").strip())
    text = _LABEL_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "", "the reply was empty"
    if _URL_RE.search(text):
        return "", "it contains a link; the narration is spoken, so it has no URLs"
    if _MARKUP_RE.search(text) or _EMOJI_RE.search(text):
        return "", "it contains markdown, list markers or emoji; write plain spoken sentences"
    words = len(text.split())
    if words < min_words or words > max_words:
        return "", f"it is {words} words; it must be {min_words} to {max_words} words"
    if text[-1] not in ".!?":
        return "", "it stops mid-sentence; end on a complete sentence"
    defects = [d for d in hook_defects(_first_sentence(text), max_chars=400, max_words=60)
               if d in CONTENT_DEFECTS]
    if defects:
        return "", (
            "the first sentence is not a claim about the story "
            f"({', '.join(defects)}); open with the news itself"
        )
    # Every number, not only the ones numeric_fidelity recognises as a claim
    # (a percent, a currency, a number with a unit): "9 pallets" is a number
    # with no unit, and a script inventing it passed that check alone.
    unsourced = sorted(_number_tokens(text) - _number_tokens(" ".join(corpus), spelled=True))
    if unsourced:
        return "", (
            f"these numbers are not in the source: {', '.join(unsourced[:6])}; "
            "use only numbers the source states"
        )
    result = verify(text, corpus, score_unattributed=True)
    if result.unsupported:
        bad = ", ".join(v.claim.raw for v in result.unsupported[:6])
        return "", f"these numbers are not in the source: {bad}; use only numbers the source states"
    return text, ""


async def write_script(
    *, pool: Any, site_config: Any, story: NewsStory, source_text: str,
    subject: str = "", task_id: str | None = None,
) -> tuple[str, str]:
    """Write and check the narration. ``(script, note)``; ``script`` is ``""`` on failure."""
    from poindexter.services.checked_llm_line import generate_checked_line
    from poindexter.services.prompt_manager import get_prompt_manager

    model = _model(site_config)
    if not model:
        return "", "no news_shorts_script_model / video_director_model set"
    min_words, target_words, max_words = word_bounds(site_config)
    corpus = [story.title, story.summary, source_text]
    prompts = get_prompt_manager()
    return await generate_checked_line(
        pool=pool,
        model=model,
        prompt=prompts.get_prompt(
            "news_short.script", title=story.title, source=source_text,
            subject=subject or "technology news",
            target_words=target_words, min_words=min_words, max_words=max_words,
        ),
        check=lambda raw: clean_script(raw, corpus=corpus, min_words=min_words, max_words=max_words),
        fix_prompt=lambda reason: prompts.get_prompt(
            "news_short.script_fix", reason=reason, min_words=min_words, max_words=max_words,
        ),
        phase="news_short_script",
        temperature=_sc_float(site_config, "news_shorts_script_temperature", 0.5),
        max_tokens=max(128, _sc_int(site_config, "news_shorts_script_max_tokens", 600)),
        timeout_s=max(10.0, _sc_float(site_config, "news_shorts_script_timeout_seconds", 180.0)),
        disable_thinking=_sc_bool(site_config, "video_director_disable_thinking", True),
        label="news short script",
        task_id=task_id,
    )


@dataclass
class DraftResult:
    """A story taken through research and the script, with no render (``--draft``)."""

    story: NewsStory
    script: str = ""
    note: str = ""
    source_chars: int = 0
    related_post: dict[str, str] | None = None
    error: str = ""


async def draft(pool: Any, site_config: Any, story: NewsStory) -> DraftResult:
    """Research ``story`` and write its script, without queueing anything."""
    from poindexter.utils.exception_format import describe_exception

    out = DraftResult(story=story)
    try:
        found = await research(pool, site_config, story, niche=niche_slug(site_config))
    except Exception as exc:  # noqa: BLE001 — reported to the operator, not raised
        out.error = describe_exception(exc)
        return out
    out.source_chars = len(found["source_text"])
    out.related_post = found["related_post"]
    out.script, out.note = await write_script(
        pool=pool, site_config=site_config, story=story,
        source_text=found["source_text"], subject=found["subject"],
    )
    return out


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

# The Short's row, written by media.persist moments earlier. ``post_id IS NULL``
# keeps this from ever touching an article's media.
_MARK_SQL = """
    UPDATE media_assets
       SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
           title = $3,
           description = $4,
           updated_at = NOW()
     WHERE task_id::text = $1
       AND type = 'video_short'
       AND post_id IS NULL
    RETURNING id::text
"""

# The narration, where the existing Short readers look for it
# (short_package.load_short_context, media_distribute's hook fallback).
_SCRIPT_SQL = """
    UPDATE pipeline_versions
       SET stage_data = jsonb_set(
               COALESCE(stage_data, '{}'::jsonb), '{task_metadata,short_summary_script}',
               to_jsonb($2::text), true)
     WHERE task_id = $1
       AND version = (SELECT MAX(version) FROM pipeline_versions WHERE task_id = $1)
"""


def standalone_metadata(story: NewsStory, related: dict[str, str] | None) -> dict[str, Any]:
    """What finalize merges onto the Short's ``media_assets.metadata``."""
    meta: dict[str, Any] = {
        "standalone": True,
        METADATA_KEY: story.as_metadata(),
        SOURCE_URL_KEY: story.url,
    }
    if related and related.get("slug"):
        meta[RELATED_SLUG_KEY] = related["slug"]
        meta["related_post"] = related
    return meta


async def mark_standalone(
    pool: Any, task_id: str, story: NewsStory, *, related: dict[str, str] | None, script: str,
) -> list[str]:
    """Mark the task's Short standalone so it gets its own review. Returns the row ids."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _MARK_SQL, str(task_id), json.dumps(standalone_metadata(story, related)),
            story.title[:500], f"Source: {story.url}",
        )
        if script:
            await conn.execute(_SCRIPT_SQL, str(task_id), script)
    return [r["id"] for r in rows]


__all__ = [
    "METADATA_KEY",
    "RELATED_SLUG_KEY",
    "SOURCE_URL_KEY",
    "TASK_TYPE",
    "TEMPLATE_SLUG",
    "DraftResult",
    "NewsStory",
    "PickResult",
    "candidate_stories",
    "clean_script",
    "draft",
    "enabled",
    "enqueue_news_short",
    "made_since",
    "mark_standalone",
    "niche_slug",
    "pick_story",
    "related_post",
    "research",
    "sources",
    "standalone_metadata",
    "word_bounds",
    "write_script",
]
