"""``auto_publish_gate`` — observe-only auto-publish decision logger.

Per ``feedback_auto_publish_requires_edit_distance_track_record``:
auto-publish gates on edit-distance trending to zero across N
consecutive runs, NOT on quality_score alone. Per
``feedback_human_approval``: until the operator explicitly opts in,
every public-facing post requires human approval.

This module is the bridge — it computes "would the gate fire?" for
every finalize_task run, logs the decision via audit_log +
capability_outcomes, but does NOT actually approve unless dry_run
is explicitly turned off in app_settings AND the threshold + clean-
run criteria pass.

Flow:

1. finalize_task calls :func:`evaluate` with the task's quality_score
   + niche + category + content snapshot.
2. evaluate reads three settings:
   - ``dev_diary_auto_publish_threshold`` (default -1 = disabled)
   - ``dev_diary_auto_publish_dry_run`` (default true = log-only)
   - ``dev_diary_auto_publish_min_clean_runs`` (default 3)
3. Reads the trailing N rows from ``published_post_edit_metrics``
   for this niche; "clean" means char_diff_count <
   max_edit_distance.
4. Returns AutoPublishDecision: would_fire (bool) + reason (str) +
   gate_state (str: 'pass' | 'block_threshold' | 'block_unclean' |
   'disabled' | 'dry_run').

The caller (finalize_task) inspects the decision:
- ``would_fire=True AND dry_run=False`` → call approval_service.approve_task
- ``would_fire=True AND dry_run=True`` → log only, leave awaiting_approval
- ``would_fire=False`` → leave awaiting_approval, log gate state

Edit-distance writer: :func:`record_post_approve_metrics` is called
from approval_service.approve when an operator approves a post,
diffing the pre-approve content snapshot against the post-approve
content snapshot and writing one row to published_post_edit_metrics.
That feeds the next gate evaluation's "trailing N clean runs" check.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AutoPublishDecision:
    """Result of the auto-publish gate evaluation."""

    would_fire: bool
    """True when the gate would auto-approve. May still be blocked by
    dry_run; see ``dry_run`` field."""

    dry_run: bool
    """True when the gate is configured for observe-only mode. The
    caller should NOT actually approve when dry_run is true, even if
    would_fire is true."""

    gate_state: str
    """One of 'pass', 'block_threshold', 'block_unclean', 'disabled',
    'no_history'."""

    reason: str
    """Human-readable reason — surfaces in audit_log + Discord."""

    quality_score: float = 0.0
    threshold: float = -1.0
    trailing_clean_runs: int = 0
    required_clean_runs: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)


async def evaluate(
    pool: Any,
    *,
    task_id: str,
    niche_slug: str | None,
    category: str | None,
    quality_score: float,
) -> AutoPublishDecision:
    """Evaluate the auto-publish gate for a task. Best-effort —
    exceptions return a 'disabled' decision rather than blocking
    the caller's main flow.
    """
    try:
        from services.site_config import site_config
    except Exception as exc:  # noqa: BLE001
        logger.debug("[auto_publish_gate] site_config unavailable: %s", exc)
        return AutoPublishDecision(
            would_fire=False, dry_run=True, gate_state="disabled",
            reason=f"site_config unavailable: {exc}",
            quality_score=quality_score,
        )

    threshold_raw = site_config.get("dev_diary_auto_publish_threshold", "-1")
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError):
        threshold = -1.0

    dry_run_raw = site_config.get("dev_diary_auto_publish_dry_run", "true")
    dry_run = str(dry_run_raw).strip().lower() in ("true", "1", "yes", "on")

    min_clean_raw = site_config.get("dev_diary_auto_publish_min_clean_runs", "3")
    try:
        min_clean = int(float(min_clean_raw))
    except (TypeError, ValueError):
        min_clean = 3

    max_edit_raw = site_config.get(
        "dev_diary_auto_publish_max_edit_distance", "50"
    )
    try:
        max_edit_distance = int(float(max_edit_raw))
    except (TypeError, ValueError):
        max_edit_distance = 50

    base = AutoPublishDecision(
        would_fire=False, dry_run=dry_run, gate_state="disabled",
        reason="threshold disabled (default state)",
        quality_score=quality_score, threshold=threshold,
        required_clean_runs=min_clean,
    )

    # Gate condition 1: threshold opt-in.
    if threshold < 0:
        base.gate_state = "disabled"
        base.reason = "auto_publish_threshold < 0 — gate disabled by default"
        return base

    # Gate condition 2: quality floor.
    if quality_score < threshold:
        base.gate_state = "block_threshold"
        base.reason = (
            f"quality_score {quality_score:.1f} below threshold {threshold:.1f}"
        )
        return base

    # Gate condition 3: trailing clean-run count for this niche.
    clean_runs = 0
    last_n_diffs: list[int] = []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT char_diff_count
                  FROM published_post_edit_metrics
                 WHERE COALESCE(niche_slug, '') = COALESCE($1, '')
                    OR COALESCE(category, '')   = COALESCE($2, '')
                 ORDER BY approved_at DESC
                 LIMIT $3
                """,
                niche_slug, category, max(min_clean, 1),
            )
            last_n_diffs = [int(r["char_diff_count"]) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[auto_publish_gate] history fetch failed: %s — defaulting to no_history",
            exc,
        )
        base.gate_state = "no_history"
        base.reason = f"history fetch failed: {exc}"
        return base

    clean_runs = sum(1 for d in last_n_diffs if d < max_edit_distance)
    base.trailing_clean_runs = clean_runs

    if len(last_n_diffs) < min_clean:
        base.gate_state = "no_history"
        base.reason = (
            f"only {len(last_n_diffs)} historical approves for this niche/category — "
            f"need {min_clean} clean runs before gate fires"
        )
        return base

    if clean_runs < min_clean:
        base.gate_state = "block_unclean"
        base.reason = (
            f"only {clean_runs}/{min_clean} trailing approves had "
            f"edit_distance < {max_edit_distance}"
        )
        return base

    # All conditions pass.
    base.would_fire = True
    base.gate_state = "pass"
    base.reason = (
        f"quality_score {quality_score:.1f} >= {threshold:.1f}, "
        f"{clean_runs}/{min_clean} trailing clean runs"
    )
    base.metrics = {
        "trailing_diffs": last_n_diffs,
        "max_edit_distance": max_edit_distance,
    }
    return base


