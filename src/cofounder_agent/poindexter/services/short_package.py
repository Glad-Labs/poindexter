"""A YouTube Short's own title and description lead, written as a title and checked in code.

Earned 2026-10-13. Since glad-labs-stack#3944 a Short was titled with the
first sentence of its narration, shortened to fit. That sentence is written to
be SPOKEN, and the live channel showed what a spoken line looks like as a
title:

    "DeepSeek and Huawei's open-source tools for Ascend chips signify a #Shorts"
    "On June 19th, a GPU lock bug was quietly wreaking havoc on our #Shorts"
    "Three hundred fifty days have passed since OpenAI's last #Shorts"

Cut mid-phrase by the length budget, or carrying the TTS spelling of a number
("three hundred fifty") because narration is normalised for the speech
engine. The description opened with the same spoken text. The long video, by
contrast, publishes a title the writer drafted as a title and that code checked
for searchability, and a thumbnail whose words code checks.

This module gives the Short the same treatment:

- **Title.** The director model writes a title from the ``video.short_title``
  prompt (``skills/content/video-director/SKILL.md``). :func:`clean_short_title`
  checks it: it fits WHOLE in ``youtube_short_title_max_chars`` (never cut,
  because a cut title is the defect this replaces); every number in it appears
  in the post or the narration; it names something a viewer would search for
  (``services/title_searchability.py``, the long title's own rule); it does not
  open by describing the video or with a run-up (``services/short_hook.py``);
  and it is not a restatement of the long video's title, since both sit on one
  channel.
- **Description lead.** One or two sentences under the title, from the
  ``video.short_description`` prompt, with the same number and opener checks.
  The links and hashtags are added after it by ``youtube_payload``.

Each gets one corrective retry carrying the rejection reason
(``services/checked_llm_line.py``). A line that fails twice is left empty, and
the upload falls back to the narration-derived title and opener, which is what
every Short shipped with before. The package is written at render time by the
``media.package_short`` node and stored on the Short's ``video_short`` asset
row as ``metadata.youtube_package``, so the operator reviews it in the media
drawer with the video, and what is approved is what is uploaded.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from poindexter.services.logger_config import get_logger
from poindexter.services.short_hook import hook_defects, strip_preamble, strip_scaffold
from poindexter.services.title_searchability import has_searchable_entity

logger = get_logger(__name__)

#: ``media_assets.metadata`` key the package is stored under (on the
#: ``video_short`` row).
PACKAGE_METADATA_KEY = "youtube_package"

TITLE_MAX_CHARS_DEFAULT = 70
TITLE_MAX_WORDS_DEFAULT = 10
#: Two sentences. 220 was measured too tight on 2026-10-13: over the 10 live
#: Shorts, two good leads were rejected twice at 222 and 224 characters, and a
#: description has no display limit near this; only the first lines show
#: before "more".
DESCRIPTION_MAX_CHARS_DEFAULT = 300

_NUMBER_RE = re.compile(r"\d[\d,.]*")
_HASHTAG_RE = re.compile(r"(?:^|\s)#\w+")
_URL_RE = re.compile(r"https?://|www\.", re.I)
#: Labels a model puts before its answer. Only these are stripped: a short
#: word before a colon is usually the title's own subject ("RAG: …").
_LABELS = frozenset({
    "title", "short title", "youtube title", "headline", "description",
    "short description", "youtube description", "output", "answer",
})
#: Replies that restate the request instead of answering it.
_ECHO_STARTS = ("here", "sure", "certainly", "okay", "goal", "task", "write")
#: Short-hook defects that make a line a poor TITLE too. Deliberately not the
#: whole set: a question is a fair title, and a noun phrase is a title, not
#: the "not a claim" a spoken opener must avoid.
_TITLE_DEFECTS = {
    "describes_article": "it describes the video instead of naming its subject",
    "run_up": "it opens with a run-up clause; open with the subject",
    "fragment": "it is under three words",
    "restates_title": "it repeats the full video's title; the channel already has that title",
}


def _sc(site_config: Any, key: str, default: Any) -> Any:
    if site_config is None:
        return default
    try:
        value = site_config.get(key, default)
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not break a render
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


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _first_line(raw: str) -> str:
    for candidate in (raw or "").splitlines():
        if candidate.strip():
            return candidate.strip()
    return ""


def _drop_label(line: str) -> str:
    head, sep, tail = line.partition(":")
    if sep and head.strip().strip("\"'`*#").lower() in _LABELS:
        return tail.strip()
    return line


def _ungrounded_number(text: str, haystack: str) -> str:
    """The first number in ``text`` that ``haystack`` does not contain, else ``""``."""
    hay = haystack.replace(",", "")
    for number in _NUMBER_RE.findall(text):
        bare = number.replace(",", "").rstrip(".")
        if bare and bare not in hay:
            return number
    return ""


def clean_short_title(
    raw: str,
    *,
    long_title: str,
    source_text: str,
    max_chars: int = TITLE_MAX_CHARS_DEFAULT,
    keywords: list[str] | tuple[str, ...] = (),
) -> tuple[str, str]:
    """Reduce a model reply to a Short title. ``(title, "")`` or ``("", reason)``.

    Wrapper the model put around the line (a label, quotes, emphasis, a
    hashtag) is removed, and so is a run-up that leaves a whole title behind;
    what remains is judged, never shortened. ``source_text`` is everything the
    title may draw a number from (the post and the Short's narration).
    """
    line = strip_scaffold(_drop_label(_first_line(raw)))
    line = _HASHTAG_RE.sub(" ", line)
    title = " ".join(line.split()).strip().rstrip(".").strip()
    title = strip_preamble(title).rstrip(".").strip()
    if not title:
        return "", "empty reply"
    if title.split()[0].lower().strip(",:") in _ECHO_STARTS:
        return "", f"instruction echo: {title[:60]!r}"
    if len(title) > max_chars:
        return "", f"{len(title)} characters; the limit is {max_chars}"
    defects = hook_defects(title, max_chars=max_chars, max_words=10_000, article_title=long_title)
    for defect, why in _TITLE_DEFECTS.items():
        if defect in defects:
            return "", why
    number = _ungrounded_number(title, f"{long_title} {source_text}")
    if number:
        return "", f"number {number!r} is not in the post or the Short"
    report = has_searchable_entity(title, tags=list(keywords))
    if not report.ok:
        return "", (
            "it names nothing a viewer would search for; name the model, product, "
            "company or number the Short is about"
        )
    return title, ""


def clean_short_description(
    raw: str,
    *,
    title: str,
    source_text: str,
    max_chars: int = DESCRIPTION_MAX_CHARS_DEFAULT,
) -> tuple[str, str]:
    """Reduce a model reply to a description lead. ``(text, "")`` or ``("", reason)``.

    One or two sentences. The links and hashtags are composed after it, so a
    reply carrying either is sent back rather than doubled up.
    """
    lines = [strip_scaffold(_drop_label(ln.strip())) for ln in (raw or "").splitlines() if ln.strip()]
    text = " ".join(" ".join(lines).split()).strip()
    if not text:
        return "", "empty reply"
    if text.split()[0].lower().strip(",:") in _ECHO_STARTS:
        return "", f"instruction echo: {text[:60]!r}"
    if _URL_RE.search(text):
        return "", "it carries a link; the links are added after it"
    if _HASHTAG_RE.search(text):
        return "", "it carries a hashtag; the hashtags are added after it"
    if len(text) > max_chars:
        return "", f"{len(text)} characters; the limit is {max_chars}"
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    # Judged on the RAW opener: a title is published after its run-up is
    # stripped, but this text is published as written, so a run-up or a
    # describes-the-video prefix that a strip could remove is still a defect.
    defects = hook_defects(first, max_chars=10_000, max_words=10_000)
    if "describes_article" in defects or strip_preamble(first) != strip_scaffold(first):
        return "", "it opens with a run-up or by describing the video; open with the point itself"
    if title and " ".join(text.lower().split()).rstrip(".") == " ".join(title.lower().split()):
        return "", "it repeats the title"
    number = _ungrounded_number(text, f"{title} {source_text}")
    if number:
        return "", f"number {number!r} is not in the post or the Short"
    return text, ""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShortPackage:
    """What a Short is published under. Empty fields fall back at upload."""

    title: str = ""
    title_note: str = ""
    description: str = ""
    description_note: str = ""
    model: str = ""
    composed_at: str = ""

    def as_metadata(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_metadata(cls, raw: Any) -> ShortPackage | None:
        if not isinstance(raw, dict):
            return None
        fields = cls.__dataclass_fields__
        return cls(**{k: str(raw.get(k) or "") for k in fields})


def _model(site_config: Any) -> str:
    return str(
        _sc(site_config, "youtube_short_package_model", "")
        or _sc(site_config, "video_thumbnail_hook_model", "")
        or _sc(site_config, "video_director_model", "")
    ).strip()


async def generate_short_package(
    *,
    site_config: Any,
    pool: Any,
    script: str,
    long_title: str,
    summary: str,
    content: str = "",
    keywords: list[str] | tuple[str, ...] = (),
    task_id: str | None = None,
) -> ShortPackage:
    """Write and check the Short's title, then its description lead.

    Never raises. Off (``youtube_short_package_enabled=false``), no model, or
    no narration gives an empty package with the reason in ``title_note``, and
    the upload keeps the narration-derived title as before.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not _sc_bool(site_config, "youtube_short_package_enabled", True):
        return ShortPackage(title_note="youtube_short_package_enabled is off", composed_at=stamp)
    model = _model(site_config)
    if not model:
        return ShortPackage(
            title_note="no youtube_short_package_model / video_director_model set", composed_at=stamp,
        )
    if not (script or "").strip():
        return ShortPackage(title_note="the Short has no narration to title", composed_at=stamp)
    if site_config is None or pool is None:
        return ShortPackage(title_note="no site_config or pool", composed_at=stamp)

    from poindexter.services.checked_llm_line import generate_checked_line
    from poindexter.services.prompt_manager import get_prompt_manager

    title_max = max(20, _sc_int(site_config, "youtube_short_title_max_chars", TITLE_MAX_CHARS_DEFAULT))
    title_words = max(3, _sc_int(site_config, "youtube_short_title_max_words", TITLE_MAX_WORDS_DEFAULT))
    desc_max = max(60, _sc_int(site_config, "youtube_short_description_max_chars", DESCRIPTION_MAX_CHARS_DEFAULT))
    call = {
        "pool": pool,
        "model": model,
        "temperature": _sc_float(site_config, "youtube_short_package_temperature", 0.6),
        "max_tokens": max(32, _sc_int(site_config, "youtube_short_package_max_tokens", 256)),
        "timeout_s": max(5.0, _sc_float(site_config, "youtube_short_package_timeout_seconds", 90.0)),
        "disable_thinking": _sc_bool(site_config, "video_director_disable_thinking", True),
        "task_id": task_id,
    }
    source_text = f"{summary} {content} {script}"
    prompts = get_prompt_manager()
    summary_line = summary or "(no summary)"

    title, title_note = await generate_checked_line(
        prompt=prompts.get_prompt(
            "video.short_title", long_title=long_title or "(none)", summary=summary_line,
            script=script, max_words=title_words,
        ),
        check=lambda raw: clean_short_title(
            raw, long_title=long_title, source_text=source_text, max_chars=title_max,
            keywords=keywords,
        ),
        fix_prompt=lambda reason: prompts.get_prompt(
            "video.short_title_fix", reason=reason, max_chars=title_max, max_words=title_words,
        ),
        phase="youtube_short_title",
        label="short title",
        **call,
    )
    description, description_note = await generate_checked_line(
        prompt=prompts.get_prompt(
            "video.short_description", title=title or long_title or "(none)",
            summary=summary_line, script=script,
        ),
        check=lambda raw: clean_short_description(
            raw, title=title, source_text=f"{long_title} {source_text}", max_chars=desc_max,
        ),
        fix_prompt=lambda reason: prompts.get_prompt(
            "video.short_description_fix", reason=reason, max_chars=desc_max,
        ),
        phase="youtube_short_description",
        label="short description",
        **call,
    )
    package = ShortPackage(
        title=title, title_note=title_note, description=description,
        description_note=description_note, model=model, composed_at=stamp,
    )
    logger.info(
        "[short_package] task %s: title %r (%s), description %d chars (%s)",
        task_id, title, title_note or "ok", len(description), description_note or "ok",
    )
    return package


# ---------------------------------------------------------------------------
# Context + storage
# ---------------------------------------------------------------------------

_CONTEXT_SQL = """
    SELECT p.title, p.excerpt, p.content, p.seo_keywords::text AS seo_keywords
      FROM posts p
     WHERE p.metadata->>'pipeline_task_id' = $1
     ORDER BY p.created_at DESC
     LIMIT 1
"""

_SCRIPT_SQL = """
    SELECT v.stage_data -> 'task_metadata' ->> 'short_summary_script'
      FROM pipeline_versions v
     WHERE v.task_id = $1
     ORDER BY v.version DESC
     LIMIT 1
"""

_LOAD_SQL = """
    SELECT metadata -> 'youtube_package'
      FROM media_assets
     WHERE task_id::text = $1 AND type = 'video_short'
     ORDER BY created_at DESC
     LIMIT 1
"""

_STORE_SQL = """
    UPDATE media_assets
       SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('youtube_package', $2::jsonb),
           updated_at = NOW()
     WHERE task_id::text = $1 AND type = 'video_short'
"""


async def load_short_context(pool: Any, task_id: str) -> dict[str, Any]:
    """The post fields and stored narration a package is written from (``""`` when unknown)."""
    ctx: dict[str, Any] = {"title": "", "excerpt": "", "content": "", "keywords": [], "script": ""}
    if pool is None or not task_id:
        return ctx
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_CONTEXT_SQL, str(task_id))
            script = await conn.fetchval(_SCRIPT_SQL, str(task_id))
    except Exception as exc:  # noqa: BLE001 — a package is a refinement; the upload has its fallback
        from poindexter.utils.exception_format import describe_exception

        logger.warning("[short_package] context lookup failed for %s: %s", task_id, describe_exception(exc))
        return ctx
    if row is not None:
        ctx.update(
            title=str(row["title"] or ""), excerpt=str(row["excerpt"] or ""),
            content=str(row["content"] or ""),
            keywords=[k.strip() for k in str(row["seo_keywords"] or "").split(",") if k.strip()],
        )
    ctx["script"] = script if isinstance(script, str) else ""
    return ctx


