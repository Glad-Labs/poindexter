"""Post-pipeline side-effects shared by both orchestrators.

After the content pipeline finishes a task there is a fixed sequence of
operator-visible side-effects to run:

0. Terminal-status guard. Re-read the canonical ``pipeline_tasks.status``
   (the spinal-cord signal, not the brittle in-memory ``result`` dict). A
   task qa.aggregate already hard-rejected (``status='rejected'``, written
   straight to the DB while the graph halts) gets a routine 'QA rejected'
   Discord notice and NONE of the success-path steps below — no false
   awaiting-approval ping, no ``task.completed`` webhook, and crucially no
   auto-publish of QA-rejected content. ``failed`` / ``cancelled`` /
   ``published`` / ``approved`` are skipped silently (handled elsewhere).
1. Emit a ``task.completed`` webhook so external consumers
   (Discord ops integration, future SaaS multi-tenant routing,
   marketplace adapters) see "this task finished".
2. Auto-curator gate — for niches that don't deserve operator review
   when the QA score is below ``app_settings.min_curation_score``
   (default ``70``). Auto-reject + audit + flip the
   ``model_performance.human_approved`` learning signal + emit
   ``task.auto_rejected`` webhook. Skip the rest of the chain.
3. Auto-publish gate — when ``app_settings.require_human_approval``
   is ``false`` AND the QA score crosses
   ``app_settings.auto_publish_threshold``, delegate to
   :func:`modules.content.auto_publish.auto_publish_task` so trusted niches
   ship without manual approval.
4. Operator notification — a single Discord-or-Telegram message
   that links to the rendered preview, plus the opt-in
   ``qa_preview_screenshot_enabled`` vision-model QA pass.

These steps used to live inline in
``services/task_executor.py::_process_loop`` (lines 678-810 before
poindexter#478 landed). The Prefect cutover (Glad-Labs/poindexter#410)
short-circuited ``_process_loop`` when
``app_settings.use_prefect_orchestration=true``; Stage 4 of that
cutover (2026-05-16) deleted ``task_executor.py`` entirely. The
Prefect ``content_generation_flow`` is the sole caller of this
module now.

Design notes:

- **DI seam.** ``site_config`` is a required kwarg, never imported
  from the module-level singleton. Prefect subprocesses run a
  separate Python interpreter and never see ``main.py``'s lifespan
  rebind, so any caller that wants the right config has to thread
  it explicitly. The Prefect flow gets the wired instance back
  from ``services.di_wiring.build_and_wire_for_subprocess``.
- **Failure isolation.** Every side-effect runs inside its own
  ``try/except logged at WARNING``. A failed webhook must not
  block auto-publish; a failed notification must not block the
  next task in the loop. The block returns ``None`` on the
  auto-rejected branch to skip the rest of the chain.
- **No silent defaults.** Every fallback path logs a WARNING
  with enough context (task_id, fallback reason) to find the row
  in Grafana / Loki. Honours the ``feedback_no_silent_defaults``
  rule from MEMORY.md.
- **DB-first config.** Thresholds (``min_curation_score``,
  ``require_human_approval``, ``auto_publish_threshold``,
  ``preview_base_url``, ``qa_preview_screenshot_enabled``) are
  read via the injected ``settings_service`` / ``site_config``,
  never hardcoded. Honours ``feedback_db_first_config``.

See Glad-Labs/poindexter#478 for the full incident write-up.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .webhook_delivery_service import emit_webhook_event

logger = logging.getLogger(__name__)

# Terminal statuses the pipeline itself already decided. A task in one of
# these has been routed to its outcome by the graph or a prior side-effect,
# so the success-path actions (task.completed webhook, auto-curate,
# auto-publish, awaiting-approval ping) must NOT run. ``rejected`` is handled
# separately — it earns a routine operator notice rather than a silent skip.
_DECIDED_NON_REJECTED_STATUSES = frozenset(
    {"failed", "cancelled", "canceled", "published", "approved"}
)


# Default thresholds preserved verbatim from the inline block they
# replaced. Operators tune via app_settings; these defaults match the
# pre-extraction behaviour so the refactor is value-preserving.
_DEFAULT_MIN_CURATION_SCORE = "70"
_DEFAULT_REQUIRE_HUMAN_APPROVAL = "true"
_DEFAULT_AUTO_PUBLISH_THRESHOLD = "0"
_DEFAULT_PREVIEW_BASE_URL = "http://localhost:8002"


async def _get_setting(
    *,
    settings_service: Any | None,
    database_service: Any | None,
    key: str,
    default: str,
) -> str:
    """Read an app_settings value with the legacy fallback contract.

    Prefers the explicit ``settings_service`` (the DI seam) and falls
    back to ``database_service.get_setting_value`` for parity with the
    pre-extraction inline block in ``task_executor.py`` (deleted in
    the Prefect Stage 4 cutover, Glad-Labs/poindexter#410). Returns
    the ``default`` on any exception — operator-visible WARNING when
    the lookup actually raised vs. silently returning empty.
    """
    if settings_service is not None:
        try:
            raw = await settings_service.get_setting(key, default)
            return str(raw) if raw is not None else default
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "[POST_PIPELINE] settings_service.get_setting(%r) failed, "
                "falling back to database_service / default: %s",
                key, exc,
            )
    if database_service is not None:
        try:
            raw = await database_service.get_setting_value(key, default)
            return str(raw) if raw is not None else default
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "[POST_PIPELINE] database_service.get_setting_value(%r) "
                "failed, returning default %r: %s",
                key, default, exc,
            )
    return default


async def _read_preview_token(database_service: Any, task_id: str) -> str | None:
    """Look up the preview token written by ``FinalizeTaskStage``.

    PR #368 moved preview-token generation into the finalize stage
    (``pipeline_versions.stage_data->'metadata'->>'preview_token'``)
    so both legacy and Prefect orchestrators surface a clickable
    title link in the Grafana approval-queue panel. This helper
    reads what that stage wrote.

    Returns ``None`` when the token is missing — the finalize stage
    runs before this helper, so the token SHOULD always be present;
    if it isn't, the notification still fires but without a preview
    link (degraded but non-fatal) and a WARNING is logged with the
    task_id so the regression is visible in Grafana / Loki.
    """
    pool = (
        getattr(database_service, "cloud_pool", None)
        or getattr(database_service, "pool", None)
    )
    if pool is None:
        logger.warning(
            "[POST_PIPELINE] preview_token lookup skipped for %s — "
            "database_service has no pool; notification link omitted",
            task_id,
        )
        return None
    try:
        token = await pool.fetchval(
            """
            SELECT stage_data -> 'metadata' ->> 'preview_token'
            FROM pipeline_versions
            WHERE task_id = $1
            ORDER BY version DESC
            LIMIT 1
            """,
            str(task_id),
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "[POST_PIPELINE] preview_token SELECT failed for %s — "
            "notification will omit the preview link: %s",
            task_id, exc,
        )
        return None
    if not token:
        logger.warning(
            "[POST_PIPELINE] preview_token missing for %s — finalize "
            "stage should have written one (PR #368). Notification "
            "will fire without a preview link.",
            task_id,
        )
        return None
    return str(token)


async def _bypass_curator_for_dev_diary(
    database_service: Any, task_id: str, quality_score: float,
) -> bool:
    """Return True when this task is in the ``dev_diary`` niche.

    dev_diary posts are bundle-grounded narrative — the LLM-tuned
    quality scorer rates them on writing-craft signals that don't fit
    a build-in-public status report (no creative hook, no SEO CTA),
    so an LLM scorer underrates perfectly good operator-visible posts.
    Operator review is the only gate for this niche.

    Before 2026-05-28 this bypass keyed off ``writer_rag_mode ==
    'DETERMINISTIC_COMPOSITOR'`` — a sentinel that's no longer
    meaningful (the compositor writer was deleted, dev_diary now runs
    through ``atoms/narrate_bundle``). niche_slug is the durable seam.
    """
    pool = getattr(database_service, "pool", None)
    if pool is None:
        return False
    try:
        async with pool.acquire() as _conn:
            niche_slug = await _conn.fetchval(
                "SELECT niche_slug FROM pipeline_tasks WHERE task_id = $1",
                str(task_id),
            )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "[POST_PIPELINE] curator-bypass niche_slug lookup "
            "failed for %s — defaulting to bypass=False: %s",
            task_id, exc,
        )
        return False
    if str(niche_slug or "").lower() == "dev_diary":
        logger.info(
            "[CURATE] Bypassing auto-curator for dev_diary task %s "
            "(score=%.1f) — bundle-grounded narrative, operator review only",
            task_id, quality_score,
        )
        return True
    return False


async def _auto_curate(
    *,
    database_service: Any,
    settings_service: Any | None,
    task_id: str,
    topic: str,
    quality_score: float,
) -> bool:
    """Auto-reject a low-quality post before bothering the operator.

    Returns ``True`` when the task was rejected (caller should skip
    the rest of the post-pipeline chain), ``False`` otherwise.

    Side-effects on rejection (all best-effort, mirroring the inline
    block they replace):

    - ``pipeline_tasks.status = 'rejected'`` (the canonical signal)
    - ``pipeline_gate_history`` insert (so the ``content_tasks`` view's
      ``approval_status`` column resolves to ``'rejected'`` instead of
      NULL — operator-visible in the approval queue)
    - ``model_performance.human_approved = False`` (learning signal,
      poindexter#271 Phase 3.A1)
    - ``task.auto_rejected`` webhook (so external consumers can react)
    """
    min_score_str = await _get_setting(
        settings_service=settings_service,
        database_service=database_service,
        key="min_curation_score",
        default=_DEFAULT_MIN_CURATION_SCORE,
    )
    try:
        min_score = float(min_score_str)
    except (TypeError, ValueError):
        logger.warning(
            "[POST_PIPELINE] min_curation_score=%r is not numeric — "
            "falling back to %s",
            min_score_str, _DEFAULT_MIN_CURATION_SCORE,
        )
        min_score = float(_DEFAULT_MIN_CURATION_SCORE)

    bypass = await _bypass_curator_for_dev_diary(
        database_service, task_id, quality_score,
    )
    if bypass:
        return False
    if not (0 < quality_score < min_score):
        return False

    logger.info(
        "[CURATE] Auto-rejecting low-quality post: %s (score %.1f < %.1f)",
        topic[:40], quality_score, min_score,
    )
    try:
        await database_service.update_task(task_id, {"status": "rejected"})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] auto-curator update_task(rejected) "
            "failed for %s: %s",
            task_id, exc,
        )

    # pipeline_gate_history row — operator-visible audit trail.
    try:
        pool = getattr(database_service, "pool", None)
        if pool is not None:
            await pool.execute(
                """
                INSERT INTO pipeline_gate_history
                    (task_id, gate_name, event_kind, feedback, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                task_id,
                "auto_curator",
                "rejected",
                f"Quality score {quality_score:.1f} below threshold {min_score:.1f}",
                json.dumps(
                    {
                        "reviewer": "auto_curator",
                        "decision": "rejected",
                        "quality_score": quality_score,
                        "threshold": min_score,
                    },
                    default=str,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] auto-curator gate-history insert failed for "
            "%s: %s — rejection left with no audit-trail row", task_id, exc,
        )

    # model_performance.human_approved flip — feedback loop signal.
    try:
        await database_service.mark_model_performance_outcome(
            task_id, human_approved=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] auto-curator model_performance flip failed "
            "for %s: %s", task_id, exc,
        )

    # task.auto_rejected webhook — best-effort.
    try:
        _pool = (
            getattr(database_service, "cloud_pool", None)
            or getattr(database_service, "pool", None)
        )
        await emit_webhook_event(
            _pool,
            "task.auto_rejected",
            {
                "task_id": task_id,
                "topic": topic,
                "quality_score": quality_score,
                "reason": f"score {quality_score:.0f} < {min_score:.0f}",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] auto-curator auto_rejected webhook failed "
            "for %s: %s", task_id, exc,
        )
    return True


async def _maybe_auto_publish(
    *,
    database_service: Any,
    settings_service: Any | None,
    task_id: str,
    quality_score: float,
    site_config: Any = None,
    auto_publish_gate: dict[str, Any] | None = None,
) -> bool:
    """Auto-publish trusted-niche posts that clear the threshold.

    Returns ``True`` when :func:`modules.content.auto_publish.auto_publish_task`
    successfully shipped the post (caller should suppress the
    operator notification because ``publish_service`` sends its own
    published-post message). Returns ``False`` when the task stays
    in awaiting_approval — either the gate didn't fire, or
    ``auto_publish_task`` bailed (daily limit, missing image,
    publish error). The task is operator-visible either way; the
    only difference is whether we send the awaiting-approval ping.

    The publishing logic lives in :mod:`modules.content.auto_publish` (ported
    from the deleted ``TaskExecutor._auto_publish_task`` during the
    Prefect Stage 4 cutover — Glad-Labs/poindexter#410). This helper
    only decides whether to call it.

    Per-template gate bypass: when the caller passes a
    ``auto_publish_gate`` dict with ``would_fire=True`` and
    ``dry_run=False``, the global ``require_human_approval`` flag is
    bypassed. This is the operator opt-in pattern from
    :mod:`modules.content.auto_publish_gate` — setting
    ``dev_diary_auto_publish_threshold > 0`` AND
    ``dev_diary_auto_publish_dry_run=false`` is the affirmative signal
    that this niche has been audited and trusted to ship without a
    review pass. Without this bypass the per-template thresholds
    were dead code: the global flag forced every post to manual
    approval regardless of the niche-specific opt-in. The 2026-05-17
    vacation observation surfaced this — Matt had configured
    dev_diary to auto-publish weeks ago, but every post still went
    to awaiting_approval.
    """
    # If the caller didn't pre-compute the gate decision (the result
    # dict path is brittle — depends on the LangGraph state graph
    # plumbing the context_updates back into the top-level result),
    # compute it here. Same gate code path; just invoked from the
    # decision site instead of finalize_task's observability path.
    if auto_publish_gate is None:
        try:
            pool = getattr(database_service, "pool", None)
            if pool is not None and site_config is not None:
                row = await pool.fetchrow(
                    "SELECT niche_slug, category FROM pipeline_tasks "
                    "WHERE task_id = $1",
                    task_id,
                )
                niche_slug = row["niche_slug"] if row else None
                category = row["category"] if row else None
                from modules.content.api import evaluate_auto_publish_gate as _gate_evaluate
                decision = await _gate_evaluate(
                    pool,
                    task_id=task_id,
                    niche_slug=niche_slug,
                    category=category,
                    quality_score=quality_score,
                )
                auto_publish_gate = {
                    "would_fire": decision.would_fire,
                    "dry_run": decision.dry_run,
                    "gate_state": decision.gate_state,
                    "reason": decision.reason,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[POST_PIPELINE] inline gate evaluate failed for %s: %s",
                task_id, exc,
            )
            auto_publish_gate = None

    gate_bypass = bool(
        auto_publish_gate
        and auto_publish_gate.get("would_fire") is True
        and auto_publish_gate.get("dry_run") is False
    )
    require_str = await _get_setting(
        settings_service=settings_service,
        database_service=database_service,
        key="require_human_approval",
        default=_DEFAULT_REQUIRE_HUMAN_APPROVAL,
    )
    if require_str.lower() in ("true", "1", "yes") and not gate_bypass:
        logger.info(
            "[APPROVAL] Human approval required — task %s (score: %.0f) "
            "queued as awaiting_approval",
            task_id, quality_score,
        )
        return False
    if gate_bypass:
        gate = auto_publish_gate or {}
        logger.info(
            "[AUTO_PUBLISH] per-template gate (would_fire=True, dry_run=False) "
            "bypasses require_human_approval AND global "
            "auto_publish_threshold for task %s (score: %.0f, "
            "gate_state=%s, gate_reason=%s)",
            task_id, quality_score, gate.get("gate_state"), gate.get("reason"),
        )

    from modules.content.api import auto_publish_task, get_auto_publish_threshold

    if not gate_bypass:
        try:
            auto_threshold = await get_auto_publish_threshold(database_service)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[POST_PIPELINE] get_auto_publish_threshold failed for %s, "
                "task stays in awaiting_approval: %s",
                task_id, exc,
            )
            return False

        if auto_threshold <= 0 or quality_score < auto_threshold:
            return False

        logger.info(
            "[AUTO_PUBLISH] Quality score %s >= threshold %s, "
            "auto-publishing task %s",
            quality_score, auto_threshold, task_id,
        )
    try:
        return await auto_publish_task(
            database_service=database_service,
            task_id=task_id,
            quality_score=quality_score,
            site_config=site_config,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] auto_publish_task raised for %s, "
            "task stays in awaiting_approval: %s",
            task_id, exc,
        )
        return False


