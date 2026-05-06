"""Auto-expire stale pending approval gates (Glad-Labs/poindexter#338).

The gate-per-medium spine (PR #156) ships HITL approval gates with no
automatic exit door — a pending gate sits in ``post_approval_gates``
forever until the operator explicitly approves, rejects, or revises
it. In real-life solo-operator flow that means the review queue grows
monotonically: anything Matt forgets about (or no longer cares about)
clogs ``poindexter post list --awaiting`` until he manually clears it.

Per Matt's "alert auto-triage design pattern" rule: every alert needs a
resolution path. Notify-louder doesn't drain the queue, so this probe
implements **auto-reject + notify**:

1. Once per cycle, ``SELECT`` every ``post_approval_gates`` row where
   ``state='pending' AND created_at < NOW() - INTERVAL '<N> hours'``
   (``N`` = ``app_settings.gate_pending_max_age_hours``, default 168).
2. For each stale gate, atomically ``UPDATE`` to ``state='rejected'``
   with the sentinel ``notes`` string ``auto_rejected_after_<N>_hours``
   so an operator can later distinguish auto-expiry from real
   rejections. The UPDATE re-checks ``state='pending'`` inside the
   ``WHERE`` clause so a concurrent operator approval can't be silently
   overwritten — ``UPDATE ... RETURNING`` returns NULL on race and we
   skip the history write.
3. Write a ``pipeline_gate_history`` row (``event_kind='auto_expired'``)
   for every gate we successfully transitioned. The post_id ↔ task_id
   exclusivity check on that table means we attach the post_id and
   leave task_id NULL.
4. Write a single ``audit_log`` row (``event_type='gate_auto_expired'``)
   with the batch summary so the canonical history shows the sweep.
5. If the batch size meets the configurable notify threshold, send ONE
   coalesced Telegram message via ``notify_operator`` summarizing what
   was expired (count + oldest age + lookup hint). Per-gate Telegram
   noise would violate Matt's ``feedback_telegram_vs_discord.md`` rule.

Design parity with the rest of the brain:

- DB-configurable through ``app_settings`` (every tunable above is a
  row, not a constant — see migration
  ``20260506_132235_seed_gate_auto_expire_app_settings.py``).
- Standalone module: only stdlib + asyncpg. No ``SiteConfig`` import —
  brain reads settings via direct ``pool.fetchval`` calls (matches
  ``backup_watcher.py`` posture).
- Mirrors ``brain/backup_watcher.py`` lifecycle: a
  ``run_gate_auto_expire_probe(pool, *, ...)`` entry point with
  injectable ``now_fn``/``notify_fn`` seams for unit tests; a
  ``GateAutoExpireProbe`` Probe-Protocol wrapper for the registry.

Sentinel reason format: ``auto_rejected_after_<N>_hours`` where ``<N>``
is the configured ``gate_pending_max_age_hours``. Stored in the
``notes`` column on ``post_approval_gates`` so it shows up in the
default ``poindexter gates list`` output without an extra join.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.gate_auto_expire")


# ---------------------------------------------------------------------------
# App_settings keys — every tunable lives in the DB so an operator can
# adjust without redeploying the brain. Defaults below match the
# 20260506_132235 migration so the probe behaves consistently whether
# the seed migration ran or not (degraded-but-safe defaults).
# ---------------------------------------------------------------------------

ENABLED_KEY = "gate_auto_expire_enabled"
MAX_AGE_HOURS_KEY = "gate_pending_max_age_hours"
POLL_INTERVAL_MINUTES_KEY = "gate_auto_expire_poll_interval_minutes"
BATCH_SIZE_KEY = "gate_auto_expire_batch_size"
NOTIFY_THRESHOLD_KEY = "gate_auto_expire_notify_threshold"

DEFAULT_ENABLED = True
DEFAULT_MAX_AGE_HOURS = 168  # 7 days, per #338
DEFAULT_POLL_INTERVAL_MINUTES = 30
DEFAULT_BATCH_SIZE = 50
DEFAULT_NOTIFY_THRESHOLD = 1

# How often the registry-driven path should call the probe. Brain
# default cycle is 5 minutes; this probe is idempotent so it's safe to
# run every cycle, but the registry honors interval_seconds to keep
# DB pressure low. 30 minutes matches the default poll-interval setting.
PROBE_INTERVAL_SECONDS = 30 * 60

# Sentinel approver string written to ``post_approval_gates.approver``.
# Distinct from any human approver — the CLI/web admin never sends
# this string, so a simple ``WHERE approver=...`` on the
# pipeline_gate_history side picks out auto-expiry rows.
SENTINEL_APPROVER = "system:gate_auto_expire"


# ---------------------------------------------------------------------------
# app_settings reads — brain is standalone so we hit the DB directly.
# Each helper degrades to its default when the row is missing or the
# fetch raises, mirroring the pattern in brain/backup_watcher.py.
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GATE_EXPIRE] Could not read %s from app_settings: %s "
            "— using default %r",
            key, exc, default,
        )
        return default
    if val is None:
        return default
    return val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


async def _read_config(pool: Any) -> dict[str, Any]:
    """Pull every probe tunable in one helper.

    Returns a dict with all five settings resolved + coerced.
    """
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"),
        DEFAULT_ENABLED,
    )
    max_age_hours = _coerce_int(
        await _read_setting(pool, MAX_AGE_HOURS_KEY, DEFAULT_MAX_AGE_HOURS),
        DEFAULT_MAX_AGE_HOURS,
    )
    poll_interval_minutes = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES),
        DEFAULT_POLL_INTERVAL_MINUTES,
    )
    batch_size = _coerce_int(
        await _read_setting(pool, BATCH_SIZE_KEY, DEFAULT_BATCH_SIZE),
        DEFAULT_BATCH_SIZE,
    )
    notify_threshold = _coerce_int(
        await _read_setting(pool, NOTIFY_THRESHOLD_KEY, DEFAULT_NOTIFY_THRESHOLD),
        DEFAULT_NOTIFY_THRESHOLD,
    )

    return {
        "enabled": enabled,
        "max_age_hours": max_age_hours,
        "poll_interval_minutes": poll_interval_minutes,
        "batch_size": batch_size,
        "notify_threshold": notify_threshold,
    }


# ---------------------------------------------------------------------------
# History + audit writers — both share the audit_log degrade-to-debug
# pattern from backup_watcher because audit_log can lag behind on a
# fresh install. pipeline_gate_history is post-2026-04 so it's always
# present, but we still wrap it defensively.
# ---------------------------------------------------------------------------


async def _write_history_row(
    pool: Any,
    *,
    post_id: str,
    gate_name: str,
    sentinel_reason: str,
    age_seconds: float,
) -> bool:
    """INSERT one ``pipeline_gate_history`` row for an auto-expired gate.

    Returns True on success, False on failure (already logged). post_id
    is required + task_id is left NULL — the table's ONE-OF check
    constraint enforces exactly one of the two, and gates are keyed
    by post_id once the post row exists (which it must, since the
    gate row references it via FK).
    """
    metadata = {
        "sentinel_reason": sentinel_reason,
        "age_seconds": int(age_seconds),
        "expired_by": SENTINEL_APPROVER,
    }
    try:
        await pool.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, post_id, gate_name, event_kind, feedback, metadata)
            VALUES (NULL, $1, $2, 'auto_expired', $3, $4::jsonb)
            """,
            str(post_id),
            gate_name,
            sentinel_reason,
            json.dumps(metadata),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GATE_EXPIRE] Failed to write pipeline_gate_history for "
            "post %s gate %s: %s",
            post_id, gate_name, exc,
        )
        return False


