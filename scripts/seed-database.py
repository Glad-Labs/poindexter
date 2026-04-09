#!/usr/bin/env python3
"""
Glad Labs — Golden Database Seed (Python runner)

Ships with the Quick Start Guide. Runs seed-database.sql against a PostgreSQL
database and validates that all required keys exist afterward.

Usage:
    python scripts/seed-database.py
    python scripts/seed-database.py --database-url postgresql://user:pass@host:port/db
    DATABASE_URL=postgresql://... python scripts/seed-database.py

Environment:
    DATABASE_URL  — PostgreSQL connection string (fallback default below)
"""

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_DATABASE_URL = (
    "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain"
)

SCRIPT_DIR = Path(__file__).resolve().parent
SQL_FILE = SCRIPT_DIR / "seed-database.sql"

# All required keys that MUST exist after seeding.
# Grouped by category for readable validation output.
REQUIRED_KEYS: dict[str, list[str]] = {
    "api_keys": [
        "anthropic_api_key",
        "gemini_api_key",
        "google_api_key",
        "mercury_api_token",
        "openai_api_key",
        "pexels_api_key",
        "resend_api_key",
        "sentry_dsn",
        "serper_api_key",
    ],
    "auth": [
        "api_token",
        "jwt_secret_key",
        "secret_key",
    ],
    "content": [
        "content_max_refinement_attempts",
        "content_min_word_count",
        "content_target_word_count",
        "writing_style_reference",
    ],
    "cors": [
        "allowed_origins",
        "rate_limit_per_minute",
    ],
    "cost": [
        "cost_alert_threshold_pct",
        "daily_spend_limit",
        "electricity_rate_kwh",
        "gpu_idle_watts",
        "gpu_inference_watts",
        "monthly_spend_limit",
        "ollama_electricity_cost_per_1k_tokens",
        "system_idle_watts",
    ],
    "features": [
        "enable_mcp_server",
        "enable_memory_system",
        "enable_training_capture",
        "redis_enabled",
    ],
    "general": [
        "api_url",
        "company_name",
        "database_pool_max_size",
        "database_pool_min_size",
        "development_mode",
        "disable_auth_for_dev",
        "enable_sdxl_warmup",
        "gpu_name",
        "gpu_vram_gb",
        "grafana_url",
        "grafana_user",
        "host_home",
        "image_model",
        "log_to_file",
        "model_role_image_decision",
        "newsletter_email",
        "nvidia_exporter_url",
        "ollama_base_url",
        "openclaw_gateway_url",
        "operator_id",
        "owner_email",
        "owner_name",
        "podcast_description",
        "podcast_name",
        "privacy_email",
        "redis_url",
        "sdxl_server_url",
        "sentry_enabled",
        "site_description",
        "site_domain",
        "site_name",
        "site_tagline",
        "site_url",
        "support_email",
        "video_feed_name",
        "video_server_url",
        "gpu_busy_threshold_percent",
        "gpu_gaming_check_interval",
        "gpu_gaming_clear_checks",
        "gpu_gaming_confirm_checks",
        "hardware_cost_total",
        "hardware_useful_life_months",
        "cloudflare_account_id",
        "cloudflare_r2_access_key",
        "cloudflare_r2_bucket",
        "cloudflare_r2_endpoint",
        "cloudflare_r2_secret_key",
        "cloudflare_r2_token",
        "r2_public_url",
        "tts_acronym_replacements",
        "tts_pronunciations",
        "location_state",
    ],
    "gpu": [
        "ollama_num_ctx",
    ],
    "identity": [
        "api_base_url",
        "company_age_months",
        "company_founded_date",
        "company_founded_year",
        "company_founder_name",
        "company_name",
        "company_products",
        "company_team_size",
        "discord_ops_channel_id",
        "gpu_model",
        "newsletter_from_email",
        "privacy_email",
        "site_domain",
        "site_name",
        "site_url",
        "support_email",
    ],
    "image": [
        "enable_featured_image",
        "image_generation_model",
        "image_negative_prompt",
        "image_primary_source",
        "image_style_business",
        "image_style_default",
        "image_style_engineering",
        "image_style_insights",
        "image_style_security",
        "image_style_startup",
        "image_style_technology",
    ],
    "integration": [
        "cloudinary_api_key",
        "cloudinary_api_secret",
        "cloudinary_cloud_name",
        "discord_bot_token",
        "discord_voice_bot_token",
        "elevenlabs_api_key",
        "gitea_password",
        "gitea_repo",
        "gitea_url",
        "gitea_user",
        "grafana_api_key",
        "grafana_referral_url",
        "notion_api_key",
        "patreon_account",
        "telegram_bot_token",
    ],
    "integrations": [
        "devto_api_key",
        "revalidate_secret",
    ],
    "model_roles": [
        "model_role_code_review",
        "model_role_creative",
        "model_role_critic",
        "model_role_factchecker",
        "model_role_image_prompt",
        "model_role_seo",
        "model_role_summarizer",
        "model_role_writer",
    ],
    "models": [
        "cloud_api_daily_limit",
        "cloud_api_mode",
        "cloud_api_notify_on_use",
        "pipeline_critic_model",
        "pipeline_fallback_model",
        "pipeline_seo_model",
        "pipeline_social_model",
        "pipeline_writer_model",
    ],
    "notifications": [
        "discord_ops_webhook_url",
        "preview_base_url",
        "telegram_alerts_enabled",
        "telegram_alert_types",
        "telegram_chat_id",
    ],
    "pipeline": [
        "approval_ttl_days",
        "auto_publish_threshold",
        "content_quality_minimum",
        "content_weekly_cap",
        "daily_budget_usd",
        "daily_post_limit",
        "default_model_tier",
        "max_approval_queue",
        "max_posts_per_day",
        "max_task_retries",
        "max_tokens_per_request",
        "max_tokens_per_task",
        "min_curation_score",
        "pipeline_factcheck_model",
        "pipeline_refinement_model",
        "pipeline_research_model",
        "publish_spacing_hours",
        "staging_mode",
        "stale_task_timeout_minutes",
        "task_sweep_interval_seconds",
    ],
    "podcast": [
        "podcast_rss_url",
    ],
    "qa_workflows": [
        "qa_workflow_blog_content",
        "qa_workflow_premium_content",
        "qa_workflow_quick_check",
    ],
    "quality": [
        "qa_critical_dimension_floor",
        "qa_critic_weight",
        "qa_final_score_threshold",
        "qa_overall_score_threshold",
        "qa_validator_weight",
    ],
    "security": [
        "api_auth_token",
    ],
    "site": [
        "public_site_url",
    ],
    "social": [
        "social_linkedin_url",
        "social_x_handle",
        "social_x_url",
    ],
    "system": [
        "local_database_url",
        "repo_root",
    ],
    "tokens": [
        "content_temperature",
        "max_tokens_default",
        "qa_standard_max_tokens",
        "qa_temperature",
        "qa_thinking_model_max_tokens",
    ],
    "webhooks": [
        "openclaw_webhook_token",
        "openclaw_webhook_url",
    ],
}


