"""Rolling GPU lock-hold duration stats — the P1 admission ETA's data source.

P0 (observe) of poindexter#914. ``gpu_scheduler`` fire-and-forgets
:func:`record_release` on EVERY lock release (unlike ``gpu_task_sessions``,
which only records task-attributed sessions); the P1 admission step reads the
resulting per-``(owner, phase)`` rolling stats via :func:`read_stats` to
estimate how long the current holder has left.

The math (:func:`fold_sample`) is PURE — no I/O — so it unit-tests
exhaustively: an EWMA for the mean plus one P² streaming-quantile estimator
(Jain & Chlamtac 1985) each for p50 and p90. P² keeps 5 markers per quantile
(heights + positions), serialized into the ``gpu_lease_stats.q_state`` JSONB
column so estimates survive process restarts. Until a key has 5 samples the
raw samples are kept in the state and quantiles are computed exactly.

DB writes follow the scheduler's lazy-connection pattern
(``_record_task_session``): the scheduler carries no pool reference by
design, so each write opens a short-lived asyncpg connection resolved via
``brain.bootstrap.resolve_database_url``. Best-effort — a failure never
gates the lock lifecycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

_EWMA_ALPHA_DEFAULT = 0.2


# ---------------------------------------------------------------------------
# P² streaming quantile (single quantile, 5 markers)
# ---------------------------------------------------------------------------


def _p2_init(samples: list[float], q: float) -> dict[str, Any]:
    """Initialize P² marker state from exactly 5 samples."""
    s = sorted(samples)
    return {
        "q": q,
        # marker heights
        "h": [s[0], s[1], s[2], s[3], s[4]],
        # marker positions (1-indexed, per the paper)
        "n": [1.0, 2.0, 3.0, 4.0, 5.0],
        # desired positions
        "np": [1.0, 1.0 + 2.0 * q, 1.0 + 4.0 * q, 3.0 + 2.0 * q, 5.0],
        # desired-position increments
        "dn": [0.0, q / 2.0, q, (1.0 + q) / 2.0, 1.0],
    }


def _p2_update(state: dict[str, Any], x: float) -> dict[str, Any]:
    """One P² update step. Returns a NEW state dict (input not mutated)."""
    h = list(state["h"])
    n = list(state["n"])
    np_ = list(state["np"])
    dn = state["dn"]

    # 1. Find cell k and clamp extreme markers.
    if x < h[0]:
        h[0] = x
        k = 0
    elif x >= h[4]:
        h[4] = x
        k = 3
    else:
        k = 0
        for i in range(4):
            if h[i] <= x < h[i + 1]:
                k = i
                break

    # 2. Increment positions of markers above the cell; bump desired positions.
    for i in range(k + 1, 5):
        n[i] += 1.0
    for i in range(5):
        np_[i] += dn[i]

    # 3. Adjust interior markers toward their desired positions.
    for i in range(1, 4):
        d = np_[i] - n[i]
        if (d >= 1.0 and n[i + 1] - n[i] > 1.0) or (d <= -1.0 and n[i - 1] - n[i] < -1.0):
            d_sign = 1.0 if d >= 0 else -1.0
            # Parabolic (P²) prediction…
            cand = h[i] + d_sign / (n[i + 1] - n[i - 1]) * (
                (n[i] - n[i - 1] + d_sign) * (h[i + 1] - h[i]) / (n[i + 1] - n[i])
                + (n[i + 1] - n[i] - d_sign) * (h[i] - h[i - 1]) / (n[i] - n[i - 1])
            )
            # …falling back to linear when the parabola breaks monotonicity.
            if h[i - 1] < cand < h[i + 1]:
                h[i] = cand
            else:
                j = i + int(d_sign)
                h[i] = h[i] + d_sign * (h[j] - h[i]) / (n[j] - n[i])
            n[i] += d_sign

    return {"q": state["q"], "h": h, "n": n, "np": np_, "dn": dn}


def _p2_estimate(state: dict[str, Any]) -> float:
    """Current quantile estimate — the middle marker's height."""
    return float(state["h"][2])


def _exact_quantile(samples: list[float], q: float) -> float:
    """Exact quantile for the warm-up window (< 5 samples)."""
    s = sorted(samples)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return float(s[idx])


# ---------------------------------------------------------------------------
# Public fold + dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaseStats:
    """One (owner, phase) key's rolling stats. ``q_state`` is opaque fold
    state: ``{"warmup": [raw…]}`` below 5 samples, then
    ``{"p50": <P² state>, "p90": <P² state>}``."""

    samples: int = 0
    ewma_ms: float | None = None
    p50_ms: float | None = None
    p90_ms: float | None = None
    q_state: dict[str, Any] = field(default_factory=dict)


