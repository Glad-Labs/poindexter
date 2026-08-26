"""System clock — the operator timezone the whole system schedules and renders in.

Store-UTC / present-local: the DB, logs, metrics, and traces stay UTC. This
module is the ONLY place that converts to the operator's zone, for (a) the
scheduler's wall-clock cron interpretation and (b) operator-facing timestamp
formatting. The zone is a DB-backed tunable (`app_settings.operator_timezone`,
an IANA name); the OSS default is UTC and the operator overlay sets the real
zone.

Reachable from both runtimes: the DI worker via `SiteConfig.timezone`, and the
bare-pool brain / jobs / scheduler via `get_operator_tz(pool)`. Both funnel
through `resolve_operator_tz`, so there is exactly one validation path.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

DEFAULT_TZ = "UTC"

# Bound at module scope (default None) so tests can monkeypatch it; the real
# import is lazy inside _warn_invalid_tz to avoid an import cycle at boot.
emit_finding: Any = None


def resolve_operator_tz(name: str | None) -> ZoneInfo:
    """Parse an IANA timezone name into a ZoneInfo.

    Missing/empty -> UTC (documented base, no alert). Invalid name -> loud
    (log + deduped finding) but degrade to UTC; NEVER raises, so a timezone
    typo cannot crash the worker or the self-healing brain.
    """
    if not name or not name.strip():
        return ZoneInfo(DEFAULT_TZ)
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        _warn_invalid_tz(name.strip(), exc)
        return ZoneInfo(DEFAULT_TZ)


def _warn_invalid_tz(name: str, exc: Exception) -> None:
    logger.error("[clock] invalid operator_timezone %r (%s); falling back to UTC", name, exc)
    try:
        emit = emit_finding
        if emit is None:  # normal runtime path (tests monkeypatch the module attr)
            from utils.findings import emit_finding as _emit_finding

            emit = _emit_finding
        emit(
            source="services.clock",
            kind="invalid_timezone",
            title="operator_timezone is not a valid IANA name",
            body=f"operator_timezone={name!r} did not resolve ({describe_exception(exc)}); using UTC until fixed.",
            severity="warn",
            dedup_key=f"invalid-operator-timezone:{name}",
        )
    except Exception as e:  # noqa: BLE001 — resolve must never raise
        # A finding-emit failure means the operator alerting path itself broke
        # while reporting a config error — warn (visible in Loki), never raise
        # (resolve_operator_tz's contract). Above debug/info so it isn't a
        # silent swallow (scripts/ci/lint_silent_excepts.py).
        logger.warning("[clock] finding emit failed: %s", e)


async def get_operator_tz(pool: Any) -> ZoneInfo:
    """Bare-pool accessor: read `app_settings.operator_timezone` and resolve.

    For the independent brain (Python + asyncpg, no DI container), scheduled
    jobs, and the scheduler. `operator_timezone` is non-secret, so a direct
    read is correct (no decryption needed). Any failure degrades to UTC.
    """
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'operator_timezone'"
            )
    except Exception as e:  # noqa: BLE001 — degrade, never raise
        logger.debug("[clock] operator_timezone read failed: %s", e)
        value = None
    return resolve_operator_tz(value)


def now_local(tz: ZoneInfo) -> datetime:
    """Current instant as an aware datetime in the operator zone."""
    return datetime.now(tz)


def today_local(tz: ZoneInfo) -> date:
    """Current calendar day in the operator zone."""
    return datetime.now(tz).date()


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a stored datetime for display. Naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def format_local(dt: datetime, tz: ZoneInfo, fmt: str) -> str:
    """Render a stored datetime in the operator zone with `strftime`."""
    return to_local(dt, tz).strftime(fmt)
