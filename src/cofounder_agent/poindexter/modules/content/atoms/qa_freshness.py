"""qa.freshness — is a news-shaped draft still timely when it reaches QA?

The gap (2026-09-14 queue review): a reaction to OpenAI's September 8
announcement reached ``awaiting_approval`` on the 11th reading "OpenAI put out
a paper this week", and was still there on the 14th. Every truth rail passed
it — the claims were right — because none of them knows what day it is. A
stale news take is not a rewrite problem: the words can be made current, the
piece cannot, so this rail vetoes and the veto is deliberately NON-rescuable
(``_qa_rail_common._NON_TEXT_FIXABLE_PROVIDERS``): publish today or drop it.

Two signals, both deterministic:

1. **Relative-time phrasing** in the draft — "this week", "yesterday", "earlier
   today", "just announced", "hours ago", "breaking" — the writer anchoring
   the piece to a moment.
2. **Event age** — the newest dated source line in ``research_context``
   ("Sep 8, 2026", "8 September 2026", "2026-09-08"), else the task's
   ``created_at``. Days from that to now, compared with
   ``qa_freshness_max_age_days``.

A draft is *news-shaped* when it carries relative-time phrasing or its topic
came from a news source (``pipeline_tasks.metadata->>'discovered_by'`` in
``qa_freshness_news_sources``). News-shaped AND older than the cap → veto.
News-shaped and within the cap → an approving review (it is checkable and it
passed). Not news-shaped → no review at all, so evergreen posts never pay for
a rail they do not need. No dates anywhere → no review either; the rail never
guesses an age.

Gate status is DB-driven (``qa_gates.freshness.required_to_pass``, seeded
true by migration ``20260915_0202``); the poindexter#454 lever demotes it.
Master switch ``qa_freshness_enabled`` (default true).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from poindexter.modules.content.atoms._pool import resolve_pool
from poindexter.modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from poindexter.plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.freshness",
    type="atom",
    version="1.0.0",
    description=(
        "News-shaped drafts (relative-time phrasing, or a topic from a news "
        "source) must reach QA within qa_freshness_max_age_days of their newest "
        "dated source; a stale one is vetoed and the veto is not rescuable — "
        "a rewrite cannot make a late take current. Evergreen drafts get no "
        "review. Gate status DB-driven via qa_gates.freshness."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(
            name="research_context",
            type="str",
            description="research corpus with dated source lines",
            required=False,
        ),
    ),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="freshness review"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,  # pure string ops + one DB read
    cost_class="free",
    idempotent=True,
    side_effects=("one read-only pipeline_tasks lookup when a pool is available",),
    parallelizable=True,
)

DEFAULT_MAX_AGE_DAYS = 5
DEFAULT_NEWS_SOURCES = "rss,hacker_news,hn,google_news,news,reddit,search_autocomplete"

# The moment-anchoring phrases. Kept to unambiguous ones: "recently" and
# "now" are too common in evergreen prose to count.
_RELATIVE_RE = re.compile(
    r"\b(?:this\s+week|earlier\s+this\s+week|later\s+this\s+week|this\s+morning|"
    r"this\s+afternoon|this\s+evening|tonight|last\s+night|yesterday|earlier\s+today|"
    r"today['’]s\s+(?:announcement|news|release|paper|launch)|just\s+(?:announced|released|"
    r"shipped|dropped|published|landed)|hours\s+ago|(?:a\s+few|two|three|\d+)\s+days\s+ago|"
    r"over\s+the\s+weekend|this\s+weekend|breaking(?:\s+news)?|as\s+of\s+this\s+(?:morning|writing))\b",
    re.IGNORECASE,
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_RES = (
    re.compile(
        rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE
    ),  # Sep 8, 2026
    re.compile(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+(\d{{4}})\b", re.IGNORECASE
    ),  # 8 September 2026
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),  # 2026-09-08
)


def _today() -> date:
    """UTC calendar date; a seam so tests can pin the clock."""
    return datetime.now(timezone.utc).date()


def find_relative_phrases(content: str) -> list[str]:
    seen: list[str] = []
    for m in _RELATIVE_RE.finditer(content or ""):
        phrase = re.sub(r"\s+", " ", m.group(0).lower())
        if phrase not in seen:
            seen.append(phrase)
    return seen


def newest_source_date(text: str, *, today: date | None = None) -> date | None:
    """The newest calendar date named in ``text`` that is not in the future."""
    today = today or _today()
    best: date | None = None
    for rx in _DATE_RES:
        for m in rx.finditer(text or ""):
            g = m.groups()
            try:
                if rx is _DATE_RES[2]:
                    d = date(int(g[0]), int(g[1]), int(g[2]))
                elif rx is _DATE_RES[0]:
                    d = date(int(g[2]), _MONTHS[g[0][:3].lower()], int(g[1]))
                else:
                    d = date(int(g[2]), _MONTHS[g[1][:3].lower()], int(g[0]))
            except (ValueError, KeyError):
                continue
            if d > today + timedelta(days=1):
                continue
            if best is None or d > best:
                best = d
    return best


def _csv(raw: Any) -> set[str]:
    return {p.strip().lower() for p in str(raw or "").split(",") if p.strip()}


def _read(site_config: Any, key: str, default: Any) -> Any:
    try:
        val = site_config.get(key, default)
    except Exception as exc:  # noqa: BLE001 — a stubbed config must not sink the rail
        logger.warning("[qa.freshness] could not read %s: %s — using %r", key, exc, default)
        return default
    return default if val in (None, "") else val


# pipeline_tasks has no metadata column. A task's metadata (discovered_by,
# research_context, …) is persisted by tasks_db.add_task into
# pipeline_versions.stage_data->'task_metadata' at version 1, so it is there
# from creation — before QA runs. The first shipped query read
# pipeline_tasks.metadata and failed on every run ("task lookup skipped
# (reduced coverage)"), silently dropping the news-source signal and the
# created_at fallback; test_freshness_task_facts_sql executes this against
# the real schema so that cannot recur.
_TASK_FACTS_SQL = """
    SELECT pt.created_at,
           (SELECT pv.stage_data -> 'task_metadata' ->> 'discovered_by'
              FROM pipeline_versions pv
             WHERE pv.task_id = pt.task_id
             ORDER BY pv.version DESC
             LIMIT 1) AS discovered_by
      FROM pipeline_tasks pt
     WHERE pt.task_id = $1
