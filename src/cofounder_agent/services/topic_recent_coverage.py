"""Recent-coverage topic guard — semantic dedup of candidate topics against
recently PUBLISHED posts and IN-FLIGHT tasks (incident 2026-07-23).

Why a fourth dedup signal
-------------------------
The content-embedding engine (:mod:`services.topic_dedup_content`) compares a
candidate TITLE against published-post CONTENT chunks. Measured on the
2026-07-23 incident (task ``342a26b7`` "Ditching Grafana for Native
Telemetry", terminally rejected by the operator as duplicate coverage of "The
Shift to Native Telemetry", published 8 days earlier), that signal cannot
separate duplicates from same-domain neighbours at any threshold:

- true duplicate, title vs post content:           ``0.635`` (< 0.70 → missed)
- true duplicate, title+angle vs post content:     ``0.696`` (still < 0.70)
- unrelated same-domain control vs post content:   ``0.755`` (> 0.70 — simply
  lowering the content threshold would false-positive before it catches)

What separates cleanly is LIKE-FOR-LIKE short text: the candidate composite
(title + distilled angle) against the composite each recent post was generated
FROM (post title + source-task topic + winning candidate's angle):

- dup candidate vs "The Shift to a Native UI" composite:       ``0.860``
- dup candidate vs "The Shift to Native Telemetry" composite:  ``0.794``
- next-day re-proposal "The Death of the Grafana Iframe":      ``0.846``
- unrelated same-domain control:                               ``0.697``
- highest legitimately-coexisting published title pair:        ``≤ 0.61``
  (excluding the two escaped duplicates themselves at 0.743)

``0.80`` splits those bands → :data:`DEFAULT_THRESHOLD`.

The index also covers IN-FLIGHT tasks (``pending`` / ``in_progress`` /
``awaiting_approval`` / ``approved``): "The Shift to Native Telemetry" was
proposed 2026-07-09 while its predecessor "The Shift to a Native UI" was
generated but not yet published — a published-only check misses that window.

Niche scoping: refs from a DIFFERENT niche are excluded when the caller
supplies ``niche_slug`` (a dev_diary founder-log entry must not block a
glad-labs evergreen treatment of the same work). Refs with no niche (legacy /
manual posts) always participate.

Tunables (seeded in ``settings_defaults.py``):

- ``topic_recent_coverage_enabled``       (default ``true``) — master switch
- ``topic_recent_coverage_threshold``     (default ``0.80``) — cosine floor
- ``topic_recent_coverage_lookback_days`` (default ``90``; ``0`` = all time)

Fail-open: an embed/DB error must never sink a sweep or wedge a batch —
callers get ``None`` and proposal continues, with a warning logged (same
posture as :mod:`services.topic_dedup_guard`).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

ENABLED_KEY = "topic_recent_coverage_enabled"
THRESHOLD_KEY = "topic_recent_coverage_threshold"
LOOKBACK_KEY = "topic_recent_coverage_lookback_days"

DEFAULT_ENABLED = True
DEFAULT_THRESHOLD = 0.80
DEFAULT_LOOKBACK_DAYS = 90

# Statuses whose tasks claim a theme before any post exists. Mirrors the
# active set in TopicDeduplicator._load_existing_titles, plus 'approved'
# (staged for publish — see the System Health "Approved — awaiting publish"
# queue) so a theme stays claimed across the approve→publish gap.
_IN_FLIGHT_STATUSES = ("pending", "in_progress", "awaiting_approval", "approved")

_PUBLISHED_REFS_SQL = """
    SELECT p.id::text        AS ref_id,
           p.title           AS title,
           p.published_at    AS published_at,
           pt.topic          AS task_topic,
           pt.niche_slug     AS niche_slug,
           COALESCE(itc.distilled_angle, tc.summary, '') AS angle
    FROM posts p
    LEFT JOIN pipeline_tasks pt ON pt.task_id = p.metadata->>'pipeline_task_id'
    LEFT JOIN topic_batches tb ON tb.id = pt.topic_batch_id
    LEFT JOIN internal_topic_candidates itc
           ON tb.picked_candidate_kind = 'internal'
          AND itc.id = tb.picked_candidate_id
    LEFT JOIN topic_candidates tc
           ON tb.picked_candidate_kind = 'external'
          AND tc.id = tb.picked_candidate_id
    WHERE p.status = 'published'
      AND ($1 <= 0 OR p.published_at IS NULL
           OR p.published_at >= NOW() - make_interval(days => $1))
