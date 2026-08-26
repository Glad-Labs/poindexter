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
2. evaluate reads four NICHE-PREFIXED settings (key shape
   ``{niche_slug}_auto_publish_*``, e.g.
   ``dev_diary_auto_publish_threshold``,
   ``glad-labs_auto_publish_threshold``):
   - ``{niche}_auto_publish_threshold`` (default -1 = disabled)
   - ``{niche}_auto_publish_dry_run`` (default true = log-only)
   - ``{niche}_auto_publish_min_clean_runs`` (default 3)
   - ``{niche}_auto_publish_max_edit_distance`` (default 50)
   If the caller passes no ``niche_slug``, evaluate resolves the task's
   OWN niche from ``pipeline_tasks`` (defense-in-depth, RCA 2026-07-11 —
   the state channel was undeclared for six weeks and the gate sat
   silently disabled). If no niche exists anywhere, the gate returns
   disabled — every niche must explicitly opt in via its own threshold
   key; there is never a cross-niche fallback.
3. Reads the trailing N rows from ``published_post_edit_metrics``
   for this niche — niche-only matching; ``category`` no longer
   participates (2026-07-11: the OR-category clause let another niche's
   edit rows pollute this niche's window once every flow task's category
   defaulted to "technology" post-Phase-F). "clean" means
   char_diff_count < max_edit_distance.
4. Returns AutoPublishDecision: would_fire (bool) + reason (str) +
   gate_state (str: 'pass' | 'block_threshold' | 'block_unclean' |
   'disabled' | 'dry_run').

The caller (finalize_task) inspects the decision:
- ``would_fire=True AND dry_run=False`` → call approval_service.approve_task
- ``would_fire=True AND dry_run=True`` → log only, leave awaiting_approval
- ``would_fire=False`` → leave awaiting_approval, log gate state

Edit-distance writer: :func:`record_post_approve_metrics` is called
from ``publish_service._record_edit_distance_metrics`` at the APPROVE
seam of ``publish_post_from_task`` — before the stage_only
short-circuit, so the approve→stage→promote operator flow records it
too (the 2026-06-24→07-11 freeze was this call sitting on the
immediate-publish tail, unreachable from both the stage and promote
short-circuits). It diffs the pre-approve content snapshot against
the post-approve content snapshot and writes one row to
published_post_edit_metrics; that feeds the next gate evaluation's
"trailing N clean runs" check.

2026-05-27 niche-leak fix: prior versions of this module read a
HARDCODED ``dev_diary_auto_publish_threshold`` regardless of the
caller's niche_slug. That bug let the dev_diary opt-in cross-pollinate
to every other niche — verified live when "Claude Is Not Your
Architect. Stop." (niche=glad-labs, score 92) auto-published 2026-05-26
13:45 UTC without operator approval. Each niche now owns its own keys
and the absence of an explicit key is a loud "disabled" return per
``feedback_no_silent_defaults``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)


async def _backfill_atom_run_outcome(
    pool: Any, *, task_id: Any, edit_distance: int
) -> None:
    """Backfill the ``"approved"`` outcome (+ operator edit distance) onto every
    ``atom_runs`` row for a task (#355 Plan 2 / #552).

    Best-effort — **never raises**, so it can never fail the approve/publish
    path — but a failure is surfaced as a non-paging ``info`` finding so a
    persistent break doesn't silently starve the ``atom_runs`` (composition ->
    outcome) learning signal the router scores on (batch-10 admin_db precedent).
    Called only after the primary ``published_post_edit_metrics`` write
    succeeds, so the pool is healthy and ``emit_finding`` will land.
    """
    try:
        from services.atom_runs import record_atom_run_outcome

        await record_atom_run_outcome(
            pool,
            task_id=str(task_id),
            decision="approved",
            edit_distance=edit_distance,
        )
    except Exception as exc:  # noqa: BLE001 — backfill is additive; never fail publish
        logger.debug(
            "[auto_publish_gate] atom_runs outcome backfill failed: %s",
            exc,
        )
        from utils.findings import emit_finding

        emit_finding(
            source="modules.content.auto_publish_gate",
            kind="atom_runs_outcome_backfill_failed",
            title="atom_runs approve-outcome backfill failed",
            body=(
                f"record_atom_run_outcome raised {describe_exception(exc)} "
                f"while backfilling the 'approved' outcome (edit_distance="
                f"{edit_distance}) for task {task_id}. The primary edit-metrics "
                f"row was written, but the atom_runs (composition -> outcome) "
                f"learning signal for this task is missing its approve outcome. A "
                f"persistent failure silently starves the router's per-atom "
                f"scoring."
            ),
            severity="info",
            dedup_key=f"atom_runs_outcome_backfill_failed:{type(exc).__name__}",
            extra={"error_type": type(exc).__name__, "task_id": str(task_id)},
        )


