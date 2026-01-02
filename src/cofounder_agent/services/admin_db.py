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
import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4
from datetime import datetime

from asyncpg import Pool

from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator
from schemas.database_response_models import (
    LogResponse, FinancialEntryResponse, FinancialSummaryResponse, CostLogResponse,
    TaskCostBreakdownResponse, AgentStatusResponse, SettingResponse
)
from schemas.model_converter import ModelConverter
from .database_mixin import DatabaseServiceMixin

logger = logging.getLogger(__name__)


class AdminDatabase(DatabaseServiceMixin):
    """Administrative database operations (logs, financial, settings, health)."""

    def __init__(self, pool: Pool):
        """
        Initialize admin database module.
        
        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool

    # ========================================================================
    # LOGGING
    # ========================================================================

    async def add_log_entry(
        self,
        agent_name: str,
        level: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Add log entry.
        
        Args:
            agent_name: Name of agent creating log
            level: Log level (DEBUG, INFO, WARNING, ERROR)
            message: Log message
            context: Optional context dict
            
        Returns:
            Log ID
        """
        log_id = str(uuid4())

        sql = "INSERT INTO logs (id, agent_name, level, message, context, created_at) VALUES ($1, $2, $3, $4, $5, NOW())"
        params = [log_id, agent_name, level, message, json.dumps(context or {})]
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(sql, *params)
            return log_id
        except Exception as e:
            logger.error(f"Error adding log entry: {str(e)}")
            raise

    async def get_logs(
        self,
        agent_name: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get logs with optional filtering.
        
        Args:
            agent_name: Filter by agent name
            level: Filter by log level
            limit: Maximum logs to return
            
        Returns:
            List of log dicts
        """
        try:
            builder = ParameterizedQueryBuilder()
            
            where_clauses = []
            if agent_name:
                where_clauses.append(("agent_name", SQLOperator.EQ, agent_name))
            if level:
                where_clauses.append(("level", SQLOperator.EQ, level))
            
            sql, params = builder.select(
                columns=["*"],
                table="logs",
                where_clauses=where_clauses if where_clauses else None,
                order_by=[("created_at", "DESC")],
                limit=limit
            )
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                return [dict(row) for row in rows]
        except Exception as e:
            if "logs" in str(e) or "does not exist" in str(e) or "relation" in str(e):
                return []
            logger.warning(f"Error fetching logs: {str(e)}")
            return []

    # ========================================================================
    # FINANCIAL TRACKING
    # ========================================================================

    async def add_financial_entry(self, entry_data: Dict[str, Any]) -> str:
        """
        Add financial entry.
        
        Args:
            entry_data: Dict with category, amount, description, tags
            
        Returns:
            Entry ID
        """
        entry_id = entry_data.get("id") or str(uuid4())

        sql = "INSERT INTO financial_entries (id, category, amount, description, tags, created_at) VALUES ($1, $2, $3, $4, $5, NOW())"
        params = [
            entry_id,
            entry_data.get("category"),
            entry_data.get("amount"),
            entry_data.get("description"),
            json.dumps(entry_data.get("tags", []))
        ]
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(sql, *params)
            return entry_id
        except Exception as e:
            logger.error(f"Error adding financial entry: {str(e)}")
            raise

    async def get_financial_summary(self, days: int = 30) -> FinancialSummaryResponse:
        """
        Get financial summary for last N days.
        
        Args:
            days: Number of days to look back
            
        Returns:
            Dict with total_entries, total_amount, avg_amount, min_amount, max_amount
        """
        cutoff_date = datetime.utcnow() - __import__('datetime').timedelta(days=days)

        try:
            sql = """
                SELECT
                    COUNT(*) as total_entries,
                    SUM(amount) as total_amount,
                    AVG(amount) as avg_amount,
                    MIN(amount) as min_amount,
                    MAX(amount) as max_amount
                FROM financial_entries
                WHERE created_at >= $1
            """
            params = [cutoff_date]
            
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                if row:
                    return FinancialSummaryResponse(**dict(row))
                return FinancialSummaryResponse()
        except Exception as e:
            logger.warning(f"Error fetching financial summary: {str(e)}")
            return FinancialSummaryResponse()

    # ========================================================================
    # COST LOGGING
    # ========================================================================

    async def log_cost(self, cost_log: Dict[str, Any]) -> CostLogResponse:
        """
        Log cost of LLM API call to cost_logs table.

        Args:
            cost_log: Dict with:
                - task_id (UUID or str)
                - user_id (UUID or str, optional)
                - phase (str): research, outline, draft, assess, refine, finalize
                - model (str): ollama, gpt-3.5-turbo, gpt-4, claude-3-opus, etc.
                - provider (str): ollama, openai, anthropic, google
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
                str(cost_log["task_id"]),
                str(cost_log["user_id"]) if cost_log.get("user_id") else None,
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
                    f"✅ Logged cost for {cost_log['phase']}: ${cost_log.get('cost_usd', 0):.6f} ({cost_log['model']})"
                )
                return ModelConverter.to_cost_log_response(row)
        except Exception as e:
            logger.error(f"❌ Error logging cost: {e}")
            raise

    async def get_task_costs(self, task_id: str) -> TaskCostBreakdownResponse:
        """
        Get cost breakdown for a task by phase.

        Args:
            task_id: Task ID

        Returns:
            {
                "research": {"cost": 0.0, "model": "ollama", "count": 1},
                "outline": {"cost": 0.00075, "model": "gpt-3.5-turbo", "count": 1},
                "draft": {"cost": 0.0015, "model": "gpt-4", "count": 1},
                "total": 0.00225,
                "entries": [...]
            }
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM cost_logs
                    WHERE task_id = $1
                    ORDER BY created_at ASC
                """,
                    str(task_id),
                )

                if not rows:
                    return TaskCostBreakdownResponse(total=0.0, entries=[])

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
                    f"✅ Retrieved costs for task {task_id}: ${total_cost:.6f} across {len(entries)} entries"
                )
                return TaskCostBreakdownResponse(**response_data)
        except Exception as e:
            logger.error(f"❌ Error getting task costs: {e}")
            return TaskCostBreakdownResponse(total=0.0, entries=[])

    # ========================================================================
    # AGENT STATUS
    # ========================================================================

    async def update_agent_status(
        self,
        agent_name: str,
        status: str,
        last_run: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update or create agent status.
        
        Args:
            agent_name: Agent name
            status: Status string
            last_run: Optional last run timestamp
            metadata: Optional metadata dict
            
        Returns:
            Updated agent status dict
        """
        last_run = last_run or datetime.utcnow()
        metadata_json = json.dumps(metadata or {})

        try:
            async with self.pool.acquire() as conn:
                # Try to update first
                sql_update = "UPDATE agent_status SET status = $2, last_run = $3, metadata = $4, updated_at = NOW() WHERE agent_name = $1 RETURNING *"
                params_update = [agent_name, status, last_run, metadata_json]
                
                row = await conn.fetchrow(sql_update, *params_update)

                # If no rows updated, insert new
                if not row:
                    sql_insert = "INSERT INTO agent_status (agent_name, status, last_run, metadata, created_at, updated_at) VALUES ($1, $2, $3, $4, NOW(), NOW()) RETURNING *"
                    params_insert = [agent_name, status, last_run, metadata_json]
                    row = await conn.fetchrow(sql_insert, *params_insert)

                return self._convert_row_to_dict(row)
        except Exception as e:
            logger.error(f"Error updating agent status: {str(e)}")
            raise

    async def get_agent_status(self, agent_name: str) -> Optional[AgentStatusResponse]:
        """
        Get agent status.
        
        Args:
            agent_name: Agent name
            
        Returns:
            Agent status dict or None
        """
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["*"],
            table="agent_status",
            where_clauses=[("agent_name", SQLOperator.EQ, agent_name)]
        )
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return ModelConverter.to_agent_status_response(row) if row else None

    # ========================================================================
    # HEALTH CHECK
    # ========================================================================

    async def health_check(self, service: str = "cofounder") -> Dict[str, Any]:
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

                return {
                    "status": "healthy",
                    "service": service,
                    "database": "postgresql",
                    "timestamp": result.isoformat() if result else None,
                }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "service": service,
                "error": str(e),
            }

    # ========================================================================
    # SETTINGS MANAGEMENT
    # ========================================================================

    async def get_setting(self, key: str) -> Optional[SettingResponse]:
        """
        Get a setting by key.

        Args:
            key: Setting key identifier

        Returns:
            Setting dict or None if not found
        """
        sql = "SELECT * FROM settings WHERE key = $1 AND is_active = true"

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, key)
                if row:
                    return ModelConverter.to_setting_response(row)
                return None
        except Exception as e:
            logger.error(f"❌ Failed to get setting {key}: {e}")
            return None

    async def get_all_settings(self, category: Optional[str] = None) -> List[SettingResponse]:
        """
        Get all active settings, optionally filtered by category.

        Args:
            category: Optional category filter

        Returns:
            List of setting dicts
        """
        if category:
            sql = "SELECT * FROM settings WHERE category = $1 AND is_active = true ORDER BY key"
            params = [category]
        else:
            sql = "SELECT * FROM settings WHERE is_active = true ORDER BY key"
            params = []

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                return [ModelConverter.to_setting_response(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get settings: {e}")
            return []

    async def set_setting(
        self,
        key: str,
        value: Any,
        category: Optional[str] = None,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """
        Create or update a setting.

        Args:
            key: Setting key identifier
            value: Setting value (will be stored as text)
            category: Optional category for grouping
            display_name: Optional display name for UI
            description: Optional description

        Returns:
            True if successful
        """
        try:
            value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)

            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO settings (key, value, category, display_name, description, is_active, modified_at)
                    VALUES ($1, $2, $3, $4, $5, true, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        value = $2,
                        category = $3,
                        display_name = $4,
                        description = $5,
                        modified_at = NOW()
                    """,
                    key,
                    value_str,
                    category,
                    display_name,
                    description,
                )
                logger.info(f"✅ Setting saved: {key} = {value_str[:50]}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to set setting {key}: {e}")
            return False

    async def delete_setting(self, key: str) -> bool:
        """
        Soft delete a setting (mark as inactive).

        Args:
            key: Setting key identifier

        Returns:
            True if successful
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE settings SET is_active = false, modified_at = NOW() WHERE key = $1", key
                )
                logger.info(f"✅ Setting deleted: {key}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to delete setting {key}: {e}")
            return False

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
        if not setting or not setting.get("value"):
            return default

        # Try to parse as JSON if it looks like JSON
        value_str = setting["value"]
        try:
            return json.loads(value_str)
        except:
            return value_str

    async def setting_exists(self, key: str) -> bool:
        """
        Check if a setting exists and is active.

        Args:
            key: Setting key identifier

        Returns:
            True if setting exists and is active
        """
        sql = "SELECT EXISTS(SELECT 1 FROM settings WHERE key = $1 AND is_active = true)"
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(sql, key)
                return result or False
        except Exception as e:
            logger.error(f"❌ Failed to check setting {key}: {e}")
            return False
