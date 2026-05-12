"""Discord bot reachability probe — symmetric to the Telegram getMe check.

Closes Glad-Labs/poindexter#435.

Why: ``scripts/claude-code-watchdog.ps1`` already pings Telegram's
``getMe`` from the host. Discord had no equivalent: when the bot's
gateway connection dropped or the token rotated/expired silently,
nothing in brain caught it — alert_events to Discord just stopped
firing and the operator only noticed days later.

This probe encodes a one-shot HTTP health check:

1. Read ``discord_bot_token`` (encrypted) via ``brain.secret_reader``.
2. ``GET https://discord.com/api/v10/users/@me`` with
   ``Authorization: Bot <token>`` and a short timeout (5s by default).
3. Map the response:
   * 200 → ok, healthy.
   * 401/403 → critical: token revoked or wrong. Page the operator.
   * Network / 5xx / timeout → transient, returned ok=False at
     severity=info. We DO NOT page on transient errors because Discord
     itself has occasional 5xxs and we'd just train the operator to
     ignore the channel.
4. Internal cadence gate: only the operator-tunable
   ``discord_bot_probe_interval_minutes`` (default 5) gets the actual
   round-trip; the probe is dispatched every brain cycle but skips
   between intervals.
5. On a real failure (401/403 only), write a coalesced ``alert_events``
   row with a 1-hour dedup fingerprint so the operator hears once per
   hour rather than every 5 minutes while the token is broken.

Design parity with brain/pr_staleness_probe.py + brain/glitchtip_triage_probe.py:

* DB-configurable through ``app_settings`` — every tunable is a row.
* Standalone module: only stdlib + asyncpg + httpx.
* Module-level state for cadence + dedup, with ``_reset_state()`` test
  hook so unit tests get deterministic behaviour.
* Fails LOUD per feedback_no_silent_defaults: when token isn't set
  but the probe IS enabled, emit a single warning + return ok=False
  with ``status='unconfigured'`` in the summary.
* Probe dispatch is exception-isolated by the caller (brain_daemon).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # Flat import when brain/ is on sys.path (container runtime).
    from secret_reader import read_app_setting as _read_app_setting
except ImportError:  # pragma: no cover — package-qualified path
    from brain.secret_reader import read_app_setting as _read_app_setting

logger = logging.getLogger("brain.discord_bot_probe")


# ---------------------------------------------------------------------------
# App_settings keys — every tunable lives in the DB.
# See migration that seeds the defaults.
# ---------------------------------------------------------------------------

ENABLED_KEY = "discord_bot_probe_enabled"
POLL_INTERVAL_MINUTES_KEY = "discord_bot_probe_interval_minutes"
HTTP_TIMEOUT_SECONDS_KEY = "discord_bot_probe_timeout_seconds"
DEDUP_HOURS_KEY = "discord_bot_probe_dedup_hours"
TOKEN_KEY = "discord_bot_token"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 5
DEFAULT_HTTP_TIMEOUT_SECONDS = 5
DEFAULT_DEDUP_HOURS = 1

DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"


# ---------------------------------------------------------------------------
# Module-level state — cadence gate + dedup. Reset by ``_reset_state()``
# in tests so each test gets a deterministic starting point.
# ---------------------------------------------------------------------------

_last_real_check_at: float = 0.0
_last_alert_at: float = 0.0


def _reset_state() -> None:
    """Test hook — drop module-level cadence + dedup state."""
    global _last_real_check_at, _last_alert_at
    _last_real_check_at = 0.0
    _last_alert_at = 0.0


# ---------------------------------------------------------------------------
# Tunable readers — best-effort, fall through to defaults.
# ---------------------------------------------------------------------------


async def _read_bool(pool, key: str, default: bool) -> bool:
    raw = await _read_app_setting(pool, key, "")
    if not raw:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


async def _read_int(pool, key: str, default: int) -> int:
    raw = await _read_app_setting(pool, key, "")
    if not raw:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning(
            "[DISCORD_BOT_PROBE] %s is not an integer (%r); falling back to %d",
            key, raw, default,
        )
        return default


# ---------------------------------------------------------------------------
# alert_events row writer — Discord-routed coalesced page.
# ---------------------------------------------------------------------------


async def _write_alert(
    pool,
    *,
    status_code: int,
    detail: str,
) -> None:
    """Write one alert_events row when the token is rejected.

    Matches the schema other probes use (alert_dispatcher routes by
    ``severity`` + ``channel_hint``). The dedup fingerprint lives in
    the column so two consecutive 401s within the dedup window collapse
    into a single visible alert.
    """
    fingerprint = f"discord_bot_probe:{status_code}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                event_type, severity, channel_hint, fingerprint,
                title, body, details
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            "discord_bot_unreachable",
            "warning",
            "discord",
            fingerprint,
            f"Discord bot health check failed (HTTP {status_code})",
            detail,
            '{"probe": "discord_bot_probe", "status_code": '
            f'{status_code}, "url": "{DISCORD_ME_URL}"}}',
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("[DISCORD_BOT_PROBE] alert_events write failed: %s", exc)


# ---------------------------------------------------------------------------
# Probe entry point — called once per brain cycle from brain_daemon.
# ---------------------------------------------------------------------------


async def run_discord_bot_probe(
    pool,
    *,
    now_fn: Optional[Callable[[], float]] = None,
    http_client_factory: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Run one cycle of the probe. Returns a summary dict.

    Args:
        pool: asyncpg pool for app_settings + alert_events.
        now_fn: Injectable clock seam (defaults to ``time.monotonic``).
        http_client_factory: Injectable httpx.AsyncClient factory (for tests).

    Returns:
        ``{"ok": bool, "status": str, "detail": str, ...}`` — the standard
        probe summary shape brain_daemon stores in ``probe_results``.
    """
    global _last_real_check_at, _last_alert_at
    clock = now_fn or time.monotonic

    enabled = await _read_bool(pool, ENABLED_KEY, DEFAULT_ENABLED)
    if not enabled:
        return {"ok": True, "status": "disabled", "detail": "discord_bot_probe disabled"}

    interval_min = await _read_int(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES)
    interval_s = max(1, interval_min) * 60
    now = clock()
    if _last_real_check_at and (now - _last_real_check_at) < interval_s:
        return {
            "ok": True,
            "status": "skipped_cadence",
            "detail": f"next check in {int(interval_s - (now - _last_real_check_at))}s",
        }

    token = await _read_app_setting(pool, TOKEN_KEY, "")
    if not token:
        # Configured but no token = explicit operator misconfig. Don't page —
        # the operator is presumably about to fill the value in. Just log.
        logger.warning(
            "[DISCORD_BOT_PROBE] %s is empty — probe cannot run", TOKEN_KEY,
        )
        _last_real_check_at = now
        return {
            "ok": False,
            "status": "unconfigured",
            "detail": f"{TOKEN_KEY} is empty",
        }

    timeout_s = await _read_int(pool, HTTP_TIMEOUT_SECONDS_KEY, DEFAULT_HTTP_TIMEOUT_SECONDS)

    if httpx is None and http_client_factory is None:
        logger.warning("[DISCORD_BOT_PROBE] httpx not installed — skipping")
        _last_real_check_at = now
        return {"ok": False, "status": "no_httpx", "detail": "httpx not available"}

    _last_real_check_at = now

    # The "httpx is None" branch above already returned, so this lambda
    # can never fire when the module is missing. Pyright doesn't carry
    # the narrowing into the closure, so cast through Any.
    _httpx: Any = httpx
    factory = http_client_factory or (lambda: _httpx.AsyncClient(timeout=timeout_s))
    try:
        async with factory() as client:
            response = await client.get(
                DISCORD_ME_URL,
                headers={"Authorization": f"Bot {token}"},
            )
            status_code = response.status_code
    except Exception as exc:  # noqa: BLE001 — transient network blip
        logger.info(
            "[DISCORD_BOT_PROBE] transient error contacting Discord: %s: %s",
            type(exc).__name__, exc,
        )
        return {
            "ok": False,
            "status": "transient",
            "detail": f"{type(exc).__name__}: {exc}",
        }

    if status_code == 200:
        logger.info("[DISCORD_BOT_PROBE] Discord bot reachable (HTTP 200)")
        return {"ok": True, "status": "ok", "detail": "Discord bot reachable", "status_code": 200}

    if status_code in (401, 403):
        # Token revoked / expired / wrong-perms — page the operator
        # (Discord-routed) but dedup within the configured window so we
        # only hear once per hour while the token is broken.
        dedup_hours = await _read_int(pool, DEDUP_HOURS_KEY, DEFAULT_DEDUP_HOURS)
        dedup_s = max(1, dedup_hours) * 3600
        if _last_alert_at and (now - _last_alert_at) < dedup_s:
            logger.warning(
                "[DISCORD_BOT_PROBE] HTTP %s (dedup window suppresses page)",
                status_code,
            )
        else:
            await _write_alert(
                pool,
                status_code=status_code,
                detail=(
                    f"Discord users/@me returned HTTP {status_code}. The bot "
                    f"token is rejected — rotate {TOKEN_KEY} via "
                    "`poindexter setting set` or check the Discord developer "
                    "portal."
                ),
            )
            _last_alert_at = now
        return {
            "ok": False,
            "status": "auth_failed",
            "detail": f"HTTP {status_code}",
            "status_code": status_code,
        }

    # 5xx or unexpected status — treat as transient.
    logger.info(
        "[DISCORD_BOT_PROBE] Discord returned HTTP %s (non-fatal)",
        status_code,
    )
    return {
        "ok": False,
        "status": "transient",
        "detail": f"HTTP {status_code}",
        "status_code": status_code,
    }
