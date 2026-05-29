"""Regression guard for the squashed baseline migration.

The 0000_baseline.py + .schema.sql + .seeds.sql triple has absorbed three
flatten passes — Phase A on 2026-05-08 (169 historical files), Phase C on
2026-05-28 (61 timestamped files), and Phase D on 2026-05-29 (5 post-#691
files: media-notify seed, lab-observability columns + view, Cloudflare
beacon seeds, experiments harness + winner-label). Future drift between the
sanitized schema SQL and the runner-discovered schema is the most
likely break mode: someone edits one file without touching the other,
the baseline still applies (because ON CONFLICT / IF NOT EXISTS swallow
the discrepancy), and CI doesn't notice because migrations_smoke only
asserts row-count parity, not schema content.

This test asserts the baseline-applied DB contains the load-bearing
tables, columns, and seeded rows that downstream code expects. It's
not a byte-exact golden-file match (that lives in
``scripts/ci/migrations_smoke.py``'s live CI parity check against a
fresh Postgres); it's a faster signal that catches "someone deleted a
column from schema.sql but the application code still expects it" and
"someone removed a seed row that defaults_loader assumes is present".

Runs against the session-scoped ``test_pool`` fixture, which applies
the full ``services/migrations/`` tree (currently just the baseline)
to a disposable ``poindexter_test_<hex>`` DB. The fixture is gated on
``INTEGRATION_DB`` and skips cleanly when no live Postgres is
available.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Tables that downstream code imports directly (must exist after baseline).
# ---------------------------------------------------------------------------
_REQUIRED_TABLES = (
    # Pipeline + content
    "pipeline_tasks",
    "pipeline_versions",
    "pipeline_gate_history",
    "posts",
    "media_approvals",  # added by post-baseline phase-C migrations
    # Config
    "app_settings",
    "qa_gates",
    "content_validator_rules",
    "niches",
    "niche_goals",
    "niche_sources",
    # Brain
    "brain_knowledge",
    "brain_decisions",
    # Auth
    "oauth_clients",
    # Audit
    "audit_log",
    "cost_logs",
    # Schema-migration tracking (substrate + per-module)
    "schema_migrations",
    "module_schema_migrations",
    # Newsletter (Phase C added unsubscribe_token)
    "newsletter_subscribers",
    # Phase D (post-#691) — experiments harness foundation
    "experiments",
    "experiment_variants",
)


# ---------------------------------------------------------------------------
# Views the baseline must materialise (Phase D absorbed these).
# ---------------------------------------------------------------------------
_REQUIRED_VIEWS = (
    # 20260528_204250_lab_observability_columns_and_view.py
    "lab_outcomes_v1",
    # 20260529_000342_phase1_experiments_harness_foundation.py
    "experiment_variant_scorecard_v1",
)


# ---------------------------------------------------------------------------
# Columns added by Phase C migrations that downstream code touches by name.
# Catching a drop of these is the highest-value parity assertion this test
# can make — they were the riskiest "did the squash lose anything?" surface.
# ---------------------------------------------------------------------------
_REQUIRED_COLUMNS = (
    # 20260527_180559_add_unsubscribe_token_to_newsletter_subscribers.py
    ("newsletter_subscribers", "unsubscribe_token"),
    # 20260527_183209_add_retry_count_to_pipeline_tasks_for_stale_sweep.py
    ("pipeline_tasks", "retry_count"),
    # 20260528_003606_add_video_shot_list_column_to_posts.py
    ("posts", "video_shot_list"),
    # 20260528_001023_add_quality_signals_columns_to_media_approvals.py
    ("media_approvals", "quality_score"),
    # 20260519_191744_backfill_posts_word_count_reading_time.py
    ("posts", "word_count"),
    ("posts", "reading_time"),
    # 20260519_134736_niches_default_media_to_generate.py + niches_default_template_slug
    ("niches", "default_media_to_generate"),
    ("niches", "default_template_slug"),
    # 20260510_014520 — gen_random_uuid default on posts.id
    ("posts", "id"),
    # Phase D (post-#691) lab-observability columns
    # 20260528_204250_lab_observability_columns_and_view.py
    ("capability_outcomes", "niche_slug"),
    ("capability_outcomes", "prompt_template_key"),
    ("capability_outcomes", "prompt_template_version"),
    ("routing_outcomes", "niche_slug"),
    ("routing_outcomes", "prompt_template_key"),
    ("routing_outcomes", "prompt_template_version"),
    ("published_post_edit_metrics", "model_used"),
    ("published_post_edit_metrics", "prompt_template_key"),
    ("published_post_edit_metrics", "prompt_template_version"),
    # 20260529_000342 — experiment telemetry FK on capability_outcomes
    ("capability_outcomes", "variant_id"),
    # 20260529_012228_phase1_experiments_winner_label.py
    ("experiments", "winner_variant_label"),
)


# ---------------------------------------------------------------------------
# Seed-data invariants. These rows must exist after a fresh baseline apply;
# losing any of them silently disables a downstream feature.
# ---------------------------------------------------------------------------
_REQUIRED_APP_SETTINGS_KEYS = (
    # Cost-guard rename closed Glad-Labs/poindexter#598
    "daily_spend_limit_usd",
    "monthly_spend_limit_usd",
    # Lane B cost-tier defaults
    "cost_tier.free.model",
    "cost_tier.budget.model",
    "cost_tier.standard.model",
    "cost_tier.premium.model",
    # Lane C cutover seam
    "default_template_slug",
    # Lane D RAG master switch
    "rag_engine_enabled",
    # QA-rail master switches
    "deepeval_enabled",
    "guardrails_enabled",
    "ragas_enabled",
    # Prefect cutover
    "use_prefect_orchestration",
    # Auto-publish global gate
    "auto_publish_threshold",
    # SDXL
    "sdxl_enabled",
    # Phase-C late additions (ops triage + prefect probe + alert dedup tuning)
    "ops_triage_writer_model",
    "prefect_stuck_flow_pending_threshold_minutes",
    "alert_repeat_suppress_window_minutes",
    # Phase D (post-#691) seeded keys — the net seeds.sql change
    # 20260528_193105_seed_media_approval_discord_notify_enabled_toggle.py
    "media_approval_discord_notify_enabled",
    # 20260528_223439_seed_cloudflare_analytics_beacon_keys.py
    "cloudflare_analytics_api_token",
    "cloudflare_analytics_last_sync",
    "cloudflare_beacon_url",
)


_REQUIRED_QA_GATES = (
    "programmatic_validator",
    "llm_critic",
    "url_verifier",
    "deepeval_brand_fabrication",
    "deepeval_g_eval",
    "deepeval_faithfulness",
    "guardrails_brand",
    "guardrails_competitor",
    "ragas_eval",
)


_REQUIRED_NICHES = ("dev_diary",)


async def test_required_tables_exist(test_pool) -> None:
    """Every table downstream code imports must exist post-baseline."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    present = {r["tablename"] for r in rows}
    missing = sorted(set(_REQUIRED_TABLES) - present)
    assert not missing, (
        f"baseline lost {len(missing)} required table(s) — "
        f"schema.sql may have drifted from runner-loaded SQL: {missing}"
    )


