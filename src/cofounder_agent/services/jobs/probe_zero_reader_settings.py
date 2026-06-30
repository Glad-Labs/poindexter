"""ProbeZeroReaderSettingsJob — surface app_settings keys nothing reads.

Third piece of the settings-lifecycle work (Glad-Labs/poindexter#756 item 3).
``SiteConfig.get`` stamps ``app_settings.last_read_at`` (via
``FlushSettingsReadTelemetryJob``) for every key the running system consults.
This probe runs the inverse query every 6h: keys whose ``last_read_at`` is
still NULL, that have existed longer than a grace window — orphan candidates
the operator can review and retire.

It emits ONE advisory ``settings_zero_reader_keys`` finding (severity ``warn``)
with a stable ``dedup_key`` so the alert dispatcher collapses repeated fires
into a single Discord page until the situation changes. The live list also
renders on the Integrations & Admin Grafana board.

ADVISORY, not authoritative. ``last_read_at`` is only stamped on the
``SiteConfig.get`` path, so a key read EXCLUSIVELY via a non-SiteConfig path
(direct SQL — e.g. ``findings_alert_router`` reading ``findings.*`` policies —
or ``SettingsService.get``) also shows up here. The finding body says as much;
verify each key before retiring it. (If this proves noisy, the next step is to
instrument ``SettingsService.get`` the same way.)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# app_settings keys (seeded in settings_defaults.py).
_ENABLED_KEY = "settings_zero_reader_probe_enabled"
_GRACE_DAYS_KEY = "settings_zero_reader_grace_days"
_MAX_REPORT_KEY = "settings_zero_reader_max_report"
_DEFAULT_GRACE_DAYS = 30
_DEFAULT_MAX_REPORT = 50

_FINDING_KIND = "settings_zero_reader_keys"

# Keys whose last_read_at stays NULL but only because the grace window hasn't
# elapsed are excluded; the orphan query joins on created_at so brand-new keys
# get a fair chance to be read before being flagged.
_ORPHAN_QUERY = """
    SELECT key, category, created_at
    FROM app_settings
    WHERE last_read_at IS NULL
      AND is_secret = false
      AND COALESCE(deprecated, false) = false
      AND created_at < NOW() - ($1 * INTERVAL '1 day')
    ORDER BY created_at ASC
    LIMIT $2
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


def _format_created(value: Any) -> str:
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        return "unknown"


def _build_body(rows: list[dict[str, Any]], grace_days: int) -> str:
    lines = [
        f"{len(rows)} app_settings key(s) have a NULL `last_read_at` and have "
        f"existed for more than {grace_days} day(s) — nothing in the running "
        f"system has read them via `SiteConfig.get` since read-telemetry began.",
        "",
        "**Advisory only.** A key also appears here if it is read EXCLUSIVELY "
        "via a non-SiteConfig path (direct SQL, `SettingsService.get`). Verify "
        "each is truly unused before retiring it.",
        "",
    ]
    for r in rows:
        lines.append(
            f"- `{r['key']}` (category: {r.get('category') or 'general'}, "
            f"created {_format_created(r.get('created_at'))})"
        )
    return "\n".join(lines)


class ProbeZeroReaderSettingsJob:
    """Emit a finding listing app_settings keys nothing reads (orphan candidates)."""

    name = "probe_zero_reader_settings"
    description = (
        "Surface app_settings keys with NULL last_read_at past the grace window "
        "(orphan candidates, poindexter#756)"
    )
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)

        grace_days = _cfg_int(site_config, _GRACE_DAYS_KEY, _DEFAULT_GRACE_DAYS)
        limit = _cfg_int(site_config, _MAX_REPORT_KEY, _DEFAULT_MAX_REPORT)

        try:
            async with pool.acquire() as conn:
                rows = [dict(r) for r in await conn.fetch(_ORPHAN_QUERY, grace_days, limit)]
        except Exception as e:  # noqa: BLE001 — a probe must never crash a cycle
            logger.warning("[probe_zero_reader_settings] query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {e}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="no zero-reader keys above the grace window",
                changes_made=0,
            )

        keys = [r["key"] for r in rows]
        emit_finding(
            source="zero_reader_settings_probe",
            kind=_FINDING_KIND,
            title=f"{len(keys)} app_settings key(s) never read",
            body=_build_body(rows, grace_days),
            severity="warn",
            dedup_key=_FINDING_KIND,
            extra={"count": len(keys), "keys": keys},
        )
        logger.info(
            "[probe_zero_reader_settings] emitted finding for %d zero-reader key(s)",
            len(keys),
        )
        return JobResult(
            ok=True,
            detail=f"emitted finding for {len(keys)} zero-reader key(s)",
            changes_made=0,
        )