"""

_IN_FLIGHT_REFS_SQL = """
    SELECT pt.task_id        AS ref_id,
           pt.topic          AS title,
           NULL::timestamptz AS published_at,
           NULL::text        AS task_topic,
           pt.niche_slug     AS niche_slug,
           COALESCE(itc.distilled_angle, tc.summary, '') AS angle
    FROM pipeline_tasks pt
    LEFT JOIN topic_batches tb ON tb.id = pt.topic_batch_id
    LEFT JOIN internal_topic_candidates itc
           ON tb.picked_candidate_kind = 'internal'
          AND itc.id = tb.picked_candidate_id
    LEFT JOIN topic_candidates tc
           ON tb.picked_candidate_kind = 'external'
          AND tc.id = tb.picked_candidate_id
    WHERE pt.status = ANY($1::text[])
      AND COALESCE(pt.topic, '') <> ''
"""


@dataclass(slots=True)
class CoverageRef:
    """One reference the index compares candidates against."""

    kind: str  # 'published_post' | 'in_flight_task'
    ref_id: str
    title: str
    niche_slug: str | None
    published_at: datetime | None
    text: str
    embedding: list[float]


@dataclass(slots=True)
class RecentCoverageMatch:
    """A candidate ↔ reference near-duplicate hit at/above threshold."""

    kind: str
    ref_id: str
    title: str
    similarity: float
    published_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref_id": self.ref_id,
            "title": self.title,
            "similarity": round(self.similarity, 3),
            "published_at": (
                self.published_at.isoformat() if self.published_at else None
            ),
        }


class RecentCoverageError(ValueError):
    """A topic near-duplicates recently published / in-flight coverage.

    ``ValueError`` subclass so the resolve HTTP routes map it → 400 and the
    CLI prints it as a friendly error, unchanged (same contract as
    ``TopicSanityError``). ``topic_auto_resolve`` catches this type to expire
    the offending batch instead of retrying it every cycle.
    """

    def __init__(
        self, *, topic: str, match: RecentCoverageMatch, threshold: float,
    ) -> None:
        self.topic = topic
        self.match = match
        self.threshold = float(threshold)
        super().__init__(self._message())

    def _message(self) -> str:
        when = (
            f"published {self.match.published_at.date().isoformat()}"
            if self.match.published_at
            else "in flight"
        )
        return (
            f"Topic {self.topic!r} near-duplicates recent coverage: "
            f"{self.match.title!r} ({self.match.kind}, {when}) at cosine "
            f"{self.match.similarity:.3f} ≥ threshold {self.threshold:.2f}. "
            "Rank a different candidate or edit the winner to a fresh angle "
            "before resolving; tune app_settings."
            f"{THRESHOLD_KEY} / {LOOKBACK_KEY}, or disable via "
            f"{ENABLED_KEY}=false."
        )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    # strict: a length mismatch means two different embed models produced
    # these vectors — a real wiring bug that must surface (the callers'
    # fail-open wrappers turn it into a logged skip, not a crash).
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def compose_text(title: str, *extra: str | None) -> str:
    """Join title + angle/topic fragments into the composite text the
    calibration above was measured on. Skips blanks and fragments that
    merely repeat the title."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (title, *extra):
        frag = (raw or "").strip()
        if not frag:
            continue
        key = frag.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(frag)
    return " — ".join(parts)


def _resolve_enabled(site_config: Any) -> bool:
    try:
        return bool(site_config.get_bool(ENABLED_KEY, DEFAULT_ENABLED))
    except Exception:
        return DEFAULT_ENABLED


def _resolve_threshold(site_config: Any) -> float:
    try:
        return float(site_config.get_float(THRESHOLD_KEY, DEFAULT_THRESHOLD))
    except Exception:
        return DEFAULT_THRESHOLD


def _resolve_lookback_days(site_config: Any) -> int:
    try:
        return int(site_config.get_int(LOOKBACK_KEY, DEFAULT_LOOKBACK_DAYS))
    except Exception:
        return DEFAULT_LOOKBACK_DAYS