async def compose_short_package(
    *, pool: Any, site_config: Any, task_id: str, script: str | None = None,
) -> ShortPackage:
    """Load the Short's context and write its package. ``script`` overrides the stored narration."""
    ctx = await load_short_context(pool, task_id)
    return await generate_short_package(
        site_config=site_config, pool=pool,
        script=script if script is not None else ctx["script"],
        long_title=ctx["title"], summary=ctx["excerpt"], content=ctx["content"],
        keywords=ctx["keywords"], task_id=task_id,
    )


async def load_short_package(pool: Any, task_id: str | None) -> ShortPackage | None:
    """The stored package of ``task_id``'s Short, or ``None``."""
    if pool is None or not task_id:
        return None
    try:
        raw = await pool.fetchval(_LOAD_SQL, str(task_id))
    except Exception as exc:  # noqa: BLE001 — no package means the narration fallback
        from poindexter.utils.exception_format import describe_exception

        logger.warning("[short_package] package lookup failed for %s: %s", task_id, describe_exception(exc))
        return None
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return ShortPackage.from_metadata(raw)


async def store_short_package(pool: Any, task_id: str, package: ShortPackage) -> bool:
    """Write ``package`` onto the task's ``video_short`` row. ``True`` if a row took it."""
    import json

    try:
        result = await pool.execute(_STORE_SQL, str(task_id), json.dumps(package.as_metadata()))
    except Exception as exc:  # noqa: BLE001
        from poindexter.utils.exception_format import describe_exception

        logger.warning("[short_package] store failed for %s: %s", task_id, describe_exception(exc))
        return False
    return not str(result or "").endswith(" 0")