async def test_required_views_exist(test_pool) -> None:
    """Phase D absorbed the lab + experiment-scorecard views; the baseline
    schema.sql must recreate them or downstream reads break."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT viewname FROM pg_views WHERE schemaname = 'public'"
        )
    present = {r["viewname"] for r in rows}
    missing = sorted(set(_REQUIRED_VIEWS) - present)
    assert not missing, (
        f"baseline lost {len(missing)} required view(s) — "
        f"a Phase-D view may have been dropped from schema.sql: {missing}"
    )


async def test_required_columns_exist(test_pool) -> None:
    """Phase-C-added columns are the highest-risk parity surface."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
    present = {(r["table_name"], r["column_name"]) for r in rows}
    missing = sorted(set(_REQUIRED_COLUMNS) - present)
    assert not missing, (
        f"baseline lost {len(missing)} required column(s) — "
        f"a Phase-C alter may have been dropped from schema.sql: {missing}"
    )


async def test_required_app_settings_seeded(test_pool) -> None:
    """app_settings keys downstream code reads must seed in baseline."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch("SELECT key FROM app_settings")
    present = {r["key"] for r in rows}
    missing = sorted(set(_REQUIRED_APP_SETTINGS_KEYS) - present)
    assert not missing, (
        f"baseline.seeds.sql is missing {len(missing)} required setting(s): {missing}"
    )


async def test_required_qa_gates_seeded(test_pool) -> None:
    """Six OSS QA rails + the always-on substrate gates must seed."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM qa_gates")
    present = {r["name"] for r in rows}
    missing = sorted(set(_REQUIRED_QA_GATES) - present)
    assert not missing, (
        f"baseline.seeds.sql is missing {len(missing)} required qa_gate(s): {missing}"
    )


async def test_required_niches_seeded(test_pool) -> None:
    """The `dev_diary` niche is the founder-voice template seam."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch("SELECT slug FROM niches")
    present = {r["slug"] for r in rows}
    missing = sorted(set(_REQUIRED_NICHES) - present)
    assert not missing, (
        f"baseline.seeds.sql is missing {len(missing)} required niche(s): {missing}"
    )


async def test_baseline_self_records(test_pool) -> None:
    """The baseline migration must record itself in schema_migrations."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM schema_migrations ORDER BY name")
    names = [r["name"] for r in rows]
    assert "0000_baseline.py" in names, (
        "baseline didn't self-record in schema_migrations — runner integration "
        f"may be broken. Found: {names}"
    )


async def test_no_legacy_gitea_tables(test_pool) -> None:
    """Phase A.2 (2026-05-28) drop of the 60 legacy Gitea/Forgejo tables
    must be reflected in the squashed baseline — they should not return."""
    legacy_samples = (
        "access",
        "action",
        "action_run",
        "user",
        "repository",
        "issue",
        "pull_request",
    )
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename = ANY($1::text[])",
            list(legacy_samples),
        )
    leaked = sorted(r["tablename"] for r in rows)
    assert not leaked, (
        f"baseline shouldn't recreate dropped Gitea schema tables: {leaked}"
    )