# ---------------------------------------------------------------------------
# Structural requirement constants (template-level, no LLM)
# ---------------------------------------------------------------------------

# Title patterns that look like a bare date with no substantive heading.
# Matched case-insensitively against the stripped title.
_DATE_ONLY_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(
        r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r",?\s+\w+\s+\d{1,2},?\s*\d{0,4}$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+\d{1,2},?\s+\d{4}$",
        re.IGNORECASE,
    ),
    # dev_diary header used verbatim as title
    re.compile(r"^what\s+we\s+shipped\s+on\s+", re.IGNORECASE),
)

# Substrings that identify boilerplate-only lines (case-insensitive).
_BOILERPLATE_SUBSTRINGS = (
    "auto-compiled by poindexter",
    "see the work: github.com/glad-labs",
)

# Minimum real-prose word count after stripping H1 headers + boilerplate.
_MIN_BODY_WORDS = 30


def _check_structural_requirements(
    title: str | None,
    content: str | None,
    excerpt: str | None,
) -> tuple[bool, str]:
    """Deterministic structural checks — no LLM, cannot flake.

    Returns ``(passed, reason)``. Called by :func:`evaluate` as Gate
    condition 2.5 — after the score clears the threshold, before the
    (potentially DB-hitting) trailing clean-run history fetch.

    Three checks:
    1. Excerpt must be non-empty (proxy for "body has real prose").
    2. Body must have >= _MIN_BODY_WORDS words after stripping H1
       headers and known boilerplate lines (header + footer).
    3. Title must not look like a bare date.
    """
    excerpt_s = (excerpt or "").strip()
    content_s = (content or "").strip()
    title_s = (title or "").strip()

    # Check 1: excerpt must exist — empty excerpt means no extractable prose.
    if not excerpt_s:
        return False, (
            "excerpt is empty — body likely contains no prose beyond boilerplate"
        )

    # Check 2: body must have real content beyond header/footer.
    real_lines = [
        line
        for line in content_s.splitlines()
        if line.strip()
        and not line.strip().startswith("# ")
        and not any(b in line.lower() for b in _BOILERPLATE_SUBSTRINGS)
    ]
    word_count = len(" ".join(real_lines).split())
    if word_count < _MIN_BODY_WORDS:
        return False, (
            f"body has only {word_count} real word(s) beyond boilerplate "
            f"(minimum {_MIN_BODY_WORDS})"
        )

    # Check 3: title must not be just a date.
    if title_s:
        for pat in _DATE_ONLY_PATTERNS:
            if pat.match(title_s):
                return False, f"title looks like a bare date: {title_s!r}"

    return True, "structural checks passed"


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
    """One of 'pass', 'block_threshold', 'block_structural',
    'block_unclean', 'block_qa_flagged', 'disabled', 'no_history'."""

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
    platform: Any = None,
    title: str | None = None,
    content: str | None = None,
    excerpt: str | None = None,
    qa_flagged: bool = False,
) -> AutoPublishDecision:
    """Evaluate the auto-publish gate for a task. Best-effort —
    exceptions return a 'disabled' decision rather than blocking
    the caller's main flow.

    Seam 1 Wave 3e (#667): ``platform`` is the capability handle
    (``context['platform']`` threaded from the caller stage). Config
    reads go through ``platform.config.get(key, default)``. When
    None, returns the 'disabled' decision because every threshold
    defaults to "off" without operator-tuned settings — preserving
    the prior ``site_config``-None seam behaviour.

    ``title`` / ``content`` / ``excerpt`` feed Gate condition 2.5 —
    cheap structural checks (no LLM) that run after the score clears
    the threshold. Defaulting to None skips the structural check for
    callers that don't pass content, preserving backwards compatibility.
    """
    # Self-heal-before-paging (#qa-self-heal): a draft the QA gate flagged
    # requires operator sign-off and must NEVER auto-publish — regardless of the
    # niche opt-in or the score. Checked first so it short-circuits even an
    # otherwise-passing gate. The operator's explicit publish path is unaffected.
    if qa_flagged:
        return AutoPublishDecision(
            would_fire=False, dry_run=True, gate_state="block_qa_flagged",
            reason="post flagged by QA — operator sign-off required, never auto-publish",
            quality_score=quality_score,
        )

    if platform is None:
        logger.debug("[auto_publish_gate] no platform handle — gate disabled")
        return AutoPublishDecision(
            would_fire=False, dry_run=True, gate_state="disabled",
            reason="platform not provided",
            quality_score=quality_score,
        )

    # 2026-05-27 niche-leak fix: every key is now niche-prefixed. A
    # missing niche_slug returns disabled per
    # ``feedback_no_silent_defaults`` — no implicit cross-niche fallback.
    #
    # RCA 2026-07-11 defense-in-depth: when the CALLER couldn't supply the
    # niche (the ``PipelineState`` channel was undeclared 2026-05-28 →
    # 2026-07-11, so every stage read None and the gate sat disabled for
    # six weeks while the operator hand-approved daily), resolve the
    # task's OWN niche from the durable seam — the ``pipeline_tasks`` row.
    # This is a same-task lookup, NOT a cross-niche fallback: a task with
    # no niche anywhere still returns disabled, and the resolved niche
    # still has to carry its own opt-in keys.
    niche = (niche_slug or "").strip()
    if not niche and pool is not None and task_id:
        try:
            async with pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT niche_slug FROM pipeline_tasks WHERE task_id = $1",
                    str(task_id),
                )
            if isinstance(raw, str) and raw.strip():
                niche = raw.strip()
                logger.info(
                    "[auto_publish_gate] caller passed no niche_slug — "
                    "resolved %r from pipeline_tasks for task %s",
                    niche, task_id,
                )
        except Exception as exc:  # noqa: BLE001 — fail-closed to 'disabled'
            logger.warning(
                "[auto_publish_gate] niche_slug fallback lookup failed for "
                "task %s (gate stays disabled): %s",
                task_id, exc,
            )
    if not niche:
        logger.debug(
            "[auto_publish_gate] no niche_slug — gate disabled (no cross-niche fallback)"
        )
        return AutoPublishDecision(
            would_fire=False, dry_run=True, gate_state="disabled",
            reason="niche_slug missing — every niche must opt in via its own key",
            quality_score=quality_score,
        )

    threshold_key = f"{niche}_auto_publish_threshold"
    dry_run_key = f"{niche}_auto_publish_dry_run"
    min_clean_key = f"{niche}_auto_publish_min_clean_runs"
    max_edit_key = f"{niche}_auto_publish_max_edit_distance"

    threshold_raw = platform.config.get(threshold_key, "-1")
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError):
        threshold = -1.0

    dry_run_raw = platform.config.get(dry_run_key, "true")
    dry_run = str(dry_run_raw).strip().lower() in ("true", "1", "yes", "on")

    min_clean_raw = platform.config.get(min_clean_key, "3")
    try:
        min_clean = int(float(min_clean_raw))
    except (TypeError, ValueError):
        min_clean = 3

    max_edit_raw = platform.config.get(max_edit_key, "50")
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
        base.reason = f"{threshold_key} < 0 — gate disabled by default"
        return base

    # Gate condition 2: quality floor.
    if quality_score < threshold:
        base.gate_state = "block_threshold"
        base.reason = (
            f"quality_score {quality_score:.1f} below threshold {threshold:.1f}"
        )
        return base

    # Gate condition 2.5: structural requirements — cheap, deterministic,
    # no LLM. Only evaluated when content was provided; callers that omit
    # it (backwards compat) skip this gate silently.
    if content is not None or excerpt is not None or title is not None:
        struct_ok, struct_reason = _check_structural_requirements(
            title, content, excerpt,
        )
        if not struct_ok:
            base.gate_state = "block_structural"
            base.reason = struct_reason
            return base

    # Gate condition 3: trailing clean-run count for this niche — and ONLY
    # this niche. The pre-2026-07-11 query also matched ``OR category=$2``;
    # once the Phase F squash retired ``pipeline_tasks.category``
    # (2026-06-22) every flow task's category defaulted to "technology",
    # so the OR pulled OTHER niches' edit rows into this niche's trailing
    # window (glad-labs' 124-char edit would have blocked dev_diary as
    # ``block_unclean``). Cross-niche pollution is the same bug class the
    # 2026-05-27 niche-prefix fix closed — the track record a niche earns
    # is the track record its gate reads. ``category`` is still accepted
    # by this function (call-site backcompat + recorded on write) but no
    # longer participates in matching. Note $1 binds the RESOLVED niche
    # (which may have come from the pipeline_tasks fallback above), not
    # the raw ``niche_slug`` argument.
    clean_runs = 0
    last_n_diffs: list[int] = []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT char_diff_count
                  FROM published_post_edit_metrics
                 WHERE COALESCE(niche_slug, '') = $1
                 ORDER BY approved_at DESC
                 LIMIT $2
                """,
                niche, max(min_clean, 1),
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


async def _lookup_latest_capability_outcome(
    pool: Any, task_id: str,
) -> dict[str, Any] | None:
    """Best-effort lookup of the most-recent capability_outcomes row
    for a task. Returns dict with model_used / prompt_template_key /
    prompt_template_version, or None if no row exists / query failed.

    Phase 0 lab observability (2026-05-28). Used at approve time to
    cross-stamp the writer's provenance onto the edit metric row, so
    the lab view can attribute "operator edited N chars on the post
    that the writer produced with prompt_template_key=X version=Y on
    model Z". Pulls the latest non-NULL model_used because the writer
    atom is the right grounding signal (other stages set NULL for
    model_used since they don't invoke an LLM).
    """
    if pool is None or not task_id:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT model_used,
                       prompt_template_key,
                       prompt_template_version
                  FROM capability_outcomes
                 WHERE task_id = $1
                   AND (model_used IS NOT NULL
                        OR prompt_template_key IS NOT NULL)
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                str(task_id),
            )
        if row is None:
            return None
        return {
            "model_used": row["model_used"],
            "prompt_template_key": row["prompt_template_key"],
            "prompt_template_version": row["prompt_template_version"],
        }
    except Exception as exc:  # noqa: BLE001
        from utils.findings import emit_finding

        emit_finding(
            source="auto_publish_gate",
            kind="capability_outcomes_lookup_failed",
            title="capability_outcomes lookup failed at approve time",
            body=(
                f"lookup failed for task {task_id}: {describe_exception(exc)}. The approve-time "
                "edit metric row won't be cross-stamped with the writer's "
                "model/prompt-template provenance (lab-view attribution gap "
                "for this one approval; non-blocking)."
            ),
            dedup_key="capability_outcomes_lookup_failed",
        )
        return None


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
    model_used: str | None = None,
    prompt_template_key: str | None = None,
    prompt_template_version: int | None = None,
) -> bool:
    """Compute pre/post-approve edit metrics + persist a row.

    Best-effort. Returns True on success. Called from
    publish_service.publish_post_from_task when an operator approves an
    awaiting_approval task — the diff between the content the
    pipeline produced and the content the operator approved IS the
    auto-publish gate's training signal.

    Phase 0 lab observability (2026-05-28) adds three new optional
    kwargs — ``model_used`` / ``prompt_template_key`` /
    ``prompt_template_version`` — that flow into the matching columns.
    Callers that don't have them up front can leave them None and
    this function does a best-effort lookup against capability_outcomes
    for the same task_id, copying whichever fields are populated
    there. Per ``feedback_backcompat_now_required``, the new kwargs
    default to None so existing call sites stay unchanged.
    """
    if pool is None:
        # Loud skip, not a silent one: a caller wired without a DB pool
        # starves the gate's edit-distance training signal invisibly —
        # the approve/publish itself still succeeds, so nothing else
        # surfaces the gap (2026-07-11 freeze RCA).
        logger.warning(
            "[auto_publish_gate] record_post_approve_metrics skipped for "
            "task %s: caller passed pool=None — edit-distance row NOT "
            "written",
            task_id,
        )
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
        except Exception:  # noqa: BLE001 — silent-ok: refines char_diff, already computed as abs(len) at the top of this block; stdlib difflib over post-sized strings can't realistically raise, and the crude fallback still records a metric
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
        except Exception:  # noqa: BLE001 — silent-ok: refines line_diff, already computed as abs(len) at the top of this block; stdlib difflib over post-sized strings can't realistically raise, and the crude fallback still records a metric
            pass

    # Phase 0 lab observability — back-fill any unset provenance fields
    # from the matching capability_outcomes row. The writer atom
    # produces the canonical (model, prompt_key, prompt_version) trio
    # at run time; copying them here means the lab view can join
    # outcome → edit_metric without an extra correlation step.
    if (
        model_used is None
        or prompt_template_key is None
        or prompt_template_version is None
    ):
        latest = await _lookup_latest_capability_outcome(pool, task_id)
        if latest is not None:
            if model_used is None:
                model_used = latest.get("model_used")
            if prompt_template_key is None:
                prompt_template_key = latest.get("prompt_template_key")
            if prompt_template_version is None:
                prompt_template_version = latest.get(
                    "prompt_template_version"
                )

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO published_post_edit_metrics
                  (task_id, post_id, niche_slug, category, approver,
                   pre_approve_hash, post_approve_hash,
                   char_diff_count, line_diff_count,
                   pre_approve_len, post_approve_len,
                   approve_method, metrics,
                   model_used, prompt_template_key,
                   prompt_template_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13::jsonb, $14, $15, $16)
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
                model_used, prompt_template_key, prompt_template_version,
            )
        logger.info(
            "[auto_publish_gate] recorded edit metrics for task %s "
            "(char_diff=%d, line_diff=%d, niche=%s, model=%s, "
            "prompt=%s/v%s)",
            task_id, char_diff, line_diff, niche_slug,
            model_used, prompt_template_key, prompt_template_version,
        )

        # Atom-runs outcome backfill (#355 Plan 2 / #552). This function is
        # only called on the approve/publish path, so the decision is
        # "approved" and char_diff IS the operator edit distance — the same
        # (composition -> outcome) signal capability_outcomes wants, joined
        # back to every atom_runs row for this task. Best-effort + COALESCE-
        # composing (record_atom_run_outcome keeps any field a later partial
        # update writes), so this never overwrites and never fails publish.
        # post_id is left to compose later: published_post_edit_metrics.post_id
        # is a legacy bigint while atom_runs.post_id is the posts uuid, so the
        # int passed here is not the uuid join key.
        await _backfill_atom_run_outcome(
            pool, task_id=task_id, edit_distance=char_diff
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
