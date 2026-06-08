"""Migration 20260608_012805_seed_url_probe_data_fabric_overrides.

ISSUE: Glad-Labs/poindexter#228 (operator-page noise reduction)

The operator URL probe (brain/operator_url_probe.py) auto-probes every
``*_url`` app_setting and pages when it can't reach one. Two classes of
false positive were generating ~68 warning pages / 24h:

1. DataFabric observability surfaces (#429) — ``data_fabric_loki_url``
   (http://localhost:3100) and ``data_fabric_tempo_url`` (:3200). Loki and
   Tempo return 404 on their ROOT path, so a bare GET reads as
   "unreachable" even though both services are healthy. Their liveness
   endpoint is ``/ready`` (returns 200). We add per-target ``probe_url``
   overrides pointing the probe at ``/ready``. (Companion code change
   localizes the override ``probe_url`` so the localhost form resolves to
   host.docker.internal inside the brain container.)

2. Disabled podcast TTS — ``podcast_tts_base_url`` (http://speaches:8000/v1)
   targets the speaches sidecar, which isn't running until
   ``podcast_tts_enabled=true`` (#621, default off). Probing it pages ~17x/day
   with connection errors. We add it to ``operator_url_probe_skip_keys`` to
   mute it while the feature is off. (An existing ``/health`` override for the
   same key stays in place, dormant; when the operator enables podcast TTS and
   starts speaches, removing this key from the skip-list re-activates probing.)

Both writes are read-modify-write merges that PRESERVE any existing
operator-tuned entries — they only add the keys above when absent.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_OVERRIDES_KEY = "operator_url_probe_target_overrides"
_SKIP_KEY = "operator_url_probe_skip_keys"

# Health-endpoint overrides for the DataFabric Loki/Tempo surfaces. probe_url
# uses the localhost form; operator_url_probe._localize() rewrites it to
# host.docker.internal when the brain runs in a container (no-op on host).
_OVERRIDE_ADDITIONS: dict[str, dict[str, str]] = {
    "data_fabric_loki_url": {
        "probe_url": "http://localhost:3100/ready",
        "method": "GET",
        "reason": (
            "Loki root path 404s; /ready is its liveness endpoint (200). "
            "Seeded by 20260608_012805."
        ),
    },
    "data_fabric_tempo_url": {
        "probe_url": "http://localhost:3200/ready",
        "method": "GET",
        "reason": (
            "Tempo root path 404s; /ready is its liveness endpoint (200). "
            "Seeded by 20260608_012805."
        ),
    },
}

# Surfaces to mute while their backing feature is disabled.
_SKIP_ADDITIONS: tuple[str, ...] = ("podcast_tts_base_url",)


def _parse_overrides(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_skip_csv(raw: str | None) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


async def up(pool) -> None:
    """Merge the DataFabric health overrides + podcast skip-key (idempotent)."""
    async with pool.acquire() as conn:
        # --- 1) target overrides: merge, never clobber existing entries ------
        raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", _OVERRIDES_KEY
        )
        overrides = _parse_overrides(raw)
        added: list[str] = []
        for key, cfg in _OVERRIDE_ADDITIONS.items():
            if key not in overrides:
                overrides[key] = cfg
                added.append(key)
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, is_secret)
            VALUES ($1, $2, false)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            _OVERRIDES_KEY,
            json.dumps(overrides, sort_keys=True),
        )
        logger.info(
            "Migration url_probe_data_fabric_overrides: overrides added=%s "
            "(total=%d)",
            added or "none", len(overrides),
        )

        # --- 2) skip-keys CSV: append, dedupe, preserve order ----------------
        skip = _parse_skip_csv(
            await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", _SKIP_KEY
            )
        )
        skip_added: list[str] = []
        for key in _SKIP_ADDITIONS:
            if key not in skip:
                skip.append(key)
                skip_added.append(key)
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, is_secret)
            VALUES ($1, $2, false)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            _SKIP_KEY,
            ",".join(skip),
        )
        logger.info(
            "Migration url_probe_data_fabric_overrides: skip-keys added=%s "
            "(now %d keys)",
            skip_added or "none", len(skip),
        )


async def down(pool) -> None:
    """Remove only the entries this migration added (preserve everything else)."""
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", _OVERRIDES_KEY
        )
        overrides = _parse_overrides(raw)
        for key in _OVERRIDE_ADDITIONS:
            overrides.pop(key, None)
        await conn.execute(
            "UPDATE app_settings SET value = $2 WHERE key = $1",
            _OVERRIDES_KEY,
            json.dumps(overrides, sort_keys=True),
        )

        skip = _parse_skip_csv(
            await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", _SKIP_KEY
            )
        )
        skip = [k for k in skip if k not in _SKIP_ADDITIONS]
        await conn.execute(
            "UPDATE app_settings SET value = $2 WHERE key = $1",
            _SKIP_KEY,
            ",".join(skip),
        )
        logger.info(
            "Migration url_probe_data_fabric_overrides down: reverted overrides "
            "+ skip-key additions"
        )
