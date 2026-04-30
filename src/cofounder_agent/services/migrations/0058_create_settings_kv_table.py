"""
Migration 0058: Create app_settings key-value table

Introduces a dedicated key-value settings table (`app_settings`) to replace
most environment variables with DB-stored configuration managed via OpenClaw.

The existing `settings` table (JSONB-valued, used by the auth/admin system)
is left untouched. `app_settings` uses a simpler TEXT value column with
category grouping, secret masking, and an updated_at trigger.

Includes a `seed_defaults` function that pre-populates expected keys with
empty/default values so OpenClaw can discover and fill them.
"""

import secrets

from services.logger_config import get_logger

logger = get_logger(__name__)

UP = """
CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    category VARCHAR(100) NOT NULL DEFAULT 'general',
    description TEXT,
    is_secret BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ensure is_active exists on databases that pre-date this column being part
-- of CREATE TABLE (added retroactively for fresh-DB migration smoke parity —
-- migrations 0093/0099/0101/0104/0105 INSERT INTO app_settings (..., is_active)).
ALTER TABLE app_settings
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_app_settings_category ON app_settings(category);
"""

DOWN = """
DROP INDEX IF EXISTS idx_app_settings_category;
DROP TABLE IF EXISTS app_settings;
"""


async def up(pool):
    """Apply migration 0058: create app_settings table and seed defaults."""
    async with pool.acquire() as conn:
        await conn.execute(UP)
    logger.info("Created app_settings table")
    await seed_defaults(pool)


async def down(pool):
    """Revert migration 0058: drop app_settings table."""
    async with pool.acquire() as conn:
        await conn.execute(DOWN)
    logger.info("Dropped app_settings table")


async def seed_defaults(pool):
    """Insert default setting keys so OpenClaw can discover them.

    Uses ON CONFLICT DO NOTHING so existing values are never overwritten.
    Secret keys that need auto-generation (secret_key, jwt_secret_key) are
    populated with a random token on first insert only.
    """
    secret_key = secrets.token_urlsafe(48)
    jwt_secret_key = secrets.token_urlsafe(48)

    defaults = [
        # --- api_keys ---
        ("anthropic_api_key", "", "api_keys", "Anthropic API key", True),
        ("openai_api_key", "", "api_keys", "OpenAI API key", True),
        ("google_api_key", "", "api_keys", "Google AI API key", True),
        ("pexels_api_key", "", "api_keys", "Pexels image API key", True),
        ("serper_api_key", "", "api_keys", "Serper search API key", True),
        ("sentry_dsn", "", "api_keys", "Sentry DSN for error tracking", True),
        # --- pipeline ---
        ("auto_publish_threshold", "0", "pipeline", "Quality score threshold for auto-publishing (0=disabled)", False),
        ("daily_budget_usd", "5.00", "pipeline", "Daily LLM API spend budget in USD", False),
        ("default_model_tier", "budget", "pipeline", "Default model cost tier (free/budget/standard/premium/flagship)", False),
        # --- auth ---
        ("api_token", "", "auth", "API token for frontend-to-backend authentication", True),
        ("secret_key", secret_key, "auth", "Application secret key (auto-generated)", True),
        ("jwt_secret_key", jwt_secret_key, "auth", "JWT signing secret (auto-generated)", True),
        # --- features ---
        ("enable_training_capture", "false", "features", "Enable training data capture from pipeline runs", False),
        ("enable_mcp_server", "true", "features", "Enable Model Context Protocol server", False),
        ("enable_memory_system", "true", "features", "Enable agent memory system", False),
        ("redis_enabled", "false", "features", "Enable Redis for caching and pub/sub", False),
        # --- cors ---
        # Default empty — operator must configure before exposing the API.
        # Matches how webhook URLs + API tokens are seeded here: placeholder
        # that forces a conscious setup step, rather than a hardcoded domain
        # that silently mis-points a forked Poindexter install at gladlabs.io.
        # Operators set this post-install via `poindexter cli config set
        # allowed_origins "https://your-site.com,https://www.your-site.com"`
        # or directly in the settings UI.
        ("allowed_origins", "", "cors", "Comma-separated allowed CORS origins. Must be set before exposing the API publicly — empty value means no cross-origin requests are accepted.", False),
        ("rate_limit_per_minute", "100", "cors", "Max API requests per minute per client", False),
        # --- webhooks ---
        ("openclaw_webhook_url", "", "webhooks", "OpenClaw webhook delivery URL", False),
        ("openclaw_webhook_token", "", "webhooks", "OpenClaw webhook auth token", True),
    ]

    insert_sql = """
        INSERT INTO app_settings (key, value, category, description, is_secret)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (key) DO NOTHING
    """

    async with pool.acquire() as conn:
        inserted = 0
        for key, value, category, description, is_secret in defaults:
            result = await conn.execute(insert_sql, key, value, category, description, is_secret)
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(f"Seeded {inserted} default app_settings (skipped {len(defaults) - inserted} existing)")