# ---------------------------------------------------------------------------
# Backfill: Shorts rendered before packages existed
# ---------------------------------------------------------------------------

# One row per task (a re-rendered task holds several ``video_short`` rows): the
# one on YouTube if any, else the latest. A Short is included when it is on
# YouTube or awaiting approval (it then uploads with the package); $1 = include
# every Short, which only a single-Short selection asks for.
_SHORTS_SQL = """
SELECT * FROM (
  SELECT DISTINCT ON (ma.task_id)
         ma.task_id::text AS task_id,
         COALESCE(ma.platform_video_ids->>'youtube', '') AS video_id,
         p.id::text AS post_id, p.slug, p.title,
         ma.metadata->'youtube_package' AS youtube_package,
         ma.created_at
    FROM media_assets ma
    LEFT JOIN posts p ON p.metadata->>'pipeline_task_id' = ma.task_id::text
   WHERE ma.type = 'video_short' AND ma.task_id IS NOT NULL
     AND ($1::boolean
          OR COALESCE(ma.platform_video_ids->>'youtube', '') <> ''
          OR EXISTS (
               SELECT 1 FROM media_approvals a
                WHERE a.medium = 'video_short' AND a.status = 'pending'
                  AND (a.post_id = ma.post_id OR a.post_id = p.id OR a.task_id = ma.task_id::text)
          ))
   ORDER BY ma.task_id,
            (COALESCE(ma.platform_video_ids->>'youtube', '') <> '') DESC,
            ma.created_at DESC
) v
ORDER BY created_at DESC
"""