async def _emit_audit_event(
    pool: Any,
    *,
    expired_count: int,
    max_age_hours: int,
    oldest_age_seconds: Optional[float],
    sentinel_reason: str,
    expired_post_ids: list[str],
) -> None:
    """Single audit_log row per cycle summarizing the sweep."""
    payload: dict[str, Any] = {
        "expired_count": expired_count,
        "max_age_hours": max_age_hours,
        "sentinel_reason": sentinel_reason,
        "expired_post_ids": expired_post_ids[:50],  # cap payload size
    }
    if oldest_age_seconds is not None:
        payload["oldest_age_seconds"] = int(oldest_age_seconds)
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            "gate_auto_expired",
            "brain.gate_auto_expire",
            json.dumps(payload),
            "warning" if expired_count > 0 else "info",
        )
    except Exception as exc:  # noqa: BLE001
        # audit_log table may not exist on a very fresh install — log
        # and carry on so the probe still does its job.
        logger.debug(
            "[GATE_EXPIRE] Could not write audit event: %s", exc,
        )


# ---------------------------------------------------------------------------
# Stale-gate sweep — the heart of the probe.
# ---------------------------------------------------------------------------


async def _select_stale_gates(
    pool: Any,
    *,
    max_age_hours: int,
    batch_size: int,
    now_utc: datetime,
) -> list[dict[str, Any]]:
    """Return up to ``batch_size`` pending gates older than the threshold.

    Uses a parameterised interval so the threshold stays operator-tunable
    without inlining the value into SQL. Joins ``posts`` so we can
    surface a friendly title in the coalesced notification — LEFT JOIN
    because a stale gate against a deleted post is still worth
    expiring (the FK is ON DELETE CASCADE so this should never happen,
    but defensive joins beat surprise NULLs).
    """
    rows = await pool.fetch(
        """
        SELECT g.id, g.post_id, g.gate_name, g.created_at,
               COALESCE(p.title, '(post deleted)') AS post_title
          FROM post_approval_gates g
          LEFT JOIN posts p ON p.id = g.post_id
         WHERE g.state = 'pending'
           AND g.created_at < $1::timestamptz - ($2 || ' hours')::interval
         ORDER BY g.created_at ASC
         LIMIT $3
        """,
        now_utc,
        str(max_age_hours),
        int(batch_size),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # Stringify uuids for the public surface — same convention the
        # post_approval_gates service uses.
        for key in ("id", "post_id"):
            v = d.get(key)
            if v is not None and not isinstance(v, str):
                d[key] = str(v)
        out.append(d)
    return out


async def _expire_one_gate(
    pool: Any,
    *,
    gate_id: str,
    sentinel_reason: str,
    now_utc: datetime,
) -> bool:
    """Atomically transition one pending gate to ``rejected``.

    The ``WHERE state='pending'`` clause inside the UPDATE is the race
    guard — if a concurrent operator approval (or another probe instance)
    has already moved the row, the UPDATE affects zero rows and we
    return False so the caller skips the history write.

    Note: we deliberately do NOT update ``posts.status='rejected'`` the
    way ``services.gates.post_approval_gates.reject_gate`` does. Auto-
    expiry is a queue-cleanup signal, not a content judgment — flipping
    the post status would be a more aggressive policy than what #338
    asked for. The sentinel reason in ``notes`` plus the
    ``pipeline_gate_history`` event_kind=auto_expired row let an
    operator distinguish auto-expiry from a real rejection and
    optionally re-open via the existing ``poindexter post reopen``
    flow.
    """
    try:
        result = await pool.fetchrow(
            """
            UPDATE post_approval_gates
               SET state = 'rejected',
                   decided_at = $1::timestamptz,
                   approver = $2,
                   notes = COALESCE(notes, '') ||
                           CASE WHEN COALESCE(notes, '') = ''
                                THEN $3
                                ELSE E'\\n' || $3
                            END
             WHERE id = $4::uuid
               AND state = 'pending'
            RETURNING id
            """,
            now_utc,
            SENTINEL_APPROVER,
            sentinel_reason,
            gate_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GATE_EXPIRE] UPDATE failed for gate %s: %s", gate_id, exc,
        )
        return False
    return result is not None


