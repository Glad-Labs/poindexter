"""Migration 0108: Seed bind host/port for the voice WebRTC service.

Pairs with ``services/voice_agent_webrtc.py`` — the WebRTC surface
exposes the same Emma pipeline over the network so a phone or laptop
on the tailnet can reach it.

Defaults:

- Host ``0.0.0.0`` — bind on every interface. Tailscale handles
  who-can-reach-this; on a non-tailnet machine you'll want to flip
  this to ``127.0.0.1`` and front it with whatever auth you prefer.
- Port ``8003`` — picked to sit above the worker API (``8002``)
  without colliding with anything else on Matt's box.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so a pre-set custom value
survives a re-run.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "voice_agent_webrtc_host",
        "0.0.0.0",  # nosec B104  # tailnet-by-design seed for voice_agent_webrtc_host; operator can override to 127.0.0.1 via `poindexter set voice_agent_webrtc_host 127.0.0.1`
        "Bind host for the voice WebRTC service. 0.0.0.0 makes the agent "
        "reachable from any Tailscale device on the tailnet. Use "
        "127.0.0.1 for local-only.",
    ),
    (
        "voice_agent_webrtc_port",
        "8003",
        "Bind port for the voice WebRTC service. Sits above worker API "
        "(8002) and below typical dev tools.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "0108: seeded %d/%d voice WebRTC settings "
            "(remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info("0108: removed %d voice WebRTC seeds", len(_SEEDS))