@dataclass(frozen=True)
class PackageOutcome:
    video_id: str
    task_id: str
    post_title: str
    action: str
    package: ShortPackage | None = None
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


async def backfill_short_packages(
    pool: Any,
    site_config: Any,
    *,
    selector: str | None = None,
    recompose: bool = False,
    limit: int | None = None,
    title: str | None = None,
    description: str | None = None,
) -> list[PackageOutcome]:
    """Write and store a package for each Short that has none. Never sends anything.

    Covers every Short on YouTube or awaiting approval; ``selector`` reaches
    any one Short.

    A LOCAL write, like the thumbnail dry run: the operator reads the result,
    then ``poindexter integrations youtube sync-metadata --apply`` pushes it,
    and a Short still awaiting approval uploads with it. ``recompose`` re-rolls
    stored packages. ``title`` / ``description`` set ONE selected Short's text
    by hand (no model call, no checks, ``title_note`` "set by operator"); the
    other field keeps its stored value. Raises ``ValueError`` when the hand
    text would not land on exactly one Short.
    """
    title = (title or "").strip() or None
    description = (description or "").strip() or None
    rows = [dict(r) for r in await pool.fetch(_SHORTS_SQL, bool(selector))]
    if selector:
        rows = [r for r in rows if selector in (r.get("post_id"), r.get("task_id"), r.get("video_id"), r.get("slug"))]
    manual = title is not None or description is not None
    if manual and len(rows) != 1:
        raise ValueError(f"--title/--description need --post naming exactly one Short; {len(rows)} matched")
    if limit is not None:
        rows = rows[: max(0, limit)]

    outcomes: list[PackageOutcome] = []
    for row in rows:
        task_id, video_id = str(row["task_id"]), str(row.get("video_id") or "")
        post_title = str(row.get("title") or task_id)
        raw = row.get("youtube_package")
        if isinstance(raw, str):
            import json

            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        stored = ShortPackage.from_metadata(raw)
        if manual:
            base = stored or ShortPackage()
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            package = ShortPackage(
                title=title if title is not None else base.title,
                title_note="set by operator" if title is not None else base.title_note,
                description=description if description is not None else base.description,
                description_note="set by operator" if description is not None else base.description_note,
                model=base.model, composed_at=stamp,
            )
        elif stored is not None and stored.title and not recompose:
            outcomes.append(PackageOutcome(video_id, task_id, post_title, "stored", stored))
            continue
        else:
            package = await compose_short_package(pool=pool, site_config=site_config, task_id=task_id)
        if not await store_short_package(pool, task_id, package):
            outcomes.append(PackageOutcome(
                video_id, task_id, post_title, "store failed", package, "no video_short row took the package",
            ))
            continue
        if not package.title:
            action = "no title (the Short keeps its narration title)"
        elif video_id:
            action = "composed — review, then sync-metadata --apply"
        else:
            action = "composed — uploads with the Short when you approve it"
        outcomes.append(PackageOutcome(video_id, task_id, post_title, action, package))
    return outcomes


__all__ = [
    "DESCRIPTION_MAX_CHARS_DEFAULT",
    "PACKAGE_METADATA_KEY",
    "PackageOutcome",
    "ShortPackage",
    "TITLE_MAX_CHARS_DEFAULT",
    "TITLE_MAX_WORDS_DEFAULT",
    "backfill_short_packages",
    "clean_short_description",
    "clean_short_title",
    "compose_short_package",
    "generate_short_package",
    "load_short_context",
    "load_short_package",
    "store_short_package",
]
