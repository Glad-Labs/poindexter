"""
Admin Database Module

Handles administrative database operations including:
- Logging and audit trails
- Financial tracking and cost management
- Agent status monitoring
- System settings management
- Health checks
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from asyncpg import Pool

from schemas.database_response_models import (
    CostLogResponse,
    SettingResponse,
    TaskCostBreakdownResponse,
)
from schemas.model_converter import ModelConverter
from services.logger_config import get_logger

from .database_mixin import DatabaseServiceMixin
from .decorators import log_query_performance

logger = get_logger(__name__)


class AdminDatabase(DatabaseServiceMixin):
    """Administrative database operations (logs, financial, settings, health)."""

    # In-memory TTL cache for settings (rarely changed, frequently read)
    _SETTINGS_CACHE_TTL = 60  # seconds

    def __init__(self, pool: Pool):
        """
        Initialize admin database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool
        self._settings_cache: dict[str, Any] = {}  # key -> {"value": ..., "ts": monotonic}
        self._all_settings_cache: dict[str, Any] = {}  # category_key -> {"value": ..., "ts": ...}

    def _invalidate_settings_cache(self) -> None:
        """Clear all settings caches (call after set/delete)."""
        self._settings_cache.clear()
        self._all_settings_cache.clear()

    # ========================================================================
    # COST LOGGING
    # ========================================================================

    @log_query_performance(operation="log_cost", category="cost_write")
    async def log_cost(self, cost_log: dict[str, Any]) -> CostLogResponse:
        """
        Log cost of LLM API call to cost_logs table.

        Args:
            cost_log: Dict with:
                - task_id (UUID or str)
                - user_id (UUID or str, optional)
                - phase (str): research, outline, draft, assess, refine, finalize
                - model (str): cost tier (ultra_cheap, cheap, balanced, premium) or resolved model name
                - provider (str): ollama
                - cost_usd (float): Cost in USD
                - input_tokens (int, optional): Input token count
                - output_tokens (int, optional): Output token count
                - total_tokens (int, optional): Total token count
                - quality_score (float, optional): 1-5 star rating
                - duration_ms (int, optional): Execution time in milliseconds
                - success (bool, optional): Whether call succeeded (default: True)
                - error_message (str, optional): Error details if failed

        Returns:
            Created cost_log record
        """
        try:
            sql = """
                INSERT INTO cost_logs (
                    task_id, user_id, phase, model, provider,
                    input_tokens, output_tokens, total_tokens,
                    cost_usd, quality_score, duration_ms, success, error_message,
                    created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW(), NOW())
                RETURNING *
            """
            params = [
                cost_log["task_id"],
                cost_log.get("user_id"),
                cost_log["phase"],
                cost_log["model"],
                cost_log["provider"],
                cost_log.get("input_tokens", 0),
                cost_log.get("output_tokens", 0),
                cost_log.get("total_tokens", 0),
                float(cost_log.get("cost_usd", 0.0)),
                cost_log.get("quality_score"),
                cost_log.get("duration_ms"),
                cost_log.get("success", True),
                cost_log.get("error_message"),
            ]

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                logger.info(
                    "Logged cost for %s: $%.6f (%s)",
                    cost_log['phase'], cost_log.get('cost_usd', 0), cost_log['model'],
                )
                return ModelConverter.to_cost_log_response(row)
        except Exception as e:
            logger.error(
                "[log_cost] Error logging cost for task_id=%s, phase=%s, model=%s: %s",
                cost_log.get('task_id'), cost_log.get('phase'), cost_log.get('model'), e,
                exc_info=True,
            )
            raise

    @log_query_performance(operation="get_task_costs", category="cost_retrieval")
    async def get_task_costs(self, task_id: str) -> TaskCostBreakdownResponse:
        """
        Get cost breakdown for a task by phase.

        Args:
            task_id: Task ID

        Returns:
            {
                "research": {"cost": 0.0, "model": "ultra_cheap", "count": 1},
                "outline": {"cost": 0.00075, "model": "cheap", "count": 1},
                "draft": {"cost": 0.0015, "model": "premium", "count": 1},
                "total": 0.00225,
                "entries": [...]
            }
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, task_id, user_id, phase, model, provider,
                           input_tokens, output_tokens, total_tokens, cost_usd,
                           quality_score, duration_ms, success, error_message,
                           created_at, updated_at
                    FROM cost_logs
                    WHERE task_id = $1
                    ORDER BY created_at ASC
                    """,
                    str(task_id),
                )

                if not rows:
                    return TaskCostBreakdownResponse(total=0.0, entries=[])  # type: ignore[call-arg]

                # Group by phase
                breakdown = {}
                total_cost = 0.0
                entries = []

                for row in rows:
                    entries.append(ModelConverter.to_cost_log_response(row))

                    phase = row["phase"]
                    cost = float(row["cost_usd"] or 0.0)

                    if phase not in breakdown:
                        breakdown[phase] = {"cost": 0.0, "model": row["model"], "count": 0}

                    breakdown[phase]["cost"] += cost
                    breakdown[phase]["count"] += 1
                    total_cost += cost

                # Create response with appropriate fields
                response_data = {
                    "total": round(total_cost, 6),
                    "entries": entries,
                }

                # Add phase-specific costs
                for phase in ["research", "outline", "draft", "assess", "refine", "finalize"]:
                    if phase in breakdown:
                        response_data[phase] = breakdown[phase]

                logger.info(
                    "Retrieved costs for task %s: $%.6f across %d entries",
                    task_id, total_cost, len(entries),
                )
                return TaskCostBreakdownResponse(**response_data)
        except Exception as e:
            logger.error(
                "[get_task_costs] Error getting task costs for task_id=%s: %s",
                task_id, e,
                exc_info=True,
            )
            return TaskCostBreakdownResponse(total=0.0, entries=[])  # type: ignore[call-arg]

    # ========================================================================
    # HEALTH CHECK
    # ========================================================================

    @log_query_performance(operation="health_check", category="settings_retrieval")
    async def health_check(self, service: str = "cofounder") -> dict[str, Any]:
        """
        Check database health.

        Args:
            service: Service name

        Returns:
            Health status dict
        """
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT NOW()")

            # Report connection pool utilization alongside connectivity result
            pool_size = self.pool.get_size()
            pool_idle = self.pool.get_idle_size()
            pool_used = pool_size - pool_idle
            pool_utilization = pool_used / pool_size if pool_size > 0 else 0.0

            if pool_utilization > 0.8:
                logger.warning(
                    "[db_pool] Connection pool near exhaustion: used=%d total=%d utilization=%.1f%%",
                    pool_used, pool_size, pool_utilization * 100,
                )

            return {
                "status": "healthy",
                "service": service,
                "database": "postgresql",
                "timestamp": result.isoformat() if result else None,
                "pool": {
                    "size": pool_size,
                    "used": pool_used,
                    "idle": pool_idle,
                    "utilization": round(pool_utilization, 3),
                },
            }
        except Exception as e:
            logger.error(
                "[health_check] Health check failed for service=%s: %s",
                service, e,
                exc_info=True,
            )
            return {
                "status": "unhealthy",
                "service": service,
                "error": str(e),
            }

    # ========================================================================
    # SETTINGS MANAGEMENT
    # ========================================================================

    # NOTE 2026-04-12: These methods used to query the `settings` table, which
    # is an abandoned schema with 4 stale rows. The live configuration lives in
    # `app_settings` (237+ rows, actively written by MCP, `site_config`, the
    # worker, and migration 023 / 024). Migrating here so GET /api/settings
    # and PUT /api/settings/{key} return the real data. Tracked during
    # Gitea #192 slice work on 2026-04-12. The old `settings` table should
    # be dropped in a follow-up migration once everything is verified.
    #
    # The `app_settings` schema is simpler than the old `settings` schema:
    # it lacks `display_name`, `is_active`, `modified_at`, `updated_by`.
    # `display_name` is dropped (the key itself is the display name).
    # `is_active` is dropped (every row is always active — delete == hard
    # delete now).
    _APP_SETTINGS_COLUMNS = (
        "id, key, value, category, description, is_secret, is_active, "
        "created_at, updated_at"
    )

    @log_query_performance(operation="get_setting", category="settings_retrieval")
    async def get_setting(
        self, key: str, include_inactive: bool = False
    ) -> SettingResponse | None:
        """Get a setting by key from `app_settings` (60s in-memory TTL cache).

        By default, only active rows (`is_active = true`) are returned —
        soft-deleted rows are hidden. Pass `include_inactive=True` for admin
        or debug workflows that need to see disabled keys.
        """
        cache_key = f"{key}|{include_inactive}"
        cached = self._settings_cache.get(cache_key)
        if cached and (time.monotonic() - cached["ts"]) < self._SETTINGS_CACHE_TTL:
            return cached["value"]

        where = "key = $1" if include_inactive else "key = $1 AND is_active = true"
        sql = f"SELECT {self._APP_SETTINGS_COLUMNS} FROM app_settings WHERE {where}"

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, key)
                result = ModelConverter.to_setting_response(row) if row else None
                self._settings_cache[cache_key] = {"value": result, "ts": time.monotonic()}
                return result
        except Exception as e:
            logger.error(
                "[get_setting] Failed to get setting key=%s: %s",
                key, e,
                exc_info=True,
            )
            return None

    @log_query_performance(operation="get_all_settings", category="settings_retrieval")
    async def get_all_settings(
        self, category: str | None = None, include_inactive: bool = False
    ) -> list[SettingResponse]:
        """Get all settings from `app_settings`, optionally filtered by category.

        By default, only active rows are returned. Pass `include_inactive=True`
        to surface soft-deleted rows too (useful for admin/debug).
        """
        cache_key = f"{category or '__all__'}|{include_inactive}"
        cached = self._all_settings_cache.get(cache_key)
        if cached and (time.monotonic() - cached["ts"]) < self._SETTINGS_CACHE_TTL:
            return cached["value"]

        where_clauses: list[str] = []
        params: list[Any] = []
        if category:
            where_clauses.append(f"category = ${len(params) + 1}")
            params.append(category)
        if not include_inactive:
            where_clauses.append("is_active = true")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = (
            f"SELECT {self._APP_SETTINGS_COLUMNS} FROM app_settings "
            f"{where_sql} ORDER BY key"
        )

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                result = [ModelConverter.to_setting_response(row) for row in rows]
                self._all_settings_cache[cache_key] = {"value": result, "ts": time.monotonic()}
                return result
        except Exception as e:
            logger.error(
                "[get_all_settings] Failed to get settings for category=%s: %s",
                category, e,
                exc_info=True,
            )
            return []

    @log_query_performance(operation="set_setting", category="settings_write")
    async def set_setting(
        self,
        key: str,
        value: Any,
        category: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
    ) -> bool:
        """Create or update a setting in `app_settings`.

        Upserts always set `is_active = true` — writing a value is an
        implicit "bring this setting back" operation. To disable a key
        without losing the value, call `set_setting_active(key, False)`.

        `display_name` is accepted for back-compat but ignored.
        """
        del display_name
        try:
            value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)

            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO app_settings (key, value, category, description, is_active)
                    VALUES ($1, $2, COALESCE($3, 'general'), COALESCE($4, ''), true)
                    ON CONFLICT (key) DO UPDATE SET
                        value       = EXCLUDED.value,
                        category    = COALESCE(EXCLUDED.category, app_settings.category),
                        description = COALESCE(EXCLUDED.description, app_settings.description),
                        is_active   = true,
                        updated_at  = NOW()
                    """,
                    key,
                    value_str,
                    category,
                    description,
                )
                self._invalidate_settings_cache()
                logger.info("Setting saved: %s = %s", key, value_str[:50])
                return True
        except Exception as e:
            logger.error(
                "[set_setting] Failed to set setting key=%s: %s",
                key, e,
                exc_info=True,
            )
            return False

    @log_query_performance(operation="set_setting_active", category="settings_write")
    async def set_setting_active(self, key: str, active: bool) -> bool:
        """Toggle `is_active` without touching the value.

        Use this for fallback / A-B testing: flip the setting off to test
        default behavior, flip it back on to restore. Returns True if a
        row was found and updated, False if the key doesn't exist.
        """
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE app_settings SET is_active = $2, updated_at = NOW() WHERE key = $1",
                    key,
                    active,
                )
                affected = int(result.split()[-1]) if result else 0
                self._invalidate_settings_cache()
                logger.info(
                    "Setting %s %s (rows affected=%d)",
                    key,
                    "enabled" if active else "disabled",
                    affected,
                )
                return affected > 0
        except Exception as e:
            logger.error(
                "[set_setting_active] Failed for key=%s active=%s: %s",
                key, active, e,
                exc_info=True,
            )
            return False

    @log_query_performance(operation="delete_setting", category="settings_write")
    async def delete_setting(self, key: str, hard: bool = False) -> bool:
        """Delete a setting.

        Default is SOFT delete (sets `is_active = false`) — the row + value
        stays recoverable via `set_setting_active(key, True)`. Pass
        `hard=True` for tests and admin cleanup that needs the row
        permanently removed.
        """
        try:
            async with self.pool.acquire() as conn:
                if hard:
                    await conn.execute("DELETE FROM app_settings WHERE key = $1", key)
                    logger.info("Setting hard-deleted: %s", key)
                else:
                    await conn.execute(
                        "UPDATE app_settings SET is_active = false, updated_at = NOW() WHERE key = $1",
                        key,
                    )
                    logger.info("Setting soft-deleted: %s", key)
                self._invalidate_settings_cache()
                return True
        except Exception as e:
            logger.error(
                "[delete_setting] Failed to delete setting key=%s: %s",
                key, e,
                exc_info=True,
            )
            return False

    @log_query_performance(operation="get_setting_value", category="settings_retrieval")
    async def get_setting_value(self, key: str, default: Any = None) -> Any:
        """
        Get just the value of a setting, with optional default.

        Args:
            key: Setting key identifier
            default: Default value if not found

        Returns:
            Setting value or default
        """
        setting = await self.get_setting(key)
        if not setting:
            return default

        # Handle both Pydantic model and dict returns
        value_str = (
            setting.get("value") if isinstance(setting, dict) else getattr(setting, "value", None)
        )
        if not value_str:
            return default
        try:
            return json.loads(value_str)
        except (json.JSONDecodeError, ValueError, TypeError):
            return value_str

    @log_query_performance(operation="setting_exists", category="settings_retrieval")
    async def setting_exists(self, key: str) -> bool:
        """
        Check if a setting exists and is active.

        Args:
            key: Setting key identifier

        Returns:
            True if setting exists and is active
        """
        sql = "SELECT EXISTS(SELECT 1 FROM app_settings WHERE key = $1)"
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(sql, key)
                return result or False
        except Exception as e:
            logger.error(
                "[setting_exists] Failed to check setting key=%s: %s",
                key, e,
                exc_info=True,
            )
            return False

    # ================================================================
    # Logging Operations (delegated from DatabaseService)
    # ================================================================

    @log_query_performance(operation="add_log_entry", category="log_write")
    async def add_log_entry(
        self, agent_name: str, level: str, message: str, context: dict | None = None
    ) -> dict[str, Any]:
        """Add a log entry to the logs table."""
        sql = """
            INSERT INTO logs (id, agent_name, level, message, context, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            RETURNING id, agent_name, level, message, context, created_at
        """
        try:
            log_id = str(uuid4())
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    sql,
                    log_id,
                    agent_name,
                    level,
                    message,
                    json.dumps(context) if context else None,
                )
                return dict(row) if row else {"id": log_id}
        except Exception:
            logger.error("[add_log_entry] Failed to add log entry", exc_info=True)
            return {"id": str(uuid4()), "error": "Failed to save log entry"}

    @log_query_performance(operation="get_logs", category="log_retrieval")
    async def get_logs(
        self, agent_name: str | None = None, level: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retrieve log entries with optional filters."""
        conditions = []
        params: list = []
        idx = 1

        if agent_name:
            conditions.append(f"agent_name = ${idx}")
            params.append(agent_name)
            idx += 1
        if level:
            conditions.append(f"level = ${idx}")
            params.append(level)
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM logs {where_clause} ORDER BY created_at DESC LIMIT ${idx}"
        params.append(limit)

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                return [dict(r) for r in rows]
        except Exception:
            logger.error("[get_logs] Failed to retrieve logs", exc_info=True)
            return []

    # ================================================================
    # Financial Operations (delegated from DatabaseService)
    # ================================================================

    @log_query_performance(operation="add_financial_entry", category="financial_write")
    async def add_financial_entry(self, entry_data: dict[str, Any]) -> dict[str, Any]:
        """Add a financial entry."""
        sql = """
            INSERT INTO financial_entries (entry_type, amount, currency, description, category, date, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            RETURNING *
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    sql,
                    entry_data.get("entry_type", "expense"),
                    entry_data.get("amount", 0),
                    entry_data.get("currency", "USD"),
                    entry_data.get("description"),
                    entry_data.get("category"),
                    entry_data.get("date", datetime.now(timezone.utc).date()),
                    json.dumps(entry_data.get("metadata", {})),
                )
                return dict(row) if row else {}
        except Exception:
            logger.error("[add_financial_entry] Failed to add financial entry", exc_info=True)
            return {}

    @log_query_performance(operation="get_financial_summary", category="financial_retrieval")
    async def get_financial_summary(self, days: int = 30) -> dict[str, Any]:
        """Get financial summary for the specified period."""
        # Use parameterized query instead of string formatting to prevent SQL injection.
        # Even though `days` is typed as int, callers may pass unsanitized input.
        sql = """
            SELECT
                COALESCE(SUM(amount), 0) as total_amount,
                COUNT(*) as entry_count,
                COALESCE(SUM(CASE WHEN entry_type = 'revenue' THEN amount ELSE 0 END), 0) as total_revenue,
                COALESCE(SUM(CASE WHEN entry_type = 'expense' THEN amount ELSE 0 END), 0) as total_expenses
            FROM financial_entries
            WHERE created_at >= NOW() - make_interval(days => $1)
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, int(days))
                return dict(row) if row else {"total_amount": 0, "entry_count": 0}
        except Exception:
            logger.error("[get_financial_summary] Failed to get financial summary", exc_info=True)
            return {"total_amount": 0, "entry_count": 0, "total_revenue": 0, "total_expenses": 0}

    # ================================================================
    # Agent Status Operations (delegated from DatabaseService)
    # ================================================================

    @log_query_performance(operation="update_agent_status", category="agent_write")
    async def update_agent_status(
        self, agent_name: str, status: str, last_run=None, metadata: dict | None = None
    ) -> bool:
        """Update or insert agent status."""
        sql = """
            INSERT INTO agent_status (agent_name, status, last_heartbeat, metadata, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (agent_name) DO UPDATE SET
                status = EXCLUDED.status,
                last_heartbeat = EXCLUDED.last_heartbeat,
                metadata = COALESCE(EXCLUDED.metadata, agent_status.metadata),
                updated_at = NOW()
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    sql,
                    agent_name,
                    status,
                    last_run or datetime.now(timezone.utc),
                    json.dumps(metadata) if metadata else None,
                )
                return True
        except Exception:
            logger.error("[update_agent_status] Failed to update agent status", exc_info=True)
            return False

    async def get_agent_status(self, agent_name: str) -> dict[str, Any] | None:
        """Get current status of an agent."""
        sql = "SELECT * FROM agent_status WHERE agent_name = $1"
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, agent_name)
                return dict(row) if row else None
        except Exception:
            logger.error("[get_agent_status] Failed to get agent status", exc_info=True)
            return None
