"""Migration 0073: alert_rules table + Grafana sync config (GH-28).

GitHub issue Glad-Labs/poindexter#28 — make Grafana alerts tunable from
the DB instead of baking them into a YAML file that requires a redeploy
to change. The brain daemon picks up rows from ``alert_rules`` and pushes
them to Grafana's provisioning API on each cycle.

Creates one table + seeds two app_settings + seeds the current baseline
of alert rules from ``infrastructure/grafana/alerts/discord-alerts.yaml``
so an operator's first ``docker compose up`` already has working alerts.

Schema:

* ``alert_rules`` — one row per Grafana alert. Columns are the superset
  of what Grafana's ``/api/v1/provisioning/alert-rules`` POST expects
  plus the fields the sync loop needs for drift detection.

Config keys (in app_settings):

* ``grafana_api_base_url`` — defaults to ``http://poindexter-grafana:3000``
  (the compose service name). Reachable from the brain container.
* ``grafana_api_token`` — empty by default with a TODO note. The operator
  generates a service-account token under Grafana → Administration →
  Service accounts and sets this value via the admin UI or
  ``mcp__poindexter__set_setting``. Without it, the sync loop logs a
  warning and skips (it does NOT crash — auth is an operator setup step).

Idempotent: ``INSERT ... ON CONFLICT DO NOTHING`` on both the table and
the settings rows. Rerunning the migration is a no-op.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


# Seeded alert rules — mirror the 4 rules currently in
# infrastructure/grafana/alerts/discord-alerts.yaml so operators start
# with the same coverage we ship today. Each entry is
# (name, promql_query, threshold, duration, severity, labels, annotations).
# Queries that target Postgres use the ``poindexter-postgres`` datasource
# (resolved in the sync loop). Queries that target Prometheus use the
# default ``Prometheus`` datasource.
_SEED_RULES = [
    {
        "name": "Daily Spend Over 80% Budget",
        "promql_query": (
            "SELECT COALESCE(SUM(cost_usd), 0) as spend FROM cost_logs "
            "WHERE created_at > date_trunc('day', NOW())"
        ),
        "threshold": 4.0,
        "duration": "0m",
        "severity": "warning",
        "labels": {"team": "glad-labs", "category": "cost"},
        "annotations": {
            "summary": "Daily LLM spend has exceeded 80% of the $5 budget",
            "description": (
                "Current daily spend is {{ $value }} USD. Budget limit is "
                "$5.00/day. Investigate provider costs and consider "
                "switching to cheaper model tiers."
            ),
        },
    },
    {
        "name": "Pipeline Failure Rate Over 20%",
        "promql_query": (
            "SELECT ROUND(COUNT(*) FILTER (WHERE status = 'failed') * 100.0 "
            "/ NULLIF(COUNT(*), 0), 1) as failure_rate "
            "FROM pipeline_tasks_view "
            "WHERE created_at > NOW() - INTERVAL '1 hour'"
        ),
        "threshold": 20.0,
        "duration": "0m",
        "severity": "critical",
        "labels": {"team": "glad-labs", "category": "pipeline"},
        "annotations": {
            "summary": "Pipeline failure rate exceeded 20% in the last hour",
            "description": (
                "{{ $value }}% of tasks failed in the last hour. Check "
                "error logs for common failure patterns."
            ),
        },
    },
    {
        "name": "Tasks Stuck In Progress",
        "promql_query": (
            "SELECT COUNT(*) as stuck_count FROM pipeline_tasks_view "
            "WHERE status = 'in_progress' "
            "AND updated_at < NOW() - INTERVAL '30 minutes'"
        ),
        "threshold": 0.0,
        "duration": "0m",
        "severity": "warning",
        "labels": {"team": "glad-labs", "category": "pipeline"},
        "annotations": {
            "summary": "{{ $value }} task(s) stuck in_progress for over 30 minutes",
            "description": (
                "Tasks have been in_progress without updates for more than "
                "30 minutes. May indicate a hung worker or lost WebSocket."
            ),
        },
    },
    {
        "name": "Poindexter Worker Down",
        "promql_query": 'up{job="poindexter-worker"}',
        "threshold": 1.0,
        "duration": "2m",
        "severity": "critical",
        "labels": {"team": "glad-labs", "category": "infrastructure"},
        "annotations": {
            "summary": "Poindexter worker is not reachable",
            "description": (
                "The worker has failed Prometheus scrapes for 2+ minutes. "
                "Check `docker ps` and `docker logs poindexter-worker`."
            ),
        },
    },
]


_SEED_SETTINGS = [
    (
        "grafana_api_base_url",
        "http://poindexter-grafana:3000",
        "monitoring",
        (
            "Grafana base URL the brain daemon uses to push alert rules "
            "and contact points. Defaults to the docker-compose service "
            "name, reachable from the brain container. Override for "
            "externally-hosted Grafana. Ref: GH-28."
        ),
        False,
    ),
    (
        "grafana_api_token",
        "",
        "monitoring",
        (
            "Grafana service-account token (Administration → Service "
            "accounts → Add service account → Add token). Required for "
            "the brain daemon's Grafana alert sync loop. Empty value "
            "means sync is skipped with a warning; operator must set "
            "this via the admin UI. Ref: GH-28."
        ),
        True,  # is_secret
    ),
    (
        "grafana_alert_sync_interval_cycles",
        "3",
        "monitoring",
        (
            "How many brain cycles (5 min each) between Grafana alert "
            "syncs. Default 3 = 15 min. Lowering this makes alert rule "
            "changes propagate faster at the cost of API chatter. "
            "Ref: GH-28."
        ),
        False,
    ),
    (
        "grafana_alert_sync_enabled",
        "true",
        "monitoring",
        (
            "Master switch for the brain daemon's Grafana alert sync "
            "loop. Set to 'false' to disable the loop entirely without "
            "removing rules. Ref: GH-28."
        ),
        False,
    ),
]


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS alert_rules (
    id              SERIAL PRIMARY KEY,
    name            TEXT        NOT NULL UNIQUE,
    promql_query    TEXT        NOT NULL,
    threshold       NUMERIC     NOT NULL,
    duration        TEXT        NOT NULL DEFAULT '0m',
    severity        TEXT        NOT NULL DEFAULT 'warning',
    enabled         BOOLEAN     NOT NULL DEFAULT TRUE,
    labels_json     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    annotations_json JSONB      NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled
    ON alert_rules (enabled) WHERE enabled = TRUE;
"""


