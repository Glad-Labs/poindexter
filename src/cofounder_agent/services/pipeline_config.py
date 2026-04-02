"""
Pipeline Configuration — DB-driven stage management.

Reads pipeline_stages and pipeline_experiments from the database.
Every gate is a toggle. Every setting is editable at runtime.

Usage:
    from services.pipeline_config import PipelineConfig
    config = PipelineConfig(pool)
    stages = await config.get_active_stages()
    stage_config = await config.get_stage_config("cross_model_qa")
    experiment = await config.get_experiment_for_stage("cross_model_qa")
"""

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineStage:
    """A single pipeline stage with its configuration."""
    key: str
    name: str
    description: str
    order: int
    enabled: bool
    config: Dict[str, Any] = field(default_factory=dict)
    ab_test_group: Optional[str] = None


@dataclass
class Experiment:
    """An A/B test on a pipeline stage."""
    id: int
    name: str
    stage_key: str
    variant_a: Dict[str, Any]
    variant_b: Dict[str, Any]
    traffic_split_pct: int  # % of traffic to variant B
    is_active: bool


class PipelineConfig:
    """Database-driven pipeline configuration.

    Reads stages, experiments, and run logs from the DB.
    Caches stages for the lifetime of a single pipeline run.
    """

    def __init__(self, pool):
        self.pool = pool
        self._stage_cache: Optional[List[PipelineStage]] = None

    async def get_active_stages(self) -> List[PipelineStage]:
        """Get all enabled pipeline stages in execution order."""
        if self._stage_cache is not None:
            return self._stage_cache

        rows = await self.pool.fetch("""
            SELECT key, name, description, stage_order, enabled, config_json, ab_test_group
            FROM pipeline_stages
            WHERE enabled = true
            ORDER BY stage_order
        """)

        stages = []
        for row in rows:
            config = row["config_json"] if isinstance(row["config_json"], dict) else json.loads(row["config_json"] or "{}")
            stages.append(PipelineStage(
                key=row["key"],
                name=row["name"],
                description=row["description"] or "",
                order=row["stage_order"],
                enabled=row["enabled"],
                config=config,
                ab_test_group=row["ab_test_group"],
            ))

        self._stage_cache = stages
        return stages

    async def get_stage_config(self, stage_key: str) -> Dict[str, Any]:
        """Get the config for a specific stage, including experiment overrides."""
        stages = await self.get_active_stages()
        stage = next((s for s in stages if s.key == stage_key), None)
        if stage is None:
            return {}

        config = dict(stage.config)

        # Check for active experiment on this stage
        experiment = await self.get_experiment_for_stage(stage_key)
        if experiment:
            # Roll the dice: variant A or B?
            use_b = random.randint(1, 100) <= experiment.traffic_split_pct
            variant = experiment.variant_b if use_b else experiment.variant_a
            variant_label = "B" if use_b else "A"
            config.update(variant)
            config["_experiment_id"] = experiment.id
            config["_experiment_variant"] = variant_label
            logger.info(
                "[PIPELINE_CONFIG] Stage '%s' using experiment '%s' variant %s",
                stage_key, experiment.name, variant_label,
            )

        return config

    async def is_stage_enabled(self, stage_key: str) -> bool:
        """Check if a specific stage is enabled."""
        stages = await self.get_active_stages()
        return any(s.key == stage_key for s in stages)

    async def get_experiment_for_stage(self, stage_key: str) -> Optional[Experiment]:
        """Get the active experiment for a stage, if any."""
        row = await self.pool.fetchrow("""
            SELECT id, name, stage_key, variant_a, variant_b, traffic_split_pct, is_active
            FROM pipeline_experiments
            WHERE stage_key = $1 AND is_active = true
            LIMIT 1
        """, stage_key)

        if not row:
            return None

        return Experiment(
            id=row["id"],
            name=row["name"],
            stage_key=row["stage_key"],
            variant_a=row["variant_a"] if isinstance(row["variant_a"], dict) else json.loads(row["variant_a"] or "{}"),
            variant_b=row["variant_b"] if isinstance(row["variant_b"], dict) else json.loads(row["variant_b"] or "{}"),
            traffic_split_pct=row["traffic_split_pct"],
            is_active=row["is_active"],
        )

    async def log_stage_run(
        self,
        task_id: str,
        stage_key: str,
        result: str,
        score: Optional[float] = None,
        duration_ms: Optional[int] = None,
        experiment_id: Optional[int] = None,
        experiment_variant: Optional[str] = None,
        details: Optional[Dict] = None,
    ):
        """Log a pipeline stage execution for observability."""
        try:
            await self.pool.execute("""
                INSERT INTO pipeline_run_log
                    (task_id, stage_key, result, score, duration_ms,
                     experiment_id, experiment_variant, details, finished_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            """,
                task_id, stage_key, result, score, duration_ms,
                experiment_id, experiment_variant,
                json.dumps(details) if details else "{}",
            )
        except Exception as e:
            logger.warning("[PIPELINE_CONFIG] Failed to log stage run: %s", e)

    async def update_experiment_results(self, experiment_id: int, variant: str, score: float, passed: bool):
        """Update experiment results for a variant."""
        col = "results_a" if variant == "A" else "results_b"
        try:
            await self.pool.execute(f"""
                UPDATE pipeline_experiments SET
                    {col} = jsonb_set(
                        jsonb_set(
                            jsonb_set({col}, '{{runs}}', to_jsonb(({col}->>'runs')::int + 1)),
                            '{{passes}}', to_jsonb(({col}->>'passes')::int + $2::int)
                        ),
                        '{{avg_score}}', to_jsonb(
                            round((({col}->>'avg_score')::float * ({col}->>'runs')::int + $3) / (({col}->>'runs')::int + 1), 1)
                        )
                    ),
                    updated_at = NOW()
                WHERE id = $1
            """, experiment_id, 1 if passed else 0, score)
        except Exception as e:
            logger.warning("[PIPELINE_CONFIG] Failed to update experiment results: %s", e)

    def invalidate_cache(self):
        """Clear the stage cache (call when stages are modified)."""
        self._stage_cache = None
