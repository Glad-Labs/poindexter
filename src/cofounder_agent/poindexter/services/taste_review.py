"""Advisory taste review: would the operator approve this draft?

A post that reaches ``awaiting_approval`` has passed every QA rail, and the
operator still rejects a good share of them, for reasons a score cannot see: off
topic, dull, a theme the blog has already covered, someone else's work told as
our own. On glad-labs the auto-publish gate passed 24 drafts in 90 days and the
operator rejected 9 (2026-10-17), so the gate cannot go live, and the
operator's review time became the publishing bottleneck.

This review reads the draft against the operator's own rubric (written from
their rejection reasons, stored in ``taste_review_rubric``) and gives a verdict and a
one-line reason. It is ADVISORY: the verdict rides in the awaiting-approval
message so a review takes seconds, and it is written to ``audit_log``
(``operator_taste_review``) so :func:`agreement` can compare it with what the
operator then decided. Nothing is approved or rejected by it. Measured before
it shipped: a local 31B judge was no better than the score gate (11 of its 32
passes were drafts the operator rejected), and Claude Sonnet caught all 20 reasoned
rejections in the held-out half but passed only 4 of 20 approvals. Live
agreement on current drafts decides whether it ever gates.

Off unless the draft's niche is listed in ``taste_review_niches`` and a rubric
is set. A failed call returns ``None`` and the notification goes out without it.

:func:`screen_topics` applies the same rubric one step earlier, to topic ideas
on the sweep's shortlist, so an idea that can only become a repeat or a recap
of other people's work is dropped before a draft is written for it. Both
drafts awaiting review on 2026-10-18 were exactly that (a roundup of four
outside laptop reviews; a near-duplicate of a live GPU ranking). Verdicts are
cached per idea, because the open batch refreshes every 30 minutes and
re-judging the same ten titles 29 times a day would cost more than the drafts.
A failed screen keeps every idea.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

EVENT_TYPE = "operator_taste_review"
SCREEN_EVENT = "topic_taste_screen"
_SOURCE = "services.taste_review"
_IMAGE_RE = re.compile(r"!\[|\[IMAGE|<img", re.IGNORECASE)


def _csv(value: Any) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(site_config.get(key, default))
    except (TypeError, ValueError):
        return default


async def review_draft(*, pool: Any, site_config: Any, task_id: str) -> dict[str, str] | None:
    """The verdict on one awaiting draft, recorded to audit_log, or ``None``.

    ``{"verdict": "approve" | "reject", "reason": str, "model": str}``.
    """
    if pool is None or site_config is None:
        return None
    row = await pool.fetchrow(
        """
        SELECT t.niche_slug, t.topic, t.created_at, n.topic_subject, v.title, v.content
          FROM pipeline_tasks t
          LEFT JOIN niches n ON n.slug = t.niche_slug
          LEFT JOIN LATERAL (
                SELECT title, content FROM pipeline_versions pv
                 WHERE pv.task_id::text = t.task_id::text
                 ORDER BY version DESC LIMIT 1) v ON true
         WHERE t.task_id::text = $1
        """,
        str(task_id),
    )
    if row is None or row["niche_slug"] not in _csv(site_config.get("taste_review_niches", "")):
        return None
    rubric = str(site_config.get("taste_review_rubric", "") or "").strip()
    if not rubric:
        logger.warning(
            "[TASTE] niche %r is listed in taste_review_niches but taste_review_rubric "
            "is empty; no review for %s", row["niche_slug"], task_id,
        )
        return None
    content = str(row["content"] or "")
    if not content.strip():
        logger.warning("[TASTE] task %s has no draft content; no review", task_id)
        return None

    recent = await _recent_titles(pool, site_config, row["niche_slug"])

    from poindexter.services.llm_text import ollama_chat_text, resolve_local_writer_model
    from poindexter.services.prompt_manager import get_prompt_manager
    from poindexter.utils.json_extract import extract_json_object

    model = str(site_config.get("taste_review_model", "") or "").strip() \
        or resolve_local_writer_model(site_config=site_config)
    prompt = get_prompt_manager().get_prompt(
        "qa.operator_taste",
        subject=row["topic_subject"] or "(no subject set for this niche)",
        rubric=rubric,
        today=row["created_at"].date().isoformat() if row["created_at"] else "",
        recent="\n".join(f"- {t}" for t in recent) or "(none yet)",
        title=row["title"] or row["topic"] or "",
        words=len(content.split()),
        images=len(_IMAGE_RE.findall(content)),
        body=content,
    )
    try:
        raw = await ollama_chat_text(
            prompt, model=model, site_config=site_config, pool=pool, task_id=str(task_id),
            phase="qa_operator_taste", timeout_setting="taste_review_timeout_seconds",
            timeout_default=180.0, think=False,
            max_tokens=_int(site_config, "taste_review_max_tokens", 6000),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TASTE] review call failed for %s (non-critical): %s", task_id, exc)
        return None
    parsed = extract_json_object(raw or "") or {}
    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in ("approve", "reject"):
        logger.warning("[TASTE] no verdict for %s: %r", task_id, (raw or "")[:200])
        return None
    review = {"verdict": verdict, "reason": str(parsed.get("reason", "")).strip(), "model": model}

    from poindexter.services.audit_log import AuditLogger

    await AuditLogger(pool).log(
        EVENT_TYPE, _SOURCE, {**review, "niche": row["niche_slug"]}, task_id=str(task_id),
    )
    return review


async def _recent_titles(pool: Any, site_config: Any, niche_slug: str) -> list[str]:
    rows = await pool.fetch(
        """
        SELECT p.title FROM posts p
          JOIN pipeline_tasks t ON t.task_id::text = p.metadata->>'pipeline_task_id'
         WHERE p.status = 'published' AND t.niche_slug = $1
         ORDER BY p.published_at DESC NULLS LAST LIMIT $2
        """,
        niche_slug, _int(site_config, "taste_review_recent_titles", 40),
    )
    return [r["title"] for r in rows if r["title"]]


def _screen_key(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


async def screen_topics(
    *, pool: Any, site_config: Any, niche_slug: str, subject: str | None,
    candidates: list[tuple[str, str, str]], today: str = "",
) -> dict[str, str]:
    """``{candidate id: reason}`` for the ideas the operator's rubric drops.

    ``candidates`` are ``(id, title, summary)``. Ideas judged within
    ``taste_review_screen_cache_days`` reuse their verdict (keyed on the
    normalised title); only new ones reach the model, in one call. Fails open:
    any error returns the cached drops alone.
    """
    if not candidates or niche_slug not in _csv(site_config.get("taste_review_niches", "")):
        return {}
    if not _bool(site_config.get("taste_review_screen_topics", "true")):
        return {}
    rubric = str(site_config.get("taste_review_rubric", "") or "").strip()
    if not rubric:
        return {}

    rows = await pool.fetch(
        """
        SELECT details->>'key' AS key, details->>'verdict' AS verdict, details->>'reason' AS reason
          FROM audit_log
         WHERE event_type = $1 AND details->>'niche' = $2
           AND timestamp > now() - make_interval(days => $3)
         ORDER BY timestamp
        """,
        SCREEN_EVENT, niche_slug, _int(site_config, "taste_review_screen_cache_days", 30),
    )
    cached = {r["key"]: (r["verdict"], r["reason"] or "") for r in rows}
    drops: dict[str, str] = {}
    fresh: list[tuple[str, str, str]] = []
    for cid, title, summary in candidates:
        hit = cached.get(_screen_key(title))
        if hit is None:
            fresh.append((cid, title, summary))
        elif hit[0] == "drop":
            drops[cid] = hit[1]
    if not fresh:
        return drops

    from poindexter.services.llm_text import ollama_chat_text, resolve_local_writer_model
    from poindexter.services.prompt_manager import get_prompt_manager
    from poindexter.utils.json_extract import extract_json_object

    ids = {f"c{i}": item for i, item in enumerate(fresh)}
    ideas = "\n".join(
        f"[{key}] {title}" + (f" — {summary.strip()}" if (summary or "").strip() else "")
        for key, (_, title, summary) in ids.items()
    )
    model = str(site_config.get("taste_review_model", "") or "").strip() \
        or resolve_local_writer_model(site_config=site_config)
    try:
        recent = await _recent_titles(pool, site_config, niche_slug)
        prompt = get_prompt_manager().get_prompt(
            "topic.taste_screen", subject=subject or "(no subject set for this niche)",
            rubric=rubric, today=today, recent="\n".join(f"- {t}" for t in recent) or "(none yet)",
            ideas=ideas,
        )
        raw = await ollama_chat_text(
            prompt, model=model, site_config=site_config, pool=pool, phase="topic_taste_screen",
            timeout_setting="taste_review_timeout_seconds", timeout_default=180.0, think=False,
            max_tokens=_int(site_config, "taste_review_max_tokens", 6000),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TASTE] topic screen failed for %s (keeping every idea): %s", niche_slug, exc)
        return drops
    verdicts = (extract_json_object(raw or "") or {}).get("topics") or []

    from poindexter.services.audit_log import AuditLogger

    audit = AuditLogger(pool)
    for item in verdicts:
        if not isinstance(item, dict) or str(item.get("id")) not in ids:
            continue
        cid, title, _summary = ids[str(item["id"])]
        keep = item.get("keep")
        if not isinstance(keep, bool):
            continue
        reason = str(item.get("reason", "")).strip()
        await audit.log(SCREEN_EVENT, _SOURCE, {
            "niche": niche_slug, "key": _screen_key(title), "title": title,
            "verdict": "keep" if keep else "drop", "reason": reason, "model": model,
        })
        if not keep:
            drops[cid] = reason
    if fresh and not verdicts:
        logger.warning("[TASTE] topic screen gave no verdicts for %s: %r", niche_slug, (raw or "")[:200])
    return drops


def note_for(review: dict[str, str] | None) -> str:
    """The awaiting-approval message line for a review, or ``""``."""
    if not review:
        return ""
    reason = review.get("reason") or "(no reason given)"
    return f"Taste check: {review['verdict']}: {reason}"


async def agreement(pool: Any, *, days: int = 60) -> dict[str, Any]:
    """How the review's verdicts compare with what the operator decided.

    The two recorders are independent: the review's ``audit_log`` row and the
    task's own terminal ``status``. A task still awaiting a decision is left
    out. ``leaked`` (the review said approve, the operator rejected) is the
    number that decides whether the review can ever gate.
    """
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (a.task_id) a.task_id, a.details->>'verdict' AS verdict,
               a.details->>'reason' AS reason, t.status, t.topic
          FROM audit_log a
          JOIN pipeline_tasks t ON t.task_id::text = a.task_id::text
         WHERE a.event_type = $1 AND a.timestamp > now() - make_interval(days => $2)
         ORDER BY a.task_id, a.timestamp DESC
        """,
        EVENT_TYPE, days,
    )
    out: dict[str, Any] = {"days": days, "pending": 0, "both_approve": 0, "both_reject": 0,
                           "leaked": [], "over_strict": []}
    for r in rows:
        if r["status"] in ("published", "approved", "scheduled"):
            decided = "approve"
        elif r["status"] == "rejected_final":
            decided = "reject"
        else:
            out["pending"] += 1
            continue
        if r["verdict"] == decided:
            out["both_" + decided] += 1
        elif r["verdict"] == "approve":
            out["leaked"].append({"task_id": str(r["task_id"]), "topic": r["topic"]})
        else:
            out["over_strict"].append({"task_id": str(r["task_id"]), "topic": r["topic"],
                                       "reason": r["reason"]})
    return out


__all__ = ["EVENT_TYPE", "SCREEN_EVENT", "agreement", "note_for", "review_draft", "screen_topics"]
