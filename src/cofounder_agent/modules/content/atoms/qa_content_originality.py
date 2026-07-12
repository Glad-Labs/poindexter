"""qa.content_originality — flag drafts whose content near-duplicates a
prior published post (the RAG self-echo class).

NOTE (2026-07-12): renamed from ``qa.opening_originality``. This task is the
pure rename; the opening-only scan below is broadened to a whole-post chunked
scan in a follow-up task. Settings reads accept the old ``opening_originality_*``
keys as a backcompat fallback so a lingering prod row still works.

The two_pass writer grounds each draft in the top-N nearest-neighbor snippets
from the ``posts`` table. For a topic that lands in a dense cluster, the #1
neighbor is a *sibling post* whose opening chunk dominates the prompt, so the
writer paraphrases it near-verbatim. The 2026-06 "VRAM is the only currency
that matters" cluster is the exemplar: four published posts (choosing-a-
quantization-format → the-vram-currency-problem → why-kv-cache-quantization →
single-gpu-vram-budgeting) opened with the same sentence, each echoing the
last. No per-post QA rail catches this — each post is internally clean; the
defect is only visible *against the corpus*.

This rail chunks the WHOLE draft (paragraph-merged, section-windowed via
``_chunk_draft``), embeds each chunk, and scores the MAX chunk-vs-corpus cosine
against the nearest published post — so a self-echo ANYWHERE in the body is
caught, not just in the opening. Above ``content_originality_max_similarity`` it
flags the draft and names the offending post. The 0.83 default is INHERITED from
the earlier opening-only calibration (2026-07-07 against the live corpus:
opening-vs-stored-chunk median 0.73, p95 0.83) and is PROVISIONAL for the
broadened whole-post scan — taking the worst chunk of the whole body shifts the
distribution up, so recalibrate against the live corpus with
``scripts/calibrate_content_originality_threshold.py`` before graduating the rail
to a hard veto. Advisory-only today, so a provisional ceiling only tunes the
dashboard label, never a gate decision.

Advisory-first: seeded ``qa_gates.content_originality.required_to_pass=false``
so it SCORES on every run (visible on the QA Rails dashboard) but does not veto
until an operator graduates it. Graduation to a hard veto
(``required_to_pass=true``) is guarded by ``content_originality_excluded_series``
(CSV of ``template_slug``; default ``dev_diary,narrate_bundle``): a draft in an
excluded recurring series has its would-be hard veto downgraded back to advisory,
because those series share an opening cadence *by template* (dev_diary
"what-we-shipped" logs use ``narrate_bundle``, not RAG grounding) and a naive
hard gate would false-veto every shipping log. The guard is future-defensive
today — ``dev_diary`` runs a 5-node subset with NO ``qa.*`` atoms, so the rail
never runs on it — and only bites after graduation. The draft's series is
resolved fail-closed from ``pipeline_tasks`` by ``task_id`` (``template_slug`` is
not a ``PipelineState`` channel, so a state-seeded value is dropped on the
graph_def path — the niche_slug lesson). Master switch
``content_originality_enabled`` (default true, falls back to the legacy
``opening_originality_enabled``). Chain position: after ``qa.self_consistency``,
before ``qa.web_factcheck``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from modules.content.atoms._pool import resolve_pool
from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from modules.content.multi_model_qa import MultiModelQA, ReviewerResult
from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.content_originality",
    type="atom",
    version="1.0.0",
    description=(
        "Flags a draft whose content is a near-duplicate of an existing "
        "published post (RAG self-echo). Chunks the whole draft, embeds each "
        "chunk, scores the max chunk-vs-corpus cosine against the nearest "
        "published post, flags above threshold. "
        "Advisory-first (DB-driven via qa_gates.content_originality)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="content-originality review result",
        ),
    ),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="compute",
    cost_class="compute",
    idempotent=False,
    side_effects=("embeds each draft chunk + one pgvector NN query per chunk",),
    parallelizable=True,
)

# Default cosine ceiling: above this, the opening is treated as a near-copy of
# the nearest published post. DB-tunable via content_originality_max_similarity.
# 0.83 calibrated 2026-07-07 against the published corpus (rail median 0.73, p95
# 0.83); the prior 0.90 sat near p99 and missed 3 of 4 exemplar echoes.
_DEFAULT_MAX_SIMILARITY = 0.83
# Whole-post chunking floors (max-over-chunks scan). DB-tunable via
# content_originality_chunk_{min,max}_chars. min_chars merges tiny paragraphs
# into a meaningful vector; max_chars windows a long section so it stays focused
# (mirrors the granularity the ``embeddings`` table stores post chunks at).
_DEFAULT_CHUNK_MIN = 200
_DEFAULT_CHUNK_MAX = 600
# Templated recurring series exempt from the HARD veto tier (they still SCORE,
# advisory). dev_diary "what-we-shipped" logs share an opening cadence *by
# template* (they use narrate_bundle, not RAG grounding), so a naive hard gate
# would false-veto every shipping log. CSV of template_slug values, DB-tunable
# via content_originality_excluded_series.
_DEFAULT_EXCLUDED_SERIES = "dev_diary,narrate_bundle"

# Leading media boilerplate the pipeline injects ahead of the prose — a featured
# or inline image (HTML ``<img>``/``<figure>``/``<picture>`` or anchor-wrapped
# image, a markdown ``![alt](url)`` or linked image ``[![...``). Embedding it as
# the "opening" blinds the rail to the actual prose: ``the-vram-currency-problem``
# opens with the identical VRAM sentence yet scored 0.749 (not ~0.90) because its
# body starts with an ``<img>`` tag. Anchored at the FRONT (leading-boilerplate
# skip only) so a mid-article image never truncates real prose.
_LEADING_IMAGE_RE = re.compile(
    r"(?i)^(?:<img\b|<figure\b|<picture\b|<a\b[^>]*>\s*<img\b|!\[|\[!\[)"
)


def _is_enabled(site_config: Any) -> bool:
    try:
        raw = site_config.get(
            "content_originality_enabled",
            site_config.get("opening_originality_enabled", "true"),
        )
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional master switch — default the advisory rail ON (it
        # only scores, never vetoes) when a stubbed/misconfigured site_config
        # raises, rather than letting a config-read blip crash the QA block.
        return True
    return str(raw).lower() in ("true", "1", "yes")


def _chunk_draft(content: str, *, min_chars: int, max_chars: int) -> list[str]:
    """Split a draft into paragraph-based chunks for the whole-post scan.

    Strips leading media boilerplate + a leading H1 (the canonical title is
    generated separately, so a leading ``# ...`` line is noise; an injected
    ``<img>``/``![]`` ahead of the prose is the same — see ``_LEADING_IMAGE_RE``,
    anchored at the paragraph START so prose merely mentioning an image word is
    never stripped). Then merges paragraphs under ``min_chars`` (a one-line
    paragraph is a noise vector) and windows paragraphs over ``max_chars`` (a
    long section still yields a focused vector). Mirrors the granularity the
    ``embeddings`` table stores post chunks at, which this rail compares against.
    Chunk 0 is the opening, so opening self-echoes are still caught.
    """
    text = (content or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    # Drop a leading heading-only or media-only paragraph.
    while paras and (paras[0].startswith("#") or _LEADING_IMAGE_RE.match(paras[0])):
        paras.pop(0)
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if len(para) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
            continue
        buf = f"{buf}\n\n{para}".strip() if buf else para
        if len(buf) >= min_chars:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks and len(buf) < min_chars:
            chunks[-1] = f"{chunks[-1]}\n\n{buf}"
        else:
            chunks.append(buf)
    return chunks


def _decide(
    *, max_similarity: float, threshold: float, nearest_slug: str | None,
) -> tuple[bool, float, str]:
    """Pure verdict from a cosine similarity. Returns ``(passed, originality_score,
    reason)`` where ``originality_score`` is 0-100 (higher = more original).

    Only a STRICTLY greater similarity flags, so a merely-related post at the
    boundary is a pass, not a false "copy".
    """
    originality = round(max(0.0, 1.0 - max_similarity) * 100.0, 1)
    if nearest_slug and max_similarity > threshold:
        return (
            False,
            originality,
            f"content near-duplicate of published post '{nearest_slug}' "
            f"(worst-chunk cosine {max_similarity:.2f} > {threshold:.2f})",
        )
    if nearest_slug:
        reason = (
            f"content original — nearest published post '{nearest_slug}' "
            f"at worst-chunk cosine {max_similarity:.2f}"
        )
    else:
        reason = "content original — no near-duplicate published post found"
    return True, originality, reason


async def _evaluate(
    *, content: str, site_config: Any, pool: Any,
) -> tuple[bool, float, str]:
    """Chunk the whole draft, embed each chunk, and score the MAX chunk-vs-corpus
    cosine against the nearest published post.

    Thin DB glue (monkeypatched in the atom unit tests); the pure decision lives
    in :func:`_decide` and the chunking in :func:`_chunk_draft`, both unit-tested.
    Runs the pgvector nearest-neighbor query the two_pass writer uses
    (``ORDER BY embedding <=> $1::vector``) once per chunk and flags on the worst
    chunk, naming its offending post (max-over-chunks).
    """
    try:
        threshold = float(
            site_config.get_float(
                "content_originality_max_similarity",
                site_config.get_float(
                    "opening_originality_max_similarity", _DEFAULT_MAX_SIMILARITY
                ),
            )
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        threshold = _DEFAULT_MAX_SIMILARITY

    def _int(key: str, default: int) -> int:
        try:
            return int(site_config.get_int(key, default))
        except Exception:  # noqa: BLE001 — stubbed site_config
            return default

    min_chars = _int("content_originality_chunk_min_chars", _DEFAULT_CHUNK_MIN)
    max_chars = _int("content_originality_chunk_max_chars", _DEFAULT_CHUNK_MAX)

    chunks = _chunk_draft(content, min_chars=min_chars, max_chars=max_chars)
    if not chunks or pool is None:
        return _decide(max_similarity=0.0, threshold=threshold, nearest_slug=None)

    from services.topic_ranking import embed_text

    best_sim = 0.0
    best_slug: str | None = None
    async with pool.acquire() as conn:
        for chunk in chunks:
            qvec = await embed_text(chunk, site_config=site_config)
            qvec_str = "[" + ",".join(str(v) for v in qvec) + "]"
            row = await conn.fetchrow(
                """
                SELECT p.slug AS slug,
                       1 - (e.embedding <=> $1::vector) AS similarity
                  FROM embeddings e
                  JOIN posts p ON p.id = e.source_id::uuid
                 WHERE e.source_table = 'posts'
                   AND p.status = 'published'
                 ORDER BY e.embedding <=> $1::vector
                 LIMIT 1
                """,
                qvec_str,
            )
            if row is not None and float(row["similarity"]) > best_sim:
                best_sim = float(row["similarity"])
                best_slug = row["slug"]
    return _decide(max_similarity=best_sim, threshold=threshold, nearest_slug=best_slug)


def _excluded_series(site_config: Any) -> set[str]:
    """The set of ``template_slug`` values exempt from the HARD veto tier
    (advisory-only for them). DB-tunable CSV via
    ``content_originality_excluded_series``."""
    try:
        raw = site_config.get(
            "content_originality_excluded_series", _DEFAULT_EXCLUDED_SERIES
        )
    except Exception:  # noqa: BLE001 — stubbed site_config
        # silent-ok: exclusion is a graduation-safety guard; on a config-read
        # blip default to the known recurring series rather than crash the rail.
        raw = _DEFAULT_EXCLUDED_SERIES
    return {s.strip() for s in str(raw or "").split(",") if s.strip()}


def _series_excluded(template_slug: str, excluded: set[str]) -> bool:
    """True when the draft belongs to a templated recurring series exempt from
    the HARD veto tier. Recurring series (dev_diary 'what-we-shipped') share a
    cadence by template and would false-veto once graduated — they still SCORE
    (advisory), just never hard-block."""
    return bool(template_slug) and template_slug in excluded


async def _resolve_template_slug(state: dict[str, Any], pool: Any) -> str:
    """The draft's ``template_slug``, resolved fail-closed to ``''``.

    ``template_slug`` is NOT a declared ``PipelineState`` channel (only
    ``task_id`` is always-present), so on the graph_def path LangGraph drops a
    state-seeded value at ainvoke — the niche_slug lesson (see the PipelineState
    docstring in ``template_runner``). Prefer an in-state value (unit tests + any
    future channel), else read it from ``pipeline_tasks`` by the always-present
    ``task_id`` — mirroring ``content.evaluate_auto_publish``'s niche resolution.
    Fail-closed to ``''`` (never excluded) so a lookup miss can't suppress a veto.
    """
    slug = str(state.get("template_slug") or "")
    if slug:
        return slug
    task_id = state.get("task_id")
    if not task_id or pool is None:
        return ""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT template_slug FROM pipeline_tasks WHERE task_id = $1",
                task_id,
            )
    except Exception:  # noqa: BLE001 — a rail must never crash a run
        # silent-ok: fail-closed series resolve — a pipeline_tasks lookup blip
        # must not crash the QA block, and '' means "unknown series" → the veto
        # is NOT suppressed (the safe direction). Only reached post-graduation,
        # when a hard veto is already imminent.
        return ""
    return str(row["template_slug"]) if row and row["template_slug"] else ""


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}
    if not _is_enabled(site_config):
        return {}

    pool = resolve_pool(state, atom="qa.content_originality")
    try:
        passed, score, reason = await _evaluate(
            content=content, site_config=site_config, pool=pool,
        )
    except Exception as exc:  # noqa: BLE001 — a rail must never zero a run
        logger.warning(
            "[qa.content_originality] evaluate() raised: %s — skipping",
            exc, exc_info=True,
        )
        return {}

    review = ReviewerResult(
        reviewer="content_originality",
        approved=passed,
        score=round(float(score), 1),
        feedback=reason,
        provider="content_originality_gate",
    )

    # Advisory status is DB-driven via qa_gates.content_originality.required_to_pass;
    # apply it here (qa.aggregate vetoes on approved=False AND advisory=False, and
    # does NOT read required_to_pass back onto the review). Mirrors qa.self_consistency.
    settings_service = state.get("settings_service")
    qa = MultiModelQA(
        pool=pool,
        settings_service=settings_service,
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "content_originality")

    # Series-exclusion (graduation safety): a draft in a templated recurring
    # series is exempt from a HARD veto — it still SCORES (advisory), never
    # blocks. No-op today: the rail is advisory AND dev_diary runs a 5-node
    # subset with NO qa.* atoms, so the rail never even runs on the excluded
    # series. It only bites after a future graduation to required_to_pass=true.
    # Resolve + check ONLY when a hard veto is imminent, so the advisory path
    # spends no extra DB lookup.
    if not review.advisory and not review.approved:
        template_slug = await _resolve_template_slug(state, pool)
        if _series_excluded(template_slug, _excluded_series(site_config)):
            # Mirror _mark_advisory_if_configured's transform exactly: set BOTH
            # bits (aggregate_rail_reviews vetoes on a non-advisory FAILING
            # review) and annotate the feedback so the audit log shows the
            # downgrade.
            review.advisory = True
            review.approved = True
            review.feedback = f"[series-excluded:{template_slug}] {review.feedback}"

    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