def fold_sample(
    prev: LeaseStats, duration_ms: float, *, alpha: float = _EWMA_ALPHA_DEFAULT
) -> LeaseStats:
    """PURE fold of one release duration into the rolling stats."""
    x = float(duration_ms)
    ewma = x if prev.ewma_ms is None else (alpha * x + (1.0 - alpha) * prev.ewma_ms)
    n = prev.samples + 1

    state = dict(prev.q_state or {})
    if "p50" not in state:
        warmup = list(state.get("warmup", [])) + [x]
        if len(warmup) < 5:
            return LeaseStats(
                samples=n,
                ewma_ms=ewma,
                p50_ms=_exact_quantile(warmup, 0.5),
                p90_ms=_exact_quantile(warmup, 0.9),
                q_state={"warmup": warmup},
            )
        # Fifth sample: initialize both P² estimators from the window.
        state = {"p50": _p2_init(warmup, 0.5), "p90": _p2_init(warmup, 0.9)}
    else:
        state = {"p50": _p2_update(state["p50"], x), "p90": _p2_update(state["p90"], x)}

    return LeaseStats(
        samples=n,
        ewma_ms=ewma,
        p50_ms=_p2_estimate(state["p50"]),
        p90_ms=_p2_estimate(state["p90"]),
        q_state=state,
    )


# ---------------------------------------------------------------------------
# DB read/write (lazy connection — the _record_task_session pattern)
# ---------------------------------------------------------------------------


_SELECT_SQL = (
    "SELECT samples, ewma_ms, p50_ms, p90_ms, q_state "
    "FROM gpu_lease_stats WHERE owner = $1 AND phase = $2"
)
_UPSERT_SQL = """
    INSERT INTO gpu_lease_stats
        (owner, phase, samples, ewma_ms, p50_ms, p90_ms, q_state, updated_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now())
    ON CONFLICT (owner, phase) DO UPDATE SET
        samples = EXCLUDED.samples,
        ewma_ms = EXCLUDED.ewma_ms,
        p50_ms = EXCLUDED.p50_ms,
        p90_ms = EXCLUDED.p90_ms,
        q_state = EXCLUDED.q_state,
        updated_at = now()
"""


def _row_to_stats(row: Any) -> LeaseStats:
    raw_state = row["q_state"]
    if isinstance(raw_state, str):
        raw_state = json.loads(raw_state)
    return LeaseStats(
        samples=int(row["samples"] or 0),
        ewma_ms=row["ewma_ms"],
        p50_ms=row["p50_ms"],
        p90_ms=row["p90_ms"],
        q_state=raw_state or {},
    )


async def _connect() -> Any | None:
    """Short-lived asyncpg connection, or None when unavailable.

    Hermetic under pytest: ``resolve_database_url()`` reads bootstrap.toml,
    which on an operator box resolves the REAL prod DSN — a unit test that
    exercises the lock would silently write prod ``gpu_lease_stats`` rows
    (and pay a live connect) on every release. Unit tests that exercise this
    module's own seam monkeypatch ``_connect``/``record_release`` directly;
    integration_db coverage talks to its throwaway DB via fixtures, not this
    path. So under pytest this returns None (capture no-ops, read_stats →
    None → admission fail-open), keeping every suite hermetic by default.
    """
    import os

    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    try:
        import asyncpg  # type: ignore[import-untyped]
        from brain.bootstrap import resolve_database_url  # type: ignore[import-untyped]
    except Exception:
        # silent-ok: core deps missing = the worker is already systemically
        # broken; a per-release finding would only add noise on top.
        return None
    dsn = resolve_database_url()
    if not dsn:
        return None
    return await asyncpg.connect(dsn, timeout=5)


async def record_release(owner: str, phase: str, duration_ms: float) -> None:
    """Fold one release duration into the key's DB row. Best-effort."""
    try:
        conn = await _connect()
        if conn is None:
            return
        try:
            row = await conn.fetchrow(_SELECT_SQL, owner, phase or "")
            prev = _row_to_stats(row) if row else LeaseStats()
            nxt = fold_sample(prev, duration_ms)
            await conn.execute(
                _UPSERT_SQL,
                owner,
                phase or "",
                nxt.samples,
                nxt.ewma_ms,
                nxt.p50_ms,
                nxt.p90_ms,
                json.dumps(nxt.q_state),
            )
        finally:
            await conn.close()
    except Exception:
        # silent-ok: observability capture must never gate the lock
        # lifecycle; a missed sample only slightly delays ETA convergence.
        logger.debug("[gpu_lease_stats] record_release failed", exc_info=True)


async def list_stats(limit: int = 100) -> list[dict[str, Any]]:
    """Every key's current headline stats (no fold state) — the console/route
    snapshot. Empty list on any failure (honest-empty)."""
    try:
        conn = await _connect()
        if conn is None:
            return []
        try:
            rows = await conn.fetch(
                "SELECT owner, phase, samples, ewma_ms, p50_ms, p90_ms, updated_at "
                "FROM gpu_lease_stats ORDER BY owner, phase LIMIT $1",
                limit,
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]
    except Exception:
        # silent-ok: a missing snapshot renders honest-empty downstream.
        logger.debug("[gpu_lease_stats] list_stats failed", exc_info=True)
        return []


async def read_stats(owner: str, phase: str) -> LeaseStats | None:
    """The key's current rolling stats, or None (unknown key / DB down)."""
    try:
        conn = await _connect()
        if conn is None:
            return None
        try:
            row = await conn.fetchrow(_SELECT_SQL, owner, phase or "")
            return _row_to_stats(row) if row else None
        finally:
            await conn.close()
    except Exception:
        # silent-ok: an unreadable stat degrades admission to its
        # conservative fallback ETA — never an error path.
        logger.debug("[gpu_lease_stats] read_stats failed", exc_info=True)
        return None