class RecentCoverageIndex:
    """Composite texts + embeddings of recent published posts and in-flight
    tasks, loaded once per pass and matched against many candidates.

    Build via :meth:`load`; it returns ``None`` when the guard is disabled or
    when loading fails (fail-open) — callers skip the pass on ``None``.
    """

    def __init__(
        self,
        refs: list[CoverageRef],
        *,
        threshold: float,
        embed: Any,
    ) -> None:
        self.refs = refs
        self.threshold = float(threshold)
        self._embed = embed

    @classmethod
    async def load(
        cls,
        pool: Any,
        *,
        site_config: Any,
        memory_client: Any,
        niche_slug: str | None = None,
    ) -> RecentCoverageIndex | None:
        """Load + embed the reference corpus.

        ``memory_client`` supplies ``embed(text) -> list[float]`` (production:
        ``poindexter.memory.MemoryClient``; tests inject a fake). ``niche_slug``
        filters refs to the candidate's niche + niche-less refs; ``None``
        keeps every ref.
        """
        if not _resolve_enabled(site_config):
            return None
        try:
            lookback = _resolve_lookback_days(site_config)
            threshold = _resolve_threshold(site_config)
            async with pool.acquire() as conn:
                published = await conn.fetch(_PUBLISHED_REFS_SQL, lookback)
                in_flight = await conn.fetch(
                    _IN_FLIGHT_REFS_SQL, list(_IN_FLIGHT_STATUSES),
                )

            refs: list[CoverageRef] = []
            for kind, rows in (
                ("published_post", published),
                ("in_flight_task", in_flight),
            ):
                for row in rows:
                    ref_niche = row["niche_slug"]
                    if (
                        niche_slug is not None
                        and ref_niche is not None
                        and ref_niche != niche_slug
                    ):
                        continue
                    text = compose_text(
                        row["title"], row["task_topic"], row["angle"],
                    )
                    if not text:
                        continue
                    refs.append(
                        CoverageRef(
                            kind=kind,
                            ref_id=str(row["ref_id"]),
                            title=(row["title"] or "").strip(),
                            niche_slug=ref_niche,
                            published_at=row["published_at"],
                            text=text,
                            embedding=await memory_client.embed(text),
                        )
                    )
            return cls(refs, threshold=threshold, embed=memory_client.embed)
        except Exception as exc:  # noqa: BLE001 — fail open, never sink a sweep
            logger.warning(
                "[recent_coverage] index load failed — skipping the "
                "recent-coverage pass (fail-open): %s",
                exc,
            )
            return None

    async def embed_and_match(self, text: str) -> RecentCoverageMatch | None:
        """Embed ``text`` and return the best reference at/above threshold,
        else ``None``. Per-candidate failures fail open (``None``)."""
        text = (text or "").strip()
        if not text or not self.refs:
            return None
        try:
            candidate = await self._embed(text)
        except Exception as exc:  # noqa: BLE001 — fail open per candidate
            logger.warning(
                "[recent_coverage] embed failed for %r — allowing "
                "(fail-open): %s",
                text[:60],
                exc,
            )
            return None
        best_ref: CoverageRef | None = None
        best_sim = 0.0
        for ref in self.refs:
            sim = _cosine(candidate, ref.embedding)
            if sim > best_sim:
                best_sim = sim
                best_ref = ref
        if best_ref is None or best_sim < self.threshold:
            return None
        return RecentCoverageMatch(
            kind=best_ref.kind,
            ref_id=best_ref.ref_id,
            title=best_ref.title,
            similarity=best_sim,
            published_at=best_ref.published_at,
        )


async def check_recent_coverage(
    text: str,
    *,
    pool: Any,
    site_config: Any,
    niche_slug: str | None = None,
    memory_client: Any | None = None,
) -> RecentCoverageMatch | None:
    """One-shot convenience: load the index and match a single text.

    Returns the match at/above threshold, or ``None`` (including when the
    guard is disabled or infra fails — fail-open, logged inside).
    """
    if memory_client is not None:
        index = await RecentCoverageIndex.load(
            pool,
            site_config=site_config,
            memory_client=memory_client,
            niche_slug=niche_slug,
        )
        if index is None:
            return None
        return await index.embed_and_match(text)

    if not _resolve_enabled(site_config):
        return None
    try:
        from poindexter.memory import MemoryClient

        # MemoryClient.__aenter__ connects eagerly — keep the whole usage
        # inside the try so an unreachable DB/embed backend degrades to
        # "no match" (fail-open) instead of failing the caller closed.
        async with MemoryClient() as mem:
            index = await RecentCoverageIndex.load(
                pool, site_config=site_config, memory_client=mem,
                niche_slug=niche_slug,
            )
            if index is None:
                return None
            return await index.embed_and_match(text)
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.warning(
            "[recent_coverage] check unavailable — skipping (fail-open): %s",
            exc,
        )
        return None


async def assert_no_recent_coverage(
    text: str,
    *,
    topic: str,
    pool: Any,
    site_config: Any,
    niche_slug: str | None = None,
    memory_client: Any | None = None,
) -> None:
    """Raise :class:`RecentCoverageError` when ``text`` (the composite
    topic+angle) near-duplicates recent coverage. ``topic`` is the short
    operator-facing topic used in the error message."""
    match = await check_recent_coverage(
        text,
        pool=pool,
        site_config=site_config,
        niche_slug=niche_slug,
        memory_client=memory_client,
    )
    if match is not None:
        raise RecentCoverageError(
            topic=topic, match=match,
            threshold=_resolve_threshold(site_config),
        )


__all__ = [
    "CoverageRef",
    "RecentCoverageError",
    "RecentCoverageIndex",
    "RecentCoverageMatch",
    "assert_no_recent_coverage",
    "check_recent_coverage",
    "compose_text",
    "DEFAULT_ENABLED",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_THRESHOLD",
    "ENABLED_KEY",
    "LOOKBACK_KEY",
    "THRESHOLD_KEY",
]