async def _maybe_run_preview_qa(
    *,
    database_service: Any,
    settings_service: Any | None,
    site_config: Any,
    task_id: str,
    topic: str,
    preview_token: str,
) -> str:
    """Opt-in vision-model QA pass against the rendered preview.

    Gated on ``app_settings.qa_preview_screenshot_enabled``
    (default ``false``). When enabled the helper screenshots the
    preview page, runs it through the vision QA reviewer, persists
    the score + verdict to ``content_tasks.metadata``, and returns
    a one-line note that the notification message embeds.

    Returns the empty string when the gate is off or when any step
    failed (preview QA is opt-in + non-critical — we never block
    the notification on a preview-screenshot hiccup).
    """
    # Lazy resolution of the gate so a missing settings service
    # defaults to "off" — matches the pre-extraction behaviour.
    try:
        enabled_raw = await _get_setting(
            settings_service=settings_service,
            database_service=database_service,
            key="qa_preview_screenshot_enabled",
            default="false",
        )
    except Exception:
        enabled_raw = "false"
    if (enabled_raw or "false").strip().lower() not in ("true", "1", "yes"):
        return ""

    try:
        # The container lookup matches the inline block's pattern;
        # MultiModelQA needs a settings_service kwarg so we resolve
        # one if the caller didn't pass it in.
        from modules.content.api import MultiModelQA
        from services.container import get_service

        _settings_svc = settings_service or get_service("settings")
        # Resolve preview URL to one reachable from inside the worker
        # container. The external preview_base_url may be a Tailscale
        # hostname that isn't resolvable here.
        internal_preview_url = f"http://localhost:8002/preview/{preview_token}"
        pool = getattr(database_service, "pool", None)
        # DI (#272): MultiModelQA requires a SiteConfig — threaded down from
        # ``_notify_operator`` (the wired lifespan-bound instance).
        pqa = MultiModelQA(
            pool=pool, settings_service=_settings_svc, site_config=site_config,
        )
        review = await pqa._check_rendered_preview(
            title=topic, topic=topic, preview_url=internal_preview_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PREVIEW_QA] skipped (non-critical): %s", exc)
        return ""

    if review is None:
        return ""

    qa_note = (
        f"Visual QA: {int(review.score)}/100 — " + (review.feedback or "")[:200]
    )
    try:
        qa_pool = (
            getattr(database_service, "cloud_pool", None)
            or getattr(database_service, "pool", None)
        )
        if qa_pool is None:
            raise RuntimeError("preview QA: no DB pool available")
        await qa_pool.execute(
            """UPDATE content_tasks
               SET metadata = COALESCE(metadata, '{}'::jsonb)
                   || jsonb_build_object(
                       'preview_qa_score', $1::numeric,
                       'preview_qa_approved', $2::boolean,
                       'preview_qa_feedback', $3::text
                   )
               WHERE task_id = $4""",
            float(review.score),
            bool(review.approved),
            (review.feedback or "")[:500],
            task_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PREVIEW_QA] persist failed: %s", exc)

    logger.info(
        "[PREVIEW_QA] Task %s visual score=%s approved=%s",
        task_id[:8], review.score, review.approved,
    )
    return qa_note


