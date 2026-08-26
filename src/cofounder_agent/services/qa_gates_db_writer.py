"""Write-side companion to ``qa_gates_db`` — bumps run counters.

The qa_gates schema reserves ``last_run_at`` / ``last_run_status`` /
``total_runs`` / ``total_rejections`` for telemetry. The runtime side
(``services.qa_gates_db``) was deliberately read-only and the original
plan was for an "audit pipeline" to update these columns. That pipeline
was never built, so as of 2026-05-09 every gate row showed
``last_run_at = NEVER`` despite the chain executing daily.

This module restores the intent: a single call-site at the end of
``MultiModelQA.review`` walks the produced ``ReviewerResult`` list and
emits one UPDATE per gate. We keep the read/write split — readers still
go through ``qa_gates_db.load_qa_gate_chain`` — but writes get their
own seam so the contract is testable in isolation.

Design notes:

- **Reviewer name → qa_gates.name aliasing.** Inline reviewer strings
  like ``image_relevance`` map to gate-row names like ``vision_gate``.
  The alias table here is the single source of truth; if a new gate
  ships, add the row + the alias here.
- **Skipped gates don't get counters bumped.** When a gate's row has
  ``enabled=False``, ``MultiModelQA`` short-circuits before producing
  a ``ReviewerResult``, so the reviewer name never reaches this writer.
  That is intentional — counters track *executions*, not *checks*.
- **Best-effort.** A counter-update failure must never bring down the
  pipeline. We log and move on.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from services.logger_config import get_logger
from utils.exception_format import describe_exception

logger = get_logger(__name__)


# Reviewer name (as written in ReviewerResult.reviewer) → qa_gates.name.
# When the two are the same we still list the row explicitly so that
# greps over either side land on this table.
_REVIEWER_TO_GATE: dict[str, str] = {
    "programmatic_validator": "programmatic_validator",
    "url_verifier": "url_verifier",
    "web_factcheck": "web_factcheck",
    "deepeval_brand_fabrication": "deepeval_brand_fabrication",
    # 2026-05-27 fix — these five gates were seeded in migrations
    # 20260510_022034 / 20260510_030530 / 20260510_032959 but never
    # added to this mapping. Result: every QA pass ran the reviewer,
    # produced a ReviewerResult, fed the score into the weighted
    # average — but record_chain_run() silently dropped the row
    # because the reviewer name had no gate alias. Operator dashboard
    # showed `total_runs=0` for ~17 days. The reviewer names match
    # the gate names exactly (no historic divergence to preserve), so
    # the mapping is identity. Test coverage added below in
    # test_alias_table_covers_every_known_inline_reviewer.
    "deepeval_g_eval": "deepeval_g_eval",
    "deepeval_faithfulness": "deepeval_faithfulness",
    "guardrails_brand": "guardrails_brand",
    "guardrails_competitor": "guardrails_competitor",
    "ragas_eval": "ragas_eval",
    # 2026-06-11 fix — THIRD recurrence of the same alias-drop class. The
    # citation_verifier / topic_delivery / self_consistency rails were
    # restored/added as qa.* atoms (Glad-Labs/poindexter#659 / #658 / #621)
    # and SEEDED their own qa_gates rows (citation_verifier + topic_delivery
    # 2026-06-03; self_consistency 2026-06-07). Each runs on the live
    # graph_def path and feeds the weighted QA score, but record_chain_run()
    # silently dropped the counter because the reviewer name had no alias
    # here — so `poindexter qa-gates list` + the /d/qa-rails dashboard showed
    # total_runs=0 / last_run_at=NEVER while audit_log proved 97 / 49 / 24
    # real runs (last seen the same day). Reviewer names match the gate
    # names exactly (identity alias). When these atoms shipped, the guard
    # test still listed citation_verifier/topic_delivery in
    # inline_reviewers_without_row (true before the rows were seeded, stale
    # after) so it passed while certifying the bug. Pinned by
    # test_restored_rail_gates_bump_their_counters.
    "citation_verifier": "citation_verifier",
    "topic_delivery": "topic_delivery",
    "self_consistency": "self_consistency",
    # poindexter#765 — same identity-alias requirement for the new advisory
    # unlinked-attribution rail (named-source-cited-without-link); it emits a
    # ReviewerResult on every graph_def QA pass and seeds its own qa_gates row.
    "unlinked_attribution": "unlinked_attribution",
    # poindexter#765 follow-up — the grounded-LLM citation rail
    # (content.llm_reconcile_citations) emits a "citation_grounding" advisory
    # ReviewerResult when it detects named sources with no corpus match. Unlike
    # the always-on rails above it only fires on a detection, so its counter
    # tracks ungrounded-detection passes rather than every QA pass — but it
    # still needs the identity alias or record_chain_run() drops the UPDATE and
    # the /d/qa-rails dashboard shows total_runs=0 (the fourth recurrence this
    # guard prevents). Gate row seeded in
    # 20260708_034620_add_citation_grounding_qa_gate.
    "citation_grounding": "citation_grounding",
    # content_originality (renamed from opening_originality, 2026-07-12) — the
    # advisory RAG self-echo rail. Identity alias: the reviewer string and the
    # qa_gates row name are both 'content_originality'. Without this,
    # record_chain_run() drops the UPDATE and /d/qa-rails shows total_runs=0 —
    # the exact evidence gap that blocks graduating it to a hard veto (the fifth
    # recurrence of the alias-drop class; opening_originality was never aliased).
    "content_originality": "content_originality",
    # title_coherence — the SIXTH alias-drop recurrence, found 2026-08-16
    # while wiring self_claim: the rail shipped 2026-07-24 (migration
    # 20260724_161837) and runs on every canonical_blog QA pass, but was
    # never added here OR to the guard test's must_be_documented list, so
    # prod showed total_runs=0 / last_run_at=NEVER against weeks of real
    # runs. Identity alias, same as every other same-named rail.
    "title_coherence": "title_coherence",
    # self_claim (poindexter#1007) — deterministic our-own-system claim
    # verification (versions / scores / settings keys / file paths).
    # Identity alias; gate row seeded advisory-first alongside the rail.
    "self_claim": "self_claim",
    # Aliases — the inline reviewer name and the gate-row name diverged
    # historically; preserve both rather than rename either side.
    "image_relevance": "vision_gate",
    # rendered_preview is the SECOND vision check (the screenshot leg) and
    # shares the vision_gate row with image_relevance. Without this alias a
    # preview-only review left vision_gate looking absent in
    # missing_required_gates, so a required vision_gate failed closed even
    # though the rail ran (Glad-Labs/poindexter#563).
    "rendered_preview": "vision_gate",
    "internal_consistency": "consistency",
    "ollama_critic": "llm_critic",
}


def reviewer_to_gate(reviewer: str | None) -> str | None:
    """Resolve a ``ReviewerResult.reviewer`` name to its ``qa_gates.name``.

    The string a rail writes (e.g. ``ollama_critic``) and the ``qa_gates``
    row it belongs to (e.g. ``llm_critic``) diverge for several rails —
    ``_REVIEWER_TO_GATE`` above is the single source of truth for that
    aliasing. Returns the gate name, or ``None`` when the reviewer has no
    gate row.

    Two callers, two fallbacks: ``record_chain_run`` skips a ``None``
    (counters track rows that exist); the qa.aggregate vacuous-pass guard
    falls back to identity (an unaliased reviewer whose name already equals
    its gate name still counts as present).
    """
    if not reviewer:
        return None
    return _REVIEWER_TO_GATE.get(reviewer)


def _field(review: Any, name: str, default: Any) -> Any:
    """Read a review field from either shape.

    The legacy ``MultiModelQA.review()`` call-site passes ``ReviewerResult``
    *objects* (attribute access). The ``qa.aggregate`` atom — the graph_def
    QA path since #355 — passes ``reviewer_to_dict()`` *dicts* on the
    ``qa_rail_reviews`` channel (key access). Tolerate both so the counter
    fires on every QA path. A dict-only ``getattr`` (the pre-#553 behavior)
    silently returned the default for every dict, so no gate matched and
    ``total_runs`` stayed frozen at 0 on prod.
    """
    if isinstance(review, dict):
        return review.get(name, default)
    return getattr(review, name, default)


async def record_chain_run(
    pool: Any,
    reviews: Iterable[Any],
) -> None:
    """Bump qa_gates counters for every gate that produced a review.

    Args:
        pool: asyncpg pool. ``None`` is tolerated and the call no-ops —
            the same fallback shape as ``load_qa_gate_chain`` so unit
            tests without a DB don't need extra wiring.
        reviews: iterable of ``ReviewerResult`` (or any object exposing
            ``.reviewer: str`` and ``.approved: bool``).
    """
    if pool is None:
        return

    # Group by gate name so a single review (e.g. url_verifier appended
    # twice for dead-link vs bonus paths) updates the row exactly once.
    runs: dict[str, dict[str, Any]] = {}
    for r in reviews:
        gate_name = reviewer_to_gate(_field(r, "reviewer", ""))
        if gate_name is None:
            continue
        bucket = runs.setdefault(
            gate_name,
            {"approved_all": True, "any_advisory_only": True},
        )
        if not _field(r, "approved", True):
            bucket["approved_all"] = False
        if not _field(r, "advisory", False):
            bucket["any_advisory_only"] = False

    if not runs:
        return

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for gate_name, info in runs.items():
                    status = "passed" if info["approved_all"] else "rejected"
                    rejected_inc = 0 if info["approved_all"] else 1
                    await conn.execute(
                        """
                        UPDATE qa_gates
                           SET last_run_at = now(),
                               last_run_status = $2,
                               total_runs = total_runs + 1,
                               total_rejections = total_rejections + $3,
                               last_error = NULL
                         WHERE name = $1
                        """,
                        gate_name, status, rejected_inc,
                    )
    except Exception as exc:  # noqa: BLE001
        logger.debug("qa_gates counter update failed: %s", exc)
        # One try wraps the WHOLE transaction (the per-gate loop is inside it),
        # so a failure loses EVERY gate's counters for this chain run while the
        # pipeline itself looks healthy. qa_gates is not audit_log, so a finding
        # is the right signal — it can't vanish along with the row it reports.
        from utils.findings import emit_finding

        emit_finding(
            source="services.qa_gates_db_writer",
            kind="qa_gates_counter_update_failed",
            title="qa_gates counter update failed",
            body=(
                f"Bumping qa_gates counters for {len(runs)} gate(s) raised "
                f"{describe_exception(exc)}. total_runs / total_rejections / "
                f"last_run_status feed the QA Rails dashboard, so a persistent "
                f"failure makes it under-report every chain run."
            ),
            severity="info",
            dedup_key="qa_gates_counter_update_failed",
        )


__all__ = ["record_chain_run", "reviewer_to_gate"]
