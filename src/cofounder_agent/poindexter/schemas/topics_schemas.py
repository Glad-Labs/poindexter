"""Topic-triage response schemas — canonical list envelope (poindexter#745).

The open-topic-batch proposals listing carries deeply nested batch + candidate
structures, so its items are typed as free-form ``dict[str, Any]`` — the
envelope is what gets canonicalized.
"""

from typing import Any

from schemas.database_response_models import ListResponse


class TopicProposalListResponse(ListResponse[dict[str, Any]]):
    """Open-topic-batch proposals — canonical offset envelope (poindexter#745).

    ``{items, total, limit, offset}`` via ``ListResponse[dict[str, Any]]``.
    Replaces the prior untyped ``dict[str, Any]`` body that used ``count`` +
    ``batches`` keys. Each item is one open batch (niche slug/name + its
    effective-score-sorted candidate list), a deeply nested serialized dict, so
    items stay free-form — the envelope is the part that's canonicalized. The
    endpoint returns every open batch unpaginated, so ``offset`` is always 0 and
    ``limit`` equals ``total``. Operator topics-triage surface (OAuth-JWT);
    consumed by the operator console (``console/js`` — updated in lockstep).
    """