async def _notify_operator(
    *,
    database_service: Any,
    settings_service: Any | None,
    site_config: Any,
    task_id: str,
    topic: str,
    quality_score: float,
) -> None:
    """Build the awaiting-approval Discord/Telegram message and send it.

    Includes the preview link (read from the finalize stage's
    ``pipeline_versions.stage_data->metadata->>'preview_token'``
    write), the QA score, and the optional preview-screenshot QA
    note. Critical=True so Telegram pages the operator — the
    awaiting_approval signal is the operator's queue indicator.
    """
    preview_token = await _read_preview_token(database_service, task_id)
    preview_url = ""
    if preview_token:
        # ``site_config.get`` is sync — no DB hit, reads from in-memory
        # cache. Honours the DI seam: callers thread the wired
        # SiteConfig instance through so Prefect subprocesses + the
        # legacy worker share the same configured base URL.
        try:
            base = site_config.get(
                "preview_base_url", _DEFAULT_PREVIEW_BASE_URL,
            )
        except Exception:
            base = _DEFAULT_PREVIEW_BASE_URL
        preview_url = f"{base}/preview/{preview_token}"

    preview_qa_note = ""
    if preview_token:
        preview_qa_note = await _maybe_run_preview_qa(
            database_service=database_service,
            settings_service=settings_service,
            site_config=site_config,
            task_id=task_id,
            topic=topic,
            preview_token=preview_token,
        )

    msg = f"Awaiting approval: \"{topic}\"\n"
    msg += f"Score: {quality_score:.0f}/100\n"
    if preview_qa_note:
        msg += preview_qa_note + "\n"
    if preview_url:
        msg += f"Preview: {preview_url}\n"
    else:
        msg += "Preview: (no preview link available)\n"
    msg += f"Approve: /approve-post {task_id[:8]}"

    try:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(msg, critical=True, site_config=site_config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] operator notification failed for %s: %s",
            task_id, exc,
        )


