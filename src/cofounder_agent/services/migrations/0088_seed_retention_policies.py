"""Migration 0088: Seed retention_policies rows (all disabled).

Phase 2 of the Declarative Data Plane RFC. Seeds one policy per
append-only data source identified in the DB + embeddings plan
(docs/architecture/database-and-embeddings-plan-2026-04-24.md),
all with ``enabled=false``.

Flipping a policy to ``enabled=true`` is a deliberate operator action.
The runner then picks it up on the next scheduled tick (or immediately
via ``poindexter retention run <name>``).

### Seeded policies

Embeddings-table TTLs keyed on ``source_table`` via ``filter_sql``:

- embeddings.claude_sessions — 30d (ephemeral working notes)
- embeddings.audit           — 90d (quarterly review window)
- embeddings.brain           — 180d (system reasoning, slower decay)

(embeddings.issues / embeddings.memory / embeddings.posts are
explicitly NOT seeded — they have no TTL per the plan.)

Append-only tables:

- audit_log        — 90d TTL
- brain_decisions  — 90d TTL
- gpu_metrics      — 30d keep_raw + hourly rollup into gpu_metrics_hourly
                     (rollup table does NOT exist yet — operator must
                     create it before enabling this policy)

``experiments`` is left dormant per operator decision.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_EMBEDDINGS_TTL_SEEDS: list[tuple[str, str, int, str]] = [
    # (slug, source_table_filter, ttl_days, description)
    (
        "embeddings.claude_sessions",
        "claude_sessions",
        30,
        "Claude session chunks — ephemeral working notes, recent context matters",
    ),
    (
        "embeddings.audit",
        "audit",
        90,
        "Audit event embeddings — long enough for quarterly reviews",
    ),
    (
        "embeddings.brain",
        "brain",
        180,
        "Brain decision embeddings — system reasoning, slower decay",
    ),
]


_TABLE_TTL_SEEDS: list[tuple[str, str, int, str]] = [
    # (slug, table_name, ttl_days, description)
    ("audit_log", "audit_log", 90, "Audit log rows"),
    ("brain_decisions", "brain_decisions", 90, "Brain decision rows"),
]


_DOWNSAMPLE_SEEDS: list[tuple[str, str, str, str]] = [
    # (slug, table_name, downsample_rule_json, description)
    (
        "gpu_metrics",
        "gpu_metrics",
        (
            '{"keep_raw_days": 30,'
            ' "rollup_table": "gpu_metrics_hourly",'
            ' "rollup_interval": "1 hour",'
            ' "aggregations": ['
            '   {"col": "utilization_pct", "fn": "avg", "as": "avg_utilization_pct"},'
            '   {"col": "memory_used_mb",  "fn": "avg", "as": "avg_memory_used_mb"},'
            '   {"col": "power_watts",     "fn": "max", "as": "peak_power_watts"}'
            ' ]}'
        ),
        "GPU utilization rollup — keeps 30d raw + hourly beyond",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # Embeddings TTL per source_table
        for slug, source_filter, ttl_days, desc in _EMBEDDINGS_TTL_SEEDS:
            await conn.execute(
                """
                INSERT INTO retention_policies
                    (name, handler_name, table_name, filter_sql, age_column,
                     ttl_days, enabled, metadata)
                VALUES ($1, 'ttl_prune', 'embeddings', $2, 'created_at',
                        $3, FALSE,
                        jsonb_build_object('description', $4::text))
                ON CONFLICT (name) DO NOTHING
                """,
                slug,
                f"source_table = '{source_filter}'",
                ttl_days,
                desc,
            )

        # Append-only table TTLs
        for slug, table_name, ttl_days, desc in _TABLE_TTL_SEEDS:
            await conn.execute(
                """
                INSERT INTO retention_policies
                    (name, handler_name, table_name, age_column,
                     ttl_days, enabled, metadata)
                VALUES ($1, 'ttl_prune', $2, 'created_at', $3, FALSE,
                        jsonb_build_object('description', $4::text))
                ON CONFLICT (name) DO NOTHING
                """,
                slug,
                table_name,
                ttl_days,
                desc,
            )

        # Downsample policies
        for slug, table_name, rule_json, desc in _DOWNSAMPLE_SEEDS:
            await conn.execute(
                """
                INSERT INTO retention_policies
                    (name, handler_name, table_name, age_column,
                     downsample_rule, enabled, metadata)
                VALUES ($1, 'downsample', $2, 'sampled_at', $3::jsonb, FALSE,
                        jsonb_build_object(
                            'description', $4::text,
                            'precondition', 'rollup_table gpu_metrics_hourly must exist — see handler doc'
                        ))
                ON CONFLICT (name) DO NOTHING
                """,
                slug,
                table_name,
                rule_json,
                desc,
            )

        logger.info(
            "0088: seeded %d retention policies (all disabled)",
            len(_EMBEDDINGS_TTL_SEEDS) + len(_TABLE_TTL_SEEDS) + len(_DOWNSAMPLE_SEEDS),
        )


async def down(pool) -> None:
    names = (
        [s[0] for s in _EMBEDDINGS_TTL_SEEDS]
        + [s[0] for s in _TABLE_TTL_SEEDS]
        + [s[0] for s in _DOWNSAMPLE_SEEDS]
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retention_policies WHERE name = ANY($1::text[])",
            names,
        )
        logger.info("0088: removed seeded retention policies")