def hash_content(content: str) -> str:
    """Stable content fingerprint for diff comparison."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


async def record_post_approve_metrics(
    pool: Any,
    *,
    task_id: str,
    pre_approve_content: str,
    post_approve_content: str,
    niche_slug: str | None = None,
    category: str | None = None,
    approver: str = "operator",
    approve_method: str = "manual",
    post_id: int | None = None,
) -> bool:
    """Compute pre/post-approve edit metrics + persist a row.

    Best-effort. Returns True on success. Called from
    approval_service.approve when an operator approves an
    awaiting_approval task — the diff between the content the
    pipeline produced and the content the operator approved IS the
    auto-publish gate's training signal.
    """
    if pool is None:
        return False

    pre = pre_approve_content or ""
    post = post_approve_content or ""

    pre_hash = hash_content(pre)
    post_hash = hash_content(post)

    # Char-level diff: simple unified-len-delta + difflib SequenceMatcher
    # ratio. Avoid pulling in external diff libs; stdlib is enough for
    # the granularity the gate needs.
    char_diff = abs(len(pre) - len(post))
    if pre and post and pre_hash != post_hash:
        try:
            import difflib
            sm = difflib.SequenceMatcher(a=pre, b=post)
            # Total edit cost = inserted + deleted chars; matching blocks
            # contribute zero. Approximate via 2 * (1 - ratio) * len(union).
            ratio = sm.ratio()
            char_diff = max(char_diff, int(2 * (1 - ratio) * max(len(pre), len(post))))
        except Exception:  # noqa: BLE001
            pass

    pre_lines = pre.splitlines()
    post_lines = post.splitlines()
    line_diff = abs(len(pre_lines) - len(post_lines))
    if pre_lines and post_lines:
        try:
            import difflib
            sm_lines = difflib.SequenceMatcher(a=pre_lines, b=post_lines)
            line_diff = max(line_diff, int(
                2 * (1 - sm_lines.ratio()) * max(len(pre_lines), len(post_lines))
            ))
        except Exception:  # noqa: BLE001
            pass

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO published_post_edit_metrics
                  (task_id, post_id, niche_slug, category, approver,
                   pre_approve_hash, post_approve_hash,
                   char_diff_count, line_diff_count,
                   pre_approve_len, post_approve_len,
                   approve_method, metrics)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
                """,
                task_id, post_id, niche_slug, category, approver,
                pre_hash, post_hash,
                char_diff, line_diff,
                len(pre), len(post),
                approve_method,
                json.dumps({
                    "pre_word_count": len(pre.split()),
                    "post_word_count": len(post.split()),
                }),
            )
        logger.info(
            "[auto_publish_gate] recorded edit metrics for task %s "
            "(char_diff=%d, line_diff=%d, niche=%s)",
            task_id, char_diff, line_diff, niche_slug,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[auto_publish_gate] record_post_approve_metrics failed: %s", exc,
        )
        return False


__all__ = [
    "AutoPublishDecision",
    "evaluate",
    "hash_content",
    "record_post_approve_metrics",
]