async def _read_task_status(
    database_service: Any, task_id: str,
) -> tuple[str | None, str | None]:
    """Return the canonical ``(status, error_message)`` for ``task_id``.

    The pipeline's terminal decision lives on ``pipeline_tasks.status`` —
    the spinal-cord canonical signal — NOT reliably on the in-memory
    ``result`` dict. ``qa.aggregate`` halts the graph and writes
    ``status='rejected'`` to the DB directly
    (``modules/content/atoms/_qa_persist.py``), so the only trustworthy read
    of the outcome is the DB.

    Best-effort: returns ``(None, None)`` when the pool/row is unavailable so
    the caller fails OPEN to the success path — a successfully-completed post
    must never be silenced by a transient read hiccup. The miss is logged
    (``feedback_no_silent_defaults``).
    """
    pool = (
        getattr(database_service, "cloud_pool", None)
        or getattr(database_service, "pool", None)
    )
    if pool is None:
        return None, None
    try:
        row = await pool.fetchrow(
            "SELECT status, error_message FROM pipeline_tasks WHERE task_id = $1",
            task_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] canonical status read failed for %s — proceeding "
            "with success-path side-effects: %s",
            task_id, exc,
        )
        return None, None
    if row is None:
        return None, None
    # ``.get`` (supported by both asyncpg.Record and dict) keeps this tolerant
    # of a row that doesn't carry the columns — the SELECT always does, but the
    # accessor never raises if a caller's double returns a narrower row.
    return row.get("status"), row.get("error_message")


