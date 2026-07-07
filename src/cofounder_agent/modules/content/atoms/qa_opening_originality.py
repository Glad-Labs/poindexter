"""qa.opening_originality — flag drafts whose opening near-duplicates a
prior published post (the RAG self-echo class).

The two_pass writer grounds each draft in the top-N nearest-neighbor snippets
from the ``posts`` table. For a topic that lands in a dense cluster, the #1
neighbor is a *sibling post* whose opening chunk dominates the prompt, so the
writer paraphrases it near-verbatim. The 2026-06 "VRAM is the only currency
that matters" cluster is the exemplar: four published posts (choosing-a-
quantization-format → the-vram-currency-problem → why-kv-cache-quantization →
single-gpu-vram-budgeting) opened with the same sentence, each echoing the
last. No per-post QA rail catches this — each post is internally clean; the
defect is only visible *against the corpus*.

This rail embeds the draft's opening and finds its nearest published-post
neighbor by cosine similarity. Above ``opening_originality_max_similarity``
(default 0.83) it flags the draft and names the offending post. The default was
calibrated 2026-07-07 against the live published corpus: the rail's real
opening-vs-stored-chunk similarity has a median of 0.73 and p95 of 0.83, so the
prior 0.90 sat near p99 and caught only 1 of the 4 exemplar VRAM-cluster echoes.

Advisory-first: seeded ``qa_gates.opening_originality.required_to_pass=false``
so it SCORES on every run (visible on the QA Rails dashboard) but does not veto
until an operator graduates it. NOTE: graduating to a hard veto
(``required_to_pass=true``) first needs same-niche/series exclusion — the
recurring ``dev_diary`` "what-we-shipped" posts share an opening cadence *by
template* (they use ``narrate_bundle``, not RAG grounding) and dominate the
flag list, so a naive hard gate would false-veto every shipping log. Master
switch ``opening_originality_enabled`` (default true). Chain position: after
``qa.self_consistency``, before ``qa.web_factcheck``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from modules.content.multi_model_qa import MultiModelQA, ReviewerResult
from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.opening_originality",
    type="atom",
    version="1.0.0",
    description=(
        "Flags a draft whose opening is a near-duplicate of an existing "
        "published post (RAG self-echo). Embeds the opening, finds the nearest "
        "published-post neighbor by cosine, flags above threshold. "
        "Advisory-first (DB-driven via qa_gates.opening_originality)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="opening-originality review result",
        ),
    ),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="compute",
    cost_class="compute",
    idempotent=False,
    side_effects=("embeds the opening + one pgvector nearest-neighbor query",),
    parallelizable=True,
)

# Default cosine ceiling: above this, the opening is treated as a near-copy of
# the nearest published post. DB-tunable via opening_originality_max_similarity.
# 0.83 calibrated 2026-07-07 against the published corpus (rail median 0.73, p95
# 0.83); the prior 0.90 sat near p99 and missed 3 of 4 exemplar echoes.
_DEFAULT_MAX_SIMILARITY = 0.83
# How much of the opening to embed. The echo lives in the first paragraph;
# embedding the whole post would dilute the signal.
_OPENING_CHARS = 400

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
        raw = site_config.get("opening_originality_enabled", "true")
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        # silent-ok: optional master switch — default the advisory rail ON (it
        # only scores, never vetoes) when a stubbed/misconfigured site_config
        # raises, rather than letting a config-read blip crash the QA block.
        return True
    return str(raw).lower() in ("true", "1", "yes")


def _extract_opening(content: str, *, max_chars: int = _OPENING_CHARS) -> str:
    """The substantive opening prose, minus a leading H1/H2 title + injected image.

    The canonical title is generated separately, so a leading ``# ...`` line is
    boilerplate that would only add noise (and false-match other posts' titles).
    A featured/inline image the pipeline injects ahead of the first paragraph is
    the same kind of noise — worse, embedding its ``<img>``/``![]`` markup as the
    "opening" makes the rail blind to the real prose (see ``_LEADING_IMAGE_RE``).
    We skip leading blank + heading + image lines and take the first ``max_chars``
    of the body.
    """
    text = (content or "").strip()
    if not text:
        return ""
    body_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not body_lines and (
            not stripped
            or stripped.startswith("#")
            or _LEADING_IMAGE_RE.match(stripped)
        ):
            continue  # skip leading blanks + title heading + injected image
        body_lines.append(line)
    body = "\n".join(body_lines).strip() or text
    return body[:max_chars]


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
            f"opening near-duplicate of published post '{nearest_slug}' "
            f"(cosine {max_similarity:.2f} > {threshold:.2f})",
        )
    if nearest_slug:
        reason = (
            f"opening original — nearest published post '{nearest_slug}' "
            f"at cosine {max_similarity:.2f}"
        )
    else:
        reason = "opening original — no near-duplicate published post found"
    return True, originality, reason


async def _evaluate(
    *, content: str, site_config: Any, pool: Any,
) -> tuple[bool, float, str]:
    """Embed the draft opening and score it against the nearest published post.

    Thin DB glue (monkeypatched in the atom unit tests); the pure decision lives
    in :func:`_decide` and the extraction in :func:`_extract_opening`, both
    unit-tested. Mirrors the pgvector nearest-neighbor query the two_pass writer
    already uses (``ORDER BY embedding <=> $1::vector``).
    """
    try:
        threshold = float(site_config.get_float("opening_originality_max_similarity", _DEFAULT_MAX_SIMILARITY))
    except Exception:  # noqa: BLE001 — stubbed site_config
        threshold = _DEFAULT_MAX_SIMILARITY

    opening = _extract_opening(content)
    if not opening or pool is None:
        return _decide(max_similarity=0.0, threshold=threshold, nearest_slug=None)

    from services.topic_ranking import embed_text

    qvec = await embed_text(opening, site_config=site_config)
    qvec_str = "[" + ",".join(str(v) for v in qvec) + "]"
    async with pool.acquire() as conn:
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
    if row is None:
        return _decide(max_similarity=0.0, threshold=threshold, nearest_slug=None)
    return _decide(
        max_similarity=float(row["similarity"]),
        threshold=threshold,
        nearest_slug=row["slug"],
    )


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}
    if not _is_enabled(site_config):
        return {}

    pool = getattr(state.get("database_service"), "pool", None)
    try:
        passed, score, reason = await _evaluate(
            content=content, site_config=site_config, pool=pool,
        )
    except Exception as exc:  # noqa: BLE001 — a rail must never zero a run
        logger.warning(
            "[qa.opening_originality] evaluate() raised: %s — skipping",
            exc, exc_info=True,
        )
        return {}

    review = ReviewerResult(
        reviewer="opening_originality",
        approved=passed,
        score=round(float(score), 1),
        feedback=reason,
        provider="opening_originality_gate",
    )

    # Advisory status is DB-driven via qa_gates.opening_originality.required_to_pass;
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
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "opening_originality")

    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