"""


async def _task_facts(pool: Any, task_id: str) -> tuple[date | None, str]:
    """``(created_at date, discovered_by)`` from pipeline_tasks, or ``(None, "")``."""
    if pool is None or not task_id:
        return None, ""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_TASK_FACTS_SQL, str(task_id))
    except Exception as exc:  # noqa: BLE001 — DB layer is optional coverage
        logger.warning("[qa.freshness] task lookup skipped (reduced coverage): %s", exc)
        return None, ""
    if not row:
        return None, ""
    created = row["created_at"]
    created_date = created.date() if hasattr(created, "date") else None
    return created_date, str(row["discovered_by"] or "").strip().lower()


def assess(
    *,
    content: str,
    research_context: str,
    discovered_by: str,
    created_at: date | None,
    news_sources: set[str],
    max_age_days: int,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Pure verdict. ``None`` = not news-shaped or no age to judge → no review."""
    today = today or _today()
    phrases = find_relative_phrases(content)
    from_news_source = bool(discovered_by) and discovered_by in news_sources
    if not phrases and not from_news_source:
        return None
    event = newest_source_date(research_context, today=today) or created_at
    if event is None:
        return None
    age = (today - event).days
    stale = age > max_age_days
    basis = (
        "newest dated source"
        if newest_source_date(research_context, today=today)
        else "task created_at"
    )
    return {
        "stale": stale,
        "age_days": age,
        "event_date": event.isoformat(),
        "basis": basis,
        "phrases": phrases,
        "from_news_source": from_news_source,
    }


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}
    if str(_read(site_config, "qa_freshness_enabled", "true")).lower() not in ("true", "1", "yes"):
        return {}
    try:
        max_age = int(_read(site_config, "qa_freshness_max_age_days", DEFAULT_MAX_AGE_DAYS))
    except (TypeError, ValueError):
        max_age = DEFAULT_MAX_AGE_DAYS
    news_sources = _csv(_read(site_config, "qa_freshness_news_sources", DEFAULT_NEWS_SOURCES))

    pool = resolve_pool(state, atom="qa.freshness")
    created_at, discovered_by = await _task_facts(pool, str(state.get("task_id") or ""))
    verdict = assess(
        content=content,
        research_context=str(state.get("research_context") or ""),
        discovered_by=discovered_by,
        created_at=created_at,
        news_sources=news_sources,
        max_age_days=max_age,
    )
    if verdict is None:
        return {}

    from poindexter.modules.content.multi_model_qa import MultiModelQA, ReviewerResult

    why = (
        f"{verdict['age_days']} day(s) behind its {verdict['basis']} ({verdict['event_date']}); "
        f"cap qa_freshness_max_age_days={max_age}"
    )
    anchors = ", ".join(verdict["phrases"][:4]) or "topic from a news source"
    if verdict["stale"]:
        feedback = (
            f"Stale news take: {why}. The draft anchors itself to a moment "
            f"({anchors}) that has passed — publish today or drop it; a rewrite "
            "cannot make it current."
        )
        logger.info("[qa.freshness] stale (task=%s): %s", str(state.get("task_id") or "?")[:8], why)
    else:
        feedback = f"Timely: {why}; anchors: {anchors}."
    review = ReviewerResult(
        reviewer="freshness",
        approved=not verdict["stale"],
        score=0.0 if verdict["stale"] else 100.0,
        feedback=feedback,
        provider="freshness",
    )
    qa = MultiModelQA(
        pool=pool,
        settings_service=state.get("settings_service"),
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "freshness")
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "assess", "find_relative_phrases", "newest_source_date", "run"]