async def _notify_operator_rejected(
    *,
    site_config: Any,
    task_id: str,
    topic: str,
    quality_score: float,
    reason: str | None,
) -> None:
    """Send a routine 'QA rejected' notice in place of the success ping.

    Fired when the canonical status is ``rejected`` (a QA hard-reject that
    halted the graph). Routes to Discord (``critical=False``) — a reject
    needs no operator action, unlike the awaiting-approval queue signal which
    pages Telegram (``critical=True``). Mirrors the operator's
    Telegram=critical / Discord=routine convention.
    """
    # ``build_reject_reason`` prefixes the DB reason with a redundant
    # "QA rejected (score N/100). " — strip it so the notice isn't doubled up
    # with the header + Score line below.
    detail = re.sub(
        r"^QA rejected \(score [\d.]+/100\)\.\s*", "", (reason or "").strip(),
    )
    msg = f"QA rejected: \"{topic}\"\n"
    msg += f"Score: {quality_score:.0f}/100\n"
    if detail:
        msg += f"Reason: {detail[:400]}\n"
    msg += f"Task {task_id[:8]} — rejected at the QA gate, no action needed."

    try:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(msg, critical=False, site_config=site_config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[POST_PIPELINE] reject notification failed for %s: %s",
            task_id, exc,
        )