# ---------------------------------------------------------------------------
# Top-level probe entry point — called once per brain cycle.
# ---------------------------------------------------------------------------


async def run_gate_auto_expire_probe(
    pool: Any,
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    notify_fn: Optional[Callable[..., None]] = None,
) -> dict[str, Any]:
    """Single execution of the gate auto-expire probe.

    Args:
        pool: asyncpg pool for app_settings + post_approval_gates +
            pipeline_gate_history + audit_log.
        now_fn: ``() -> aware datetime`` — defaults to ``datetime.now(UTC)``.
            Tests inject a fixed clock so the SELECT interval is stable.
        notify_fn: operator notifier callable. Defaults to
            :func:`brain.operator_notifier.notify_operator`. Used to
            send the single coalesced Telegram message when the batch
            size meets ``gate_auto_expire_notify_threshold``.

    Returns a structured summary suitable for inclusion in
    ``brain_decisions`` / the cycle's ``probe_results`` map.
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    notify_fn = notify_fn or notify_operator

    config = await _read_config(pool)
    if not config["enabled"]:
        return {
            "ok": True,
            "status": "disabled",
            "expired": 0,
            "detail": (
                f"Gate auto-expire disabled "
                f"(app_settings.{ENABLED_KEY}=false)"
            ),
        }

    now_utc = now_fn()
    max_age_hours = int(config["max_age_hours"])
    batch_size = int(config["batch_size"])
    notify_threshold = int(config["notify_threshold"])
    sentinel_reason = f"auto_rejected_after_{max_age_hours}_hours"

    try:
        stale = await _select_stale_gates(
            pool,
            max_age_hours=max_age_hours,
            batch_size=batch_size,
            now_utc=now_utc,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GATE_EXPIRE] SELECT for stale gates failed: %s", exc,
            exc_info=True,
        )
        return {
            "ok": False,
            "status": "select_failed",
            "expired": 0,
            "detail": f"SELECT failed: {type(exc).__name__}: {str(exc)[:160]}",
        }

    if not stale:
        # Quiet path — no stale gates to expire. INFO level would spam
        # the brain log every 30m, so this stays at DEBUG.
        logger.debug("[GATE_EXPIRE] expired 0 stale gates")
        return {
            "ok": True,
            "status": "noop",
            "expired": 0,
            "detail": "No pending gates older than threshold.",
            "max_age_hours": max_age_hours,
        }

    # Walk the batch row-by-row so a single race-loss doesn't take the
    # whole cycle down. Each successful UPDATE writes one
    # pipeline_gate_history row.
    expired: list[dict[str, Any]] = []
    skipped_races: list[str] = []
    oldest_age_seconds: Optional[float] = None
    oldest_post_title: str = ""

    for gate in stale:
        ok = await _expire_one_gate(
            pool,
            gate_id=gate["id"],
            sentinel_reason=sentinel_reason,
            now_utc=now_utc,
        )
        if not ok:
            # Race-loss (or DB error already logged inside the helper).
            # Skip the history write so we don't claim auto-expiry for a
            # gate the operator just approved.
            skipped_races.append(gate["id"])
            continue

        created_at = gate.get("created_at")
        age_seconds = 0.0
        if isinstance(created_at, datetime):
            try:
                age_seconds = max(
                    0.0, (now_utc - created_at).total_seconds()
                )
            except Exception:  # noqa: BLE001
                age_seconds = 0.0

        await _write_history_row(
            pool,
            post_id=gate["post_id"],
            gate_name=gate["gate_name"],
            sentinel_reason=sentinel_reason,
            age_seconds=age_seconds,
        )

        if oldest_age_seconds is None or age_seconds > oldest_age_seconds:
            oldest_age_seconds = age_seconds
            oldest_post_title = str(
                gate.get("post_title") or "(unknown)"
            )[:120]

        expired.append(
            {
                "gate_id": gate["id"],
                "post_id": gate["post_id"],
                "gate_name": gate["gate_name"],
                "age_seconds": age_seconds,
                "post_title": gate.get("post_title"),
            }
        )

    expired_count = len(expired)
    expired_post_ids = [e["post_id"] for e in expired]

    # One audit_log row per cycle summarising what happened.
    await _emit_audit_event(
        pool,
        expired_count=expired_count,
        max_age_hours=max_age_hours,
        oldest_age_seconds=oldest_age_seconds,
        sentinel_reason=sentinel_reason,
        expired_post_ids=expired_post_ids,
    )

    # Per-cycle log: INFO when N>0, otherwise the DEBUG line above.
    if expired_count > 0:
        logger.info("[GATE_EXPIRE] expired %d stale gates", expired_count)

    # ONE coalesced Telegram notify when the batch meets threshold. Do
    # NOT ping per-gate — that would violate the
    # feedback_telegram_vs_discord.md "Telegram = critical alerts only"
    # rule. notify_threshold=0 disables notifications entirely.
    notified = False
    if (
        notify_threshold > 0
        and expired_count >= notify_threshold
    ):
        oldest_days = (
            (oldest_age_seconds or 0) / 86400.0 if oldest_age_seconds else 0
        )
        title = (
            f"Auto-expired {expired_count} stale pending gate"
            f"{'s' if expired_count != 1 else ''}"
        )
        detail_lines = [
            f"Auto-expired {expired_count} pending gate"
            f"{'s' if expired_count != 1 else ''} older than "
            f"{max_age_hours}h ({sentinel_reason}).",
        ]
        if oldest_age_seconds is not None:
            detail_lines.append(
                f"Oldest was for post '{oldest_post_title}' "
                f"({oldest_days:.1f} days old)."
            )
        if skipped_races:
            detail_lines.append(
                f"{len(skipped_races)} gate(s) skipped due to concurrent "
                f"operator activity."
            )
        detail_lines.append(
            "See `poindexter gates list --recent-rejected` for details."
        )
        try:
            notify_fn(
                title=title,
                detail="\n".join(detail_lines),
                source="brain.gate_auto_expire",
                severity="warning",
            )
            notified = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GATE_EXPIRE] notify_fn failed: %s", exc)

    return {
        "ok": True,
        "status": "expired" if expired_count > 0 else "noop",
        "expired": expired_count,
        "skipped_races": len(skipped_races),
        "max_age_hours": max_age_hours,
        "oldest_age_seconds": oldest_age_seconds,
        "sentinel_reason": sentinel_reason,
        "notified": notified,
        "detail": (
            f"Auto-expired {expired_count} pending gate(s); "
            f"threshold={max_age_hours}h."
        ),
    }


# ---------------------------------------------------------------------------
# Probe-Protocol adapter — for the registry-driven path. Mirrors
# BackupWatcherProbe so this slots into the same registry.
# ---------------------------------------------------------------------------


class GateAutoExpireProbe:
    """Probe-Protocol-compatible wrapper around
    :func:`run_gate_auto_expire_probe`.
    """

    name: str = "gate_auto_expire"
    description: str = (
        "Auto-rejects pending HITL approval gates older than "
        "gate_pending_max_age_hours (default 7d) and emits ONE coalesced "
        "operator notification per cycle. Closes the 'review queue grows "
        "monotonically' failure mode in #338."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_gate_auto_expire_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                "status": summary.get("status"),
                "expired": summary.get("expired", 0),
                "max_age_hours": summary.get("max_age_hours"),
                "notified": summary.get("notified", False),
            },
            severity="warning" if summary.get("expired", 0) > 0 else "info",
        )
