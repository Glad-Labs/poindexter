"""Migration 0132: seed app_settings for the Glad-Labs/poindexter#27 follow-up.

Three groups of keys, all defaults are no-op / safe:

1. **Active pipeline experiment key** — controls whether the content
   pipeline routes through the experiment harness. Empty = disabled
   (default). Set via ``poindexter settings set
   active_pipeline_experiment_key <key>`` to opt the pipeline into a
   declared experiment.

2. **Webhook freshness thresholds** — drive the
   ``probe_webhook_freshness`` brain probe (business_probes.py). Default
   30 days for revenue (Lemon Squeezy is quiet for a pre-revenue site —
   alerting on day 1 of zero deliveries is noise) and 7 days for
   subscribers (Resend should see at least a digest send weekly once
   the newsletter is on). Operators can tighten/loosen via app_settings.

3. **Webhook freshness probe enable + cycle** — defaults to enabled +
   24h cadence. ``probe_webhook_freshness_enabled = false`` disables
   the probe entirely (pre-revenue founders may want to silence it
   until they cut over a real provider).

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so existing operator
values are never overwritten.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SETTINGS = [
    (
        "active_pipeline_experiment_key",
        "",
        "experiments",
        "Experiment key the content pipeline routes through (matches "
        "experiments.key in the experiments table). Empty = disabled. "
        "Set via `poindexter settings set active_pipeline_experiment_key "
        "<key>` to opt the pipeline into an A/B experiment. The harness "
        "is best-effort — a misconfigured experiment never halts the "
        "pipeline, just falls back to default config.",
    ),
    (
        "probe_webhook_freshness_enabled",
        "true",
        "monitoring",
        "Master switch for the brain's webhook-freshness probe. When "
        "true, the probe checks revenue_events / subscriber_events row "
        "freshness on its cycle and notifies the operator if either "
        "table has gone quiet beyond its threshold. Disable to silence "
        "the alert (e.g. pre-revenue, while you're still configuring "
        "providers).",
    ),
    (
        "probe_webhook_freshness_interval_minutes",
        "1440",
        "monitoring",
        "How often the webhook-freshness probe runs (minutes). Default "
        "1440 = once a day. The probe is cheap (two SELECT MAX queries) "
        "but the alerts are noisy if it fires hourly — daily catches "
        "real outages within a day, which is the right SLA for a "
        "passive revenue stream.",
    ),
    (
        "webhook_freshness_revenue_threshold_days",
        "30",
        "monitoring",
        "Notify operator if no row has been added to revenue_events in "
        "this many days. Default 30 because Lemon Squeezy is intentionally "
        "quiet on a pre-revenue site — a 1-day threshold would page Matt "
        "every morning. Operators with active stores can tighten this "
        "(e.g. 7 days) once they have steady deliveries.",
    ),
    (
        "webhook_freshness_subscriber_threshold_days",
        "7",
        "monitoring",
        "Notify operator if no row has been added to subscriber_events "
        "in this many days. Default 7 because Resend should see at "
        "least one digest send weekly once newsletters are running. "
        "Loosen if the newsletter cadence is monthly.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration 0131"
            )
            return

        seeded = 0
        for key, value, category, description in _SETTINGS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            if result == "INSERT 0 1":
                seeded += 1
        logger.info(
            "Migration 0132: seeded %d new issue-#27 follow-up setting(s) "
            "(existing operator values left untouched)",
            seeded,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _category, _description in _SETTINGS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", key,
            )
        logger.info(
            "Migration 0132 rolled back: removed %d issue-#27 settings",
            len(_SETTINGS),
        )
