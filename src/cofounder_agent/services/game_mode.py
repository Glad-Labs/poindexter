"""Game mode — park GPU services so the operator can use the machine.

Matt's PC is simultaneously the gaming rig and the AI inference server, and
the self-healing machinery is *designed* to keep the inference side up. A
hand-run ``docker stop`` therefore does not hold: ``brain/compose_drift_probe``
sees the service missing and runs ``docker compose up -d`` on the next cycle.
Game mode is the sanctioned way to say "these are down on purpose".

Design
------

**Intent lives in the DB, enforcement is distributed.** ``enable()`` writes a
single expiry timestamp to ``app_settings.game_mode_until``; every consumer
reads that one seam:

- :func:`is_active` — sync, cache-backed. ``gpu_scheduler.lock()`` calls it on
  every acquire and blocks pipeline GPU work while the operator is playing.
- ``brain/compose_drift_probe`` — folds :func:`parked_services` into its
  on-demand set, so it stops re-launching the parked containers *and* takes
  them down if they are up.
- The CLI adapter additionally fires a best-effort ``docker stop`` for instant
  effect, because the operator is usually at the keyboard and a 5-minute wait
  for the next brain cycle is a bad experience. That is an optimisation, not
  the contract — triggering from MCP (phone) with no docker socket still works,
  it just takes until the next brain cycle to land.

**TTL, not a toggle.** ``game_mode_until`` is an absolute UTC timestamp and
:func:`is_active` compares against ``now()``. A forgotten game mode therefore
expires on its own instead of silently starving the content pipeline for days
— the failure mode of a plain boolean. Nothing has to run to "clean up": an
elapsed timestamp simply reads as inactive.

**Why not reuse ``gpu_external_workload_wait_enabled``?** That flag drives the
*utilization-inference* path in ``gpu_scheduler._wait_for_gaming_clear``, which
is off by default because it mislabels the stack's own GPU bursts as gaming and
produced a 407s phantom pipeline stall (validation finding 4a). Game mode is an
*explicit* operator signal, so it needs no heuristic and re-introduces no false
positives. The two are independent on purpose: enabling game mode does not
touch that flag.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .site_config import SiteConfig

logger = logging.getLogger(__name__)

# app_settings keys. Per feedback_app_settings_value_not_null, '' means unset
# rather than NULL — an empty UNTIL_KEY is the canonical "game mode is off".
UNTIL_KEY = "game_mode_until"
PARKED_SERVICES_KEY = "game_mode_parked_services"
DEFAULT_HOURS_KEY = "game_mode_default_hours"
EVICT_OLLAMA_KEY = "game_mode_evict_ollama"
CONTAINER_PREFIX_KEY = "game_mode_container_prefix"

# Compose *service* names (the vocabulary compose_drift_probe speaks). Docker
# container names are derived by prefixing CONTAINER_PREFIX_KEY.
PARKED_SERVICES_DEFAULT = (
    "speaches,chatterbox,stable-audio,image-gen-server,wan-server"
)
DEFAULT_HOURS_DEFAULT = "4"
CONTAINER_PREFIX_DEFAULT = "poindexter-"

# Ceiling on a single enable() so a fat-fingered "--hours 400" cannot park the
# business for a fortnight. Deliberately generous: a long weekend is plausible.
MAX_HOURS = 72.0


@dataclass(frozen=True)
class GameModeStatus:
    """Resolved game-mode state. ``until`` is None whenever inactive."""

    active: bool
    until: datetime | None
    parked_services: tuple[str, ...] = ()
    seconds_remaining: int = 0
    # Populated by adapters that can reach docker; empty elsewhere.
    stopped: tuple[str, ...] = field(default_factory=tuple)
    failed: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "until": self.until.isoformat() if self.until else None,
            "parked_services": list(self.parked_services),
            "seconds_remaining": self.seconds_remaining,
            "stopped": list(self.stopped),
            "failed": list(self.failed),
        }


def _parse_until(raw: object) -> datetime | None:
    """Parse the stored timestamp. Unparseable reads as OFF, loudly.

    Failing *open* (treating garbage as "game mode on") would starve the
    pipeline indefinitely with no way for the operator to notice, so a corrupt
    value degrades to off and pages through the log instead.

    Non-``str`` input returns None *quietly*: ``SiteConfig.get`` is typed to
    return ``str``, so a non-string is a type-contract violation (a test
    double, a mocked config) rather than operator-visible data corruption —
    and this runs on the GPU-acquire hot path, where an ERROR per call would
    bury the log. A malformed *string* is real corruption and still shouts.
    """
    if not isinstance(raw, str):
        return None
    if not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        logger.error(
            "app_settings.%s is not ISO-8601 (%r) — treating game mode as OFF",
            UNTIL_KEY,
            raw,
        )
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def parked_services(site_config: SiteConfig) -> tuple[str, ...]:
    """Compose service names game mode parks."""
    return _split_csv(
        site_config.get(PARKED_SERVICES_KEY, PARKED_SERVICES_DEFAULT)
    )


def container_names(site_config: SiteConfig) -> tuple[str, ...]:
    """Docker container names for the parked compose services."""
    prefix = site_config.get(CONTAINER_PREFIX_KEY, CONTAINER_PREFIX_DEFAULT)
    return tuple(f"{prefix}{svc}" for svc in parked_services(site_config))


def is_active(site_config: SiteConfig, *, now: datetime | None = None) -> bool:
    """True while game mode is on. Sync + cache-backed — safe in hot paths.

    ``gpu_scheduler.lock()`` calls this on every acquire, so it must not do
    I/O. ``SiteConfig.get`` reads the in-memory cache that the reload job
    refreshes every minute; a phone-triggered enable therefore takes effect
    within that refresh window rather than instantly, which is fine for a
    mode measured in hours.
    """
    until = _parse_until(site_config.get(UNTIL_KEY, ""))
    if until is None:
        return False
    return (now or datetime.now(UTC)) < until


def status_from_config(
    site_config: SiteConfig, *, now: datetime | None = None
) -> GameModeStatus:
    """Resolve status without touching the DB (cache read only)."""
    moment = now or datetime.now(UTC)
    until = _parse_until(site_config.get(UNTIL_KEY, ""))
    active = until is not None and moment < until
    return GameModeStatus(
        active=active,
        until=until if active else None,
        parked_services=parked_services(site_config) if active else (),
        seconds_remaining=(
            int((until - moment).total_seconds()) if active and until else 0
        ),
    )


async def _write_until(pool: Any, value: str) -> None:
    """Persist the expiry. Upsert so a fresh install needs no seeded row."""
    await pool.execute(
        """
        INSERT INTO app_settings (key, value, description)
        VALUES ($1, $2, $3)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        UNTIL_KEY,
        value,
        "Game mode expiry (ISO-8601 UTC). Empty = off. See services/game_mode.py",
    )