async def run_post_pipeline_actions(
    *,
    database_service: Any,
    task_id: str,
    topic: str,
    result: dict[str, Any] | None,
    site_config: Any,
    settings_service: Any | None = None,
) -> None:
    """Run the four post-pipeline-success side-effects in order.

    Args:
        database_service: The service that owns the asyncpg pool +
            high-level helpers. Required.
        task_id: The pipeline_tasks.task_id of the successful task.
        topic: The task's topic string (used in notifications +
            webhook payloads).
        result: The dict returned by
            ``process_content_generation_task``. May be ``None`` for
            edge cases where the caller can't reconstruct it; in
            that case ``quality_score`` defaults to ``0.0`` and the
            auto-curator + auto-publish gates won't fire (a score
            of 0 is below every reasonable threshold AND fails the
            ``0 <`` lower bound on the curator).
        site_config: The wired SiteConfig instance. Required —
            never imported from a module-level singleton. Prefect
            subprocesses pass the instance returned by
            ``build_and_wire_for_subprocess``.
        settings_service: Optional explicit settings service. When
            ``None`` falls back to ``database_service.get_setting_value``.

    Returns: ``None``. Side-effects only.
    """
    quality_score = 0.0
    if isinstance(result, dict):
        try:
            quality_score = float(result.get("quality_score", 0))
        except (TypeError, ValueError):
            quality_score = 0.0

    # 0. Canonical terminal-status guard (defense-in-depth). The pipeline's
    # outcome is authoritative on pipeline_tasks.status, NOT result["status"]
    # — qa.aggregate halts the graph and writes status='rejected' to the DB
    # directly (the in-memory status channel is not load-bearing). Re-read it
    # so an already-decided task never receives the success-path side-effects:
    #   - rejected  → a routine 'QA rejected' Discord notice (no false
    #                 awaiting-approval ping, no task.completed webhook, and
    #                 crucially no auto-publish of QA-rejected content in an
    #                 auto-publish-opt-in niche)
    #   - failed / cancelled / published / approved → skip silently (each is
    #                 handled by its own path)
    # Fails OPEN (proceeds) when the status can't be read, so a transient DB
    # hiccup never silences a genuinely-awaiting post.
    canonical_status, reject_reason = await _read_task_status(
        database_service, task_id,
    )
    if canonical_status == "rejected":
        logger.info(
            "[POST_PIPELINE] task %s is rejected (canonical) — sending a "
            "reject notice instead of the awaiting-approval ping", task_id,
        )
        await _notify_operator_rejected(
            site_config=site_config,
            task_id=task_id,
            topic=topic,
            quality_score=quality_score,
            reason=reject_reason,
        )
        return
    if canonical_status in _DECIDED_NON_REJECTED_STATUSES:
        logger.info(
            "[POST_PIPELINE] task %s already in terminal status %r — skipping "
            "success-path side-effects", task_id, canonical_status,
        )
        return

    # 1. task.completed webhook — fires unconditionally on success.
    try:
        _pool = (
            getattr(database_service, "cloud_pool", None)
            or getattr(database_service, "pool", None)
        )
        final_status = (
            result.get("status", "awaiting_approval")
            if isinstance(result, dict)
            else "awaiting_approval"
        )
        await emit_webhook_event(
            _pool,
            "task.completed",
            {
                "task_id": task_id,
                "topic": topic,
                "quality_score": quality_score,
                "status": final_status,
            },
        )
    except Exception:
        logger.warning(
            "[WEBHOOK] Failed to emit task.completed event for %s",
            task_id, exc_info=True,
        )

    # 2. Auto-curator: reject low-quality posts before operator sees them.
    rejected = await _auto_curate(
        database_service=database_service,
        settings_service=settings_service,
        task_id=task_id,
        topic=topic,
        quality_score=quality_score,
    )
    if rejected:
        return

    # 3. Auto-publish: trusted niches with high scores ship without review.
    # finalize_task stamps the per-template auto_publish_gate decision
    # onto the result dict; threading it through lets the per-niche
    # opt-in (e.g. dev_diary_auto_publish_threshold=70 + dry_run=false)
    # bypass the global require_human_approval flag. Without this
    # bypass the niche-specific thresholds were dead code — every
    # post hit awaiting_approval regardless of the opt-in.
    auto_publish_gate_decision = None
    if isinstance(result, dict):
        gate = result.get("auto_publish_gate")
        if isinstance(gate, dict):
            auto_publish_gate_decision = gate
    auto_published = await _maybe_auto_publish(
        database_service=database_service,
        settings_service=settings_service,
        task_id=task_id,
        quality_score=quality_score,
        site_config=site_config,
        auto_publish_gate=auto_publish_gate_decision,
    )

    # 4. Operator notification — single Discord/Telegram message with
    # preview link. Skipped when auto-published (publish_service sends
    # its own published-post notification).
    if auto_published:
        return
    await _notify_operator(
        database_service=database_service,
        settings_service=settings_service,
        site_config=site_config,
        task_id=task_id,
        topic=topic,
        quality_score=quality_score,
    )


__all__ = ["run_post_pipeline_actions"]
