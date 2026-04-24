"""Migration 0085: Seed ``webhook_endpoints`` rows for existing integrations.

Phase 1 of the Declarative Data Plane RFC. Seeds one row per
currently-deployed inbound webhook so operators can swap to the
catch-all ``/api/webhooks/{name}`` URL at their leisure. Every row
ships with ``enabled=false`` so this migration is a no-op until an
operator flips it on.

Outbound destinations (Discord/Telegram notifications, Vercel ISR)
land in a follow-up migration once the outbound dispatcher handlers
are implemented.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEEDS: list[tuple[str, str, str, str, str]] = [
    # (name, handler_name, signing_algorithm, secret_key_ref, description)
    (
        "lemon_squeezy",
        "revenue_event_writer",
        "hmac-sha256",
        "lemon_squeezy_webhook_secret",
        "Lemon Squeezy order/subscription events → revenue_events",
    ),
    (
        "resend",
        "subscriber_event_writer",
        "svix",
        "resend_webhook_secret",
        "Resend email events → subscriber_events",
    ),
    (
        "alertmanager",
        "alertmanager_dispatch",
        "bearer",
        "alertmanager_webhook_token",
        "Grafana Alertmanager alerts → alert_events + operator fan-out",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for name, handler_name, algo, secret_ref, description in _SEEDS:
            await conn.execute(
                """
                INSERT INTO webhook_endpoints
                    (name, direction, handler_name, signing_algorithm,
                     secret_key_ref, enabled, metadata)
                VALUES ($1, 'inbound', $2, $3, $4, FALSE,
                        jsonb_build_object('description', $5::text))
                ON CONFLICT (name) DO NOTHING
                """,
                name,
                handler_name,
                algo,
                secret_ref,
                description,
            )
        logger.info(
            "0085: seeded %d inbound webhook endpoints (all disabled)",
            len(_SEEDS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for name, *_ in _SEEDS:
            await conn.execute(
                "DELETE FROM webhook_endpoints WHERE name = $1",
                name,
            )
        logger.info("0085: removed %d seeded webhook endpoints", len(_SEEDS))