async def enable(
    pool: Any,
    site_config: SiteConfig,
    *,
    hours: float | None = None,
    now: datetime | None = None,
) -> GameModeStatus:
    """Turn game mode on for ``hours`` (default from app_settings).

    Raises ``ValueError`` on a non-positive or over-ceiling duration rather
    than silently clamping — per feedback_no_silent_defaults, a caller that
    asked for something impossible should hear about it.
    """
    if hours is None:
        raw = site_config.get(DEFAULT_HOURS_KEY, DEFAULT_HOURS_DEFAULT)
        try:
            hours = float(raw)
        except (TypeError, ValueError):
            logger.error(
                "app_settings.%s is not a number (%r) — falling back to %s",
                DEFAULT_HOURS_KEY,
                raw,
                DEFAULT_HOURS_DEFAULT,
            )
            hours = float(DEFAULT_HOURS_DEFAULT)

    if hours <= 0:
        raise ValueError(f"game mode duration must be positive, got {hours}")
    if hours > MAX_HOURS:
        raise ValueError(
            f"game mode duration {hours}h exceeds the {MAX_HOURS}h ceiling; "
            "re-run closer to the time or raise game_mode_default_hours"
        )

    moment = now or datetime.now(UTC)
    until = moment + timedelta(hours=hours)
    await _write_until(pool, until.isoformat())
    # NOTE: the caller's SiteConfig cache still holds the pre-write value until
    # the reload job next runs. That is why this returns a status built from
    # the values just written rather than re-reading through is_active() —
    # adapters must print/act on the returned object, not re-query the cache.
    services = parked_services(site_config)
    logger.info(
        "game mode ON until %s (%.2fh) — parking %s",
        until.isoformat(),
        hours,
        ", ".join(services) or "(nothing configured)",
    )
    return GameModeStatus(
        active=True,
        until=until,
        parked_services=services,
        seconds_remaining=int((until - moment).total_seconds()),
    )


async def disable(pool: Any, site_config: SiteConfig) -> GameModeStatus:
    """Turn game mode off. Idempotent — disabling when off is a no-op.

    Parked services are NOT started here on purpose: ``compose_drift_probe``
    already owns "a service that should be up is down" and will restore them
    on its next cycle. Reusing the self-healer as the restore path means there
    is exactly one piece of code that starts containers.
    """
    await _write_until(pool, "")
    logger.info("game mode OFF — compose drift probe will restore parked services")
    return GameModeStatus(active=False, until=None)


async def status(
    pool: Any, site_config: SiteConfig | None = None
) -> GameModeStatus:
    """Authoritative status — reads the DB rather than the settings cache.

    The cache lags by up to the reload interval, which matters both for "did
    my phone toggle land?" and for the Prefect flow's claim guard (a flow
    subprocess may hold a cache older than the operator's toggle). Hot paths
    that must not do I/O use :func:`is_active` / :func:`status_from_config`.

    ``site_config`` is optional: it is only needed to resolve the parked
    service list for display. Callers that just want the active/expiry answer
    (the claim guard) may omit it.
    """
    raw = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", UNTIL_KEY)
    until = _parse_until(raw)
    moment = datetime.now(UTC)
    active = until is not None and moment < until
    return GameModeStatus(
        active=active,
        until=until if active else None,
        parked_services=(
            parked_services(site_config) if active and site_config is not None else ()
        ),
        seconds_remaining=(
            int((until - moment).total_seconds()) if active and until else 0
        ),
    )