def total_required() -> int:
    """Count unique required keys across all categories."""
    all_keys: set[str] = set()
    for keys in REQUIRED_KEYS.values():
        all_keys.update(keys)
    return len(all_keys)


# ---------------------------------------------------------------------------
# Database helpers (psycopg2 — ships with most Python/Postgres setups)
# ---------------------------------------------------------------------------

def get_connection(database_url: str):
    """Connect using psycopg2 (preferred) or fall back to psql subprocess."""
    try:
        import psycopg2  # noqa: F811
        return psycopg2.connect(database_url)
    except ImportError:
        return None


def run_with_psql(database_url: str, sql: str) -> tuple[bool, str]:
    """Fallback: run SQL via psql subprocess."""
    import subprocess
    result = subprocess.run(
        ["psql", database_url, "-f", "-"],
        input=sql,
        capture_output=True,
        text=True,
        timeout=30,
    )
    success = result.returncode == 0
    output = result.stdout + result.stderr
    return success, output


def validate_with_psql(database_url: str) -> set[str]:
    """Fallback: query existing keys via psql."""
    import subprocess
    result = subprocess.run(
        ["psql", database_url, "-t", "-A", "-c", "SELECT key FROM app_settings;"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        print(f"  psql validation query failed: {result.stderr.strip()}")
        return set()
    return {line.strip() for line in result.stdout.strip().split("\n") if line.strip()}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed the Glad Labs app_settings table with golden defaults."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="PostgreSQL connection string (or set DATABASE_URL env var)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Skip seeding — only check that all required keys exist",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-category output",
    )
    args = parser.parse_args()

    database_url: str = args.database_url
    print(f"Database: {_redact_url(database_url)}")
    print()

    # ---- Seed ----
    if not args.validate_only:
        if not SQL_FILE.exists():
            print(f"ERROR: SQL file not found: {SQL_FILE}")
            return 1

        sql = SQL_FILE.read_text(encoding="utf-8")
        print(f"Running seed SQL ({SQL_FILE.name})...")

        conn = get_connection(database_url)
        if conn is not None:
            try:
                cur = conn.cursor()
                cur.execute(sql)
                # Fetch the summary query result
                if cur.description:
                    rows = cur.fetchall()
                    print()
                    print(f"  {'Category':<20} {'Total':>6} {'Secrets':>8} {'Needs Config':>13}")
                    print(f"  {'-'*20} {'-'*6} {'-'*8} {'-'*13}")
                    for row in rows:
                        cat, total, secrets, needs = row
                        print(f"  {cat:<20} {total:>6} {secrets:>8} {needs:>13}")
                conn.commit()
                print()
                print("Seed complete.")
            except Exception as e:
                conn.rollback()
                print(f"ERROR during seed: {e}")
                return 1
            finally:
                conn.close()
        else:
            print("  psycopg2 not installed — falling back to psql CLI...")
            success, output = run_with_psql(database_url, sql)
            if not success:
                print(f"ERROR: psql seed failed:\n{output}")
                return 1
            print(output)
            print("Seed complete.")

        print()

    # ---- Validate ----
    print("Validating required keys...")

    conn = get_connection(database_url)
    existing_keys: set[str] = set()

    if conn is not None:
        try:
            cur = conn.cursor()
            cur.execute("SELECT key FROM app_settings;")
            existing_keys = {row[0] for row in cur.fetchall()}
        except Exception as e:
            print(f"ERROR during validation: {e}")
            return 1
        finally:
            conn.close()
    else:
        existing_keys = validate_with_psql(database_url)

    total_expected = total_required()
    missing_by_category: dict[str, list[str]] = {}
    found_count = 0

    for category, keys in sorted(REQUIRED_KEYS.items()):
        missing = [k for k in keys if k not in existing_keys]
        present = len(keys) - len(missing)
        found_count += present

        if missing:
            missing_by_category[category] = missing
            if not args.quiet:
                print(f"  [{category}] {present}/{len(keys)} — MISSING: {', '.join(missing)}")
        else:
            if not args.quiet:
                print(f"  [{category}] {present}/{len(keys)} — OK")

    print()
    total_missing = sum(len(v) for v in missing_by_category.values())

    if total_missing == 0:
        print(f"All {total_expected} required keys present. Database is ready.")
        print(f"Total keys in database: {len(existing_keys)}")
        return 0
    else:
        print(f"INCOMPLETE: {total_missing} required key(s) missing out of {total_expected}.")
        print("Run without --validate-only to seed missing keys.")
        return 1


def _redact_url(url: str) -> str:
    """Redact password from database URL for display."""
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)


if __name__ == "__main__":
    sys.exit(main())
