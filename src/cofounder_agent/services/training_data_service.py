"""
Training data management service for fine-tuning.

Provides filtering, tagging, exporting, and statistics for training examples.
Supports organizing data by quality, date, intent, and custom tags.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


class DataTag(str, Enum):
    """Tags for training data"""

    PRODUCTION = "production"
    DEVELOPMENT = "development"
    TEST = "test"
    LOW_QUALITY = "low_quality"
    MANUAL_APPROVED = "manual_approved"
    EXCLUDE = "exclude"


@dataclass
class TrainingDataStats:
    """Statistics about training data"""

    total_examples: int
    filtered_count: int
    by_tag: Dict[str, int]
    avg_quality_score: float
    quality_score_distribution: Dict[str, int]
    by_intent: Dict[str, int]
    success_rate: float
    date_range: Dict[str, Optional[str]]


@dataclass
class TrainingDatapoint:
    """Single training example"""

    id: str
    execution_id: str
    user_request: str
    intent: str
    business_state: Dict[str, Any]
    execution_plan: Dict[str, Any]
    execution_result: Dict[str, Any]
    quality_score: float
    success: bool
    tags: List[str]
    created_at: str
    post_publication_metrics: Optional[Dict[str, Any]] = None
    patterns_discovered: Optional[List[str]] = None


class TrainingDataService:
    """
    Manages training data for fine-tuning.

    Provides:
    - Filtering and querying training data
    - Tagging data (exclude bad data, mark production, etc.)
    - Exporting as JSONL for fine-tuning
    - Dataset versioning
    - Statistics and analytics
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool

    # ========================================================================
    # INITIALIZATION
    # ========================================================================

    async def initialize(self):
        """Create training data tables if not exist"""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orchestrator_training_data (
                    id SERIAL PRIMARY KEY,
                    execution_id VARCHAR(255) UNIQUE NOT NULL,
                    user_request TEXT NOT NULL,
                    intent VARCHAR(100),
                    
                    business_state JSONB DEFAULT '{}',
                    execution_plan JSONB DEFAULT '{}',
                    execution_result JSONB DEFAULT '{}',
                    quality_score DECIMAL(3,2) DEFAULT 0,
                    success BOOLEAN DEFAULT false,
                    
                    post_publication_metrics JSONB,
                    patterns_discovered JSONB,
                    
                    tags TEXT[] DEFAULT '{}',
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_training_data_quality 
                    ON orchestrator_training_data(quality_score DESC);
                CREATE INDEX IF NOT EXISTS idx_training_data_intent 
                    ON orchestrator_training_data(intent);
                CREATE INDEX IF NOT EXISTS idx_training_data_success 
                    ON orchestrator_training_data(success);
                CREATE INDEX IF NOT EXISTS idx_training_data_tags 
                    ON orchestrator_training_data USING GIN(tags);
                CREATE INDEX IF NOT EXISTS idx_training_data_created 
                    ON orchestrator_training_data(created_at DESC);
                
                CREATE TABLE IF NOT EXISTS training_datasets (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    version INT NOT NULL,
                    
                    filters JSONB NOT NULL,
                    example_count INT NOT NULL,
                    avg_quality DECIMAL(3,2),
                    
                    file_path VARCHAR(255),
                    file_size INT,
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    CONSTRAINT unique_dataset_version UNIQUE(name, version)
                );
                
                CREATE INDEX IF NOT EXISTS idx_datasets_created 
                    ON training_datasets(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_datasets_name 
                    ON training_datasets(name, version DESC);
            """
            )

            logger.info("Training data tables initialized")

    # ========================================================================
    # DATA RETRIEVAL & FILTERING
    # ========================================================================

    async def get_all_training_data(
        self, limit: int = 1000, offset: int = 0
    ) -> List[TrainingDatapoint]:
        """Get all training data with pagination"""
        query = """
            SELECT 
                id, execution_id, user_request, intent,
                business_state, execution_plan, execution_result,
                quality_score, success, tags,
                post_publication_metrics, patterns_discovered,
                created_at
            FROM orchestrator_training_data
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
        """

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, limit, offset)

        return [self._row_to_datapoint(row) for row in rows]

    async def filter_training_data(
        self,
        quality_min: float = 0.0,
        quality_max: float = 1.0,
        intent_filter: Optional[List[str]] = None,
        success_only: bool = False,
        exclude_tags: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
        date_after: Optional[str] = None,
        date_before: Optional[str] = None,
        limit: int = 1000,
    ) -> List[TrainingDatapoint]:
        """
        Filter training data by multiple criteria.

        Args:
            quality_min/max: Quality score range (0.0-1.0)
            intent_filter: List of intents to include
            success_only: Only successful executions
            exclude_tags: Exclude rows with any of these tags
            include_tags: Include only rows with all these tags
            date_after: Filter by created_at >= date (ISO format)
            date_before: Filter by created_at <= date (ISO format)
            limit: Max results

        Returns:
            Filtered training data
        """
        query = "SELECT * FROM orchestrator_training_data WHERE 1=1"
        params = []
        param_count = 1

        # Quality score range
        query += f" AND quality_score >= ${param_count}"
        params.append(quality_min)
        param_count += 1

        query += f" AND quality_score <= ${param_count}"
        params.append(quality_max)
        param_count += 1

        # Intent filter
        if intent_filter:
            placeholders = ", ".join([f"${param_count + i}" for i in range(len(intent_filter))])
            query += f" AND intent IN ({placeholders})"
            params.extend(intent_filter)
            param_count += len(intent_filter)

        # Success only
        if success_only:
            query += f" AND success = true"

        # Exclude tags
        if exclude_tags:
            placeholders = ", ".join([f"${param_count + i}" for i in range(len(exclude_tags))])
            query += f" AND NOT (tags && ARRAY[{placeholders}]::text[])"
            params.extend(exclude_tags)
            param_count += len(exclude_tags)

        # Include tags (must have all)
        if include_tags:
            placeholders = ", ".join([f"${param_count + i}" for i in range(len(include_tags))])
            query += f" AND tags @> ARRAY[{placeholders}]::text[]"
            params.extend(include_tags)
            param_count += len(include_tags)

        # Date range
        if date_after:
            query += f" AND created_at >= ${param_count}::timestamp"
            params.append(date_after)
            param_count += 1

        if date_before:
            query += f" AND created_at <= ${param_count}::timestamp"
            params.append(date_before)
            param_count += 1

        query += f" ORDER BY created_at DESC LIMIT ${param_count}"
        params.append(limit)

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._row_to_datapoint(row) for row in rows]

    # ========================================================================
    # TAGGING & MANAGEMENT
    # ========================================================================

    async def add_tags(self, execution_ids: List[str], tags: List[str]) -> int:
        """
        Add tags to training data.

        Args:
            execution_ids: Which data to tag
            tags: Tags to add

        Returns:
            Number of rows updated
        """
        if not execution_ids or not tags:
            return 0

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE orchestrator_training_data
                SET tags = array_cat(tags, $2::text[]),
                    updated_at = CURRENT_TIMESTAMP
                WHERE execution_id = ANY($1::text[])
                """,
                execution_ids,
                tags,
            )

        # Extract count from "UPDATE X"
        return int(result.split()[-1]) if result else 0

    async def remove_tags(self, execution_ids: List[str], tags: List[str]) -> int:
        """Remove tags from training data"""
        if not execution_ids or not tags:
            return 0

        async with self.db_pool.acquire() as conn:
            # Remove each tag individually
            count = 0
            for tag in tags:
                result = await conn.execute(
                    """
                    UPDATE orchestrator_training_data
                    SET tags = array_remove(tags, $2::text),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE execution_id = ANY($1::text[])
                    """,
                    execution_ids,
                    tag,
                )
                count += int(result.split()[-1]) if result else 0

        return count

    async def tag_by_date_range(self, date_after: str, date_before: str, tags: List[str]) -> int:
        """
        Tag all data within a date range.

        Useful for: "Tag all dev data from before 2025-12-01 as 'development'"
        """
        if not tags:
            return 0

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE orchestrator_training_data
                SET tags = array_cat(tags, $3::text[]),
                    updated_at = CURRENT_TIMESTAMP
                WHERE created_at >= $1::timestamp
                  AND created_at <= $2::timestamp
                """,
                date_after,
                date_before,
                tags,
            )

        return int(result.split()[-1]) if result else 0

    async def tag_by_quality(self, quality_max: float, tags: List[str]) -> int:
        """
        Tag low-quality data.

        Useful for: "Tag all data with quality < 0.7 as 'low_quality'"
        """
        if not tags:
            return 0

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE orchestrator_training_data
                SET tags = array_cat(tags, $2::text[]),
                    updated_at = CURRENT_TIMESTAMP
                WHERE quality_score < $1
                """,
                quality_max,
                tags,
            )

        return int(result.split()[-1]) if result else 0

    # ========================================================================
    # STATISTICS
    # ========================================================================

    async def get_statistics(self, filters: Optional[Dict[str, Any]] = None) -> TrainingDataStats:
        """
        Get statistics about training data.

        Returns: count, quality distribution, intent breakdown, etc.
        """
        # Get total count
        async with self.db_pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM orchestrator_training_data")

        # Get filtered data
        if filters:
            filtered_data = await self.filter_training_data(**filters)
        else:
            filtered_data = await self.get_all_training_data(limit=10000)

        if not filtered_data:
            return TrainingDataStats(
                total_examples=total,
                filtered_count=0,
                by_tag={},
                avg_quality_score=0.0,
                quality_score_distribution={},
                by_intent={},
                success_rate=0.0,
                date_range={"earliest": None, "latest": None},
            )

        quality_scores = [d.quality_score for d in filtered_data if d.quality_score]
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0

        by_tag = {}
        by_intent = {}
        quality_distribution = {
            "0.0-0.2": 0,
            "0.2-0.4": 0,
            "0.4-0.6": 0,
            "0.6-0.8": 0,
            "0.8-1.0": 0,
        }
        success_count = 0

        for datapoint in filtered_data:
            success_count += 1 if datapoint.success else 0

            # Tally tags
            for tag in datapoint.tags:
                by_tag[tag] = by_tag.get(tag, 0) + 1

            # Tally intents
            if datapoint.intent:
                by_intent[datapoint.intent] = by_intent.get(datapoint.intent, 0) + 1

            # Distribution
            if datapoint.quality_score:
                q = datapoint.quality_score
                if q < 0.2:
                    quality_distribution["0.0-0.2"] += 1
                elif q < 0.4:
                    quality_distribution["0.2-0.4"] += 1
                elif q < 0.6:
                    quality_distribution["0.4-0.6"] += 1
                elif q < 0.8:
                    quality_distribution["0.6-0.8"] += 1
                else:
                    quality_distribution["0.8-1.0"] += 1

        dates = [d.created_at for d in filtered_data if d.created_at]
        date_range = {
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None,
        }

        return TrainingDataStats(
            total_examples=total,
            filtered_count=len(filtered_data),
            by_tag=by_tag,
            avg_quality_score=avg_quality,
            quality_score_distribution=quality_distribution,
            by_intent=by_intent,
            success_rate=success_count / len(filtered_data) if filtered_data else 0.0,
            date_range=date_range,
        )

    # ========================================================================
    # EXPORT
    # ========================================================================

    async def export_as_jsonl(
        self, filters: Optional[Dict[str, Any]] = None, output_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Export training data as JSONL for fine-tuning.

        Returns: statistics about export
        """
        if output_path is None:
            output_path = f"/tmp/training_data_{datetime.now().timestamp()}.jsonl"

        # Get filtered data
        if filters:
            data = await self.filter_training_data(**filters)
        else:
            data = await self.get_all_training_data(limit=10000)

        # Write JSONL
        with open(output_path, "w") as f:
            for example in data:
                # Prepare for fine-tuning (format works with most providers)
                training_obj = {
                    "messages": [
                        {
                            "role": "system",
                            "content": f"You are an intelligent business orchestrator. Task: {example.intent}",
                        },
                        {"role": "user", "content": example.user_request},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "plan": example.execution_plan,
                                    "expected_quality": example.quality_score,
                                    "business_context": example.business_state,
                                }
                            ),
                        },
                    ],
                    "metadata": {
                        "success": example.success,
                        "quality_score": example.quality_score,
                        "intent": example.intent,
                        "metrics": example.post_publication_metrics,
                        "patterns": example.patterns_discovered,
                    },
                }
                f.write(json.dumps(training_obj) + "\n")

        file_size = os.path.getsize(output_path)

        return {
            "success": True,
            "file_path": output_path,
            "file_size": file_size,
            "example_count": len(data),
            "avg_quality": (
                avg_quality
                if (avg_quality := sum(e.quality_score for e in data) / len(data) if data else 0)
                else 0
            ),
        }

    # ========================================================================
    # DATASETS
    # ========================================================================

    async def create_dataset(
        self, name: str, description: str, filters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a versioned dataset for fine-tuning.

        Args:
            name: Dataset name (e.g., "production", "high-quality")
            description: What this dataset is for
            filters: Filters used to select data

        Returns:
            Dataset metadata
        """
        # Get filtered data
        filtered_data = await self.filter_training_data(**filters)

        # Export to JSONL
        export_result = await self.export_as_jsonl(
            filters=filters, output_path=f"/tmp/dataset_{name}_{datetime.now().timestamp()}.jsonl"
        )

        # Get version number
        async with self.db_pool.acquire() as conn:
            version = await conn.fetchval(
                "SELECT MAX(version) FROM training_datasets WHERE name = $1", name
            )
            version = (version or 0) + 1

            # Store dataset metadata
            await conn.execute(
                """
                INSERT INTO training_datasets 
                (name, description, version, filters, example_count, avg_quality, file_path, file_size)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                name,
                description,
                version,
                json.dumps(filters),
                export_result["example_count"],
                export_result["avg_quality"],
                export_result["file_path"],
                export_result["file_size"],
            )

        return {
            "name": name,
            "version": version,
            "example_count": export_result["example_count"],
            "avg_quality": export_result["avg_quality"],
            "file_path": export_result["file_path"],
            "file_size": export_result["file_size"],
        }

    async def list_datasets(self) -> List[Dict[str, Any]]:
        """List all versioned datasets"""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM training_datasets ORDER BY name, version DESC")

        return [dict(row) for row in rows]

    async def get_dataset(self, dataset_id: int) -> Optional[Dict[str, Any]]:
        """Get specific dataset by ID"""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM training_datasets WHERE id = $1", dataset_id)

        return dict(row) if row else None

    # ========================================================================
    # TRAINING DATA INSERTION
    # ========================================================================

    async def save_training_example(
        self,
        execution_id: str,
        user_request: str,
        intent: str,
        business_state: Dict[str, Any],
        execution_plan: Dict[str, Any],
        execution_result: Dict[str, Any],
        quality_score: float,
        success: bool,
        tags: Optional[List[str]] = None,
        post_publication_metrics: Optional[Dict[str, Any]] = None,
        patterns_discovered: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Save a training example to database"""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO orchestrator_training_data
                (execution_id, user_request, intent, business_state, execution_plan,
                 execution_result, quality_score, success, tags, post_publication_metrics,
                 patterns_discovered)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (execution_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP,
                    business_state = EXCLUDED.business_state,
                    execution_result = EXCLUDED.execution_result,
                    quality_score = EXCLUDED.quality_score,
                    success = EXCLUDED.success,
                    tags = EXCLUDED.tags,
                    post_publication_metrics = EXCLUDED.post_publication_metrics,
                    patterns_discovered = EXCLUDED.patterns_discovered
                RETURNING id, execution_id, created_at
                """,
                execution_id,
                user_request,
                intent,
                json.dumps(business_state) if business_state else {},
                json.dumps(execution_plan) if execution_plan else {},
                json.dumps(execution_result) if execution_result else {},
                quality_score,
                success,
                tags or [],
                json.dumps(post_publication_metrics) if post_publication_metrics else None,
                json.dumps(patterns_discovered) if patterns_discovered else None,
            )

        return {
            "id": row["id"],
            "execution_id": row["execution_id"],
            "created_at": row["created_at"].isoformat(),
        }

    # ========================================================================
    # HELPERS
    # ========================================================================

    def _row_to_datapoint(self, row: asyncpg.Record) -> TrainingDatapoint:
        """Convert database row to TrainingDatapoint"""
        return TrainingDatapoint(
            id=str(row["id"]),
            execution_id=row["execution_id"],
            user_request=row["user_request"],
            intent=row["intent"],
            business_state=row["business_state"] or {},
            execution_plan=row["execution_plan"] or {},
            execution_result=row["execution_result"] or {},
            quality_score=float(row["quality_score"] or 0),
            success=row["success"],
            tags=row["tags"] or [],
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
            post_publication_metrics=row.get("post_publication_metrics"),
            patterns_discovered=row.get("patterns_discovered"),
        )