_DROP_TABLE_SQL = "DROP TABLE IF EXISTS alert_rules"


async def up(pool) -> None:
    import json

    async with pool.acquire() as conn:
        # Create table
        await conn.execute(_CREATE_TABLE_SQL)
        logger.info("Migration 0073: created alert_rules table")

        # Seed rows from the current YAML baseline
        for rule in _SEED_RULES:
            await conn.execute(
                """
                INSERT INTO alert_rules (
                    name, promql_query, threshold, duration, severity,
                    enabled, labels_json, annotations_json
                ) VALUES ($1, $2, $3, $4, $5, TRUE, $6::jsonb, $7::jsonb)
                ON CONFLICT (name) DO NOTHING
                """,
                rule["name"],
                rule["promql_query"],
                rule["threshold"],
                rule["duration"],
                rule["severity"],
                json.dumps(rule["labels"]),
                json.dumps(rule["annotations"]),
            )
        logger.info(
            "Migration 0073: seeded %d alert_rules rows", len(_SEED_RULES)
        )

        # Seed Grafana sync config keys into app_settings
        if not await _table_exists(conn, "app_settings"):
            logger.warning(
                "Table 'app_settings' missing — "
                "skipping Grafana sync settings seed in migration 0073"
            )
            return

        for key, value, category, description, is_secret in _SEED_SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description, is_secret,
            )
        logger.info(
            "Migration 0073: seeded %d Grafana sync settings",
            len(_SEED_SETTINGS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        # Remove settings first (they reference nothing)
        if await _table_exists(conn, "app_settings"):
            keys = [k for k, *_ in _SEED_SETTINGS]
            await conn.execute(
                "DELETE FROM app_settings WHERE key = ANY($1::text[])",
                keys,
            )
        await conn.execute(_DROP_TABLE_SQL)
        logger.info("Migration 0073 rolled back: dropped alert_rules + settings")
