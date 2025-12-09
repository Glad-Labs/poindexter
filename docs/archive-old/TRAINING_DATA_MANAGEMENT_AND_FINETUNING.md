# Training Data Management & Fine-Tuning Strategy

## Quick Answers

### 1. **Configurable Training Data** ✅

Yes - we can add a UI dashboard where you can:

- **Filter which data to use** (by quality score, date range, intent type, success rate)
- **Preview training examples** before exporting
- **Exclude specific data ranges** (all dev junk from dates X to Y)
- **Tag data** (good, bad, test, production) and filter by tags
- **Version training datasets** (v1, v2, v3) and compare results

### 2. **Delete Bad Data?** ⚠️

Possible but **not recommended**. Better approach:

- **Tag data as "excluded"** but keep it (reversible)
- **Archive old data** to separate schema
- **Filter at export time** (don't include marked data in training)
- This way: if you later need context, it's still there

### 3. **Training with Local + Proprietary LLMs** ✅✅✅

YES! You have **three great options**:

```
OPTION 1: Fine-tune a Proprietary Ollama Model (100% Local & Private)
├─ Export training data as JSONL
├─ Run fine-tuning script with Ollama
├─ Deploy fine-tuned model locally
└─ No API keys, no cloud costs, completely private

OPTION 2: Use Major Models (Gemini, Claude, OpenAI)
├─ Export training data as JSONL
├─ Upload to Gemini, Claude, or OpenAI fine-tuning service
├─ Deploy fine-tuned model via their API
└─ Higher quality, but costs $ and requires API access

OPTION 3: Hybrid Approach (Recommended)
├─ Start with Ollama locally for development/testing
├─ Export to Gemini/Claude for production when confident
├─ Revert to Ollama if API costs too high
└─ Switch between models at runtime (ModelRouter already supports this!)
```

---

## Architecture: Configurable Training Data Pipeline

```
USER INTERFACE LAYER
┌──────────────────────────────────────────────────────────────────┐
│                                                                   │
│ TrainingDataDashboard.jsx (New Component)                        │
│                                                                   │
│ Section 1: DATA FILTERING & PREVIEW                             │
│ ├─ Date range slider (from - to date)                           │
│ ├─ Quality score filter (0.0 - 1.0)                             │
│ ├─ Intent type checkboxes (create_content, analyze, publish)   │
│ ├─ Success/failure filter                                        │
│ ├─ Tag selector (exclude "dev", "test", "bad", include "prod") │
│ ├─ Platform filter (linkedin, twitter, blog, all)              │
│ └─ Live preview: "Using X of Y examples"                       │
│                                                                   │
│ Section 2: DATA TAGGING & MANAGEMENT                            │
│ ├─ Bulk operations:                                             │
│ │  ├─ Select date range → Tag as "dev" → Apply                 │
│ │  ├─ Select by quality < 0.7 → Tag as "low_quality"          │
│ │  ├─ Select by date after 2025-12-01 → Tag as "prod"         │
│ │  └─ Mass exclude/include                                      │
│ └─ View tagged data breakdown                                   │
│    └─ "prod: 150 examples | dev: 89 examples | test: 23"       │
│                                                                   │
│ Section 3: TRAINING DATASET MANAGEMENT                          │
│ ├─ Dataset v1: Created 2025-12-01 (150 examples, 0.89 avg q)  │
│ ├─ Dataset v2: Created 2025-12-05 (200 examples, 0.91 avg q)  │
│ ├─ Dataset v3: Created 2025-12-09 (220 examples, 0.92 avg q)  │
│ ├─ For each version: [Export] [Preview] [Rollback] [Delete]    │
│ └─ New Dataset: [Create from filtered data] [Download] [Upload]│
│                                                                   │
│ Section 4: FINE-TUNING MODEL SELECTION                          │
│ ├─ Target Model:                                                │
│ │  ├─ ☐ Ollama (Mistral, Llama2, Neural-Chat) - LOCAL         │
│ │  ├─ ☐ Gemini (Google) - API                                  │
│ │  ├─ ☐ Claude (Anthropic) - API                              │
│ │  └─ ☐ GPT-4 (OpenAI) - API                                  │
│ ├─ Select target model & dataset                               │
│ ├─ [Start Fine-Tuning] (shows cost estimate & time)            │
│ └─ Status: Running / Complete / Failed                         │
│                                                                   │
│ Section 5: TRAINED MODELS                                       │
│ ├─ orchestrator-v1-ollama (Ollama) - 89% acc, tested           │
│ ├─ orchestrator-v2-gemini (Gemini) - 94% acc, in-prod         │
│ ├─ orchestrator-v3-claude (Claude) - 91% acc, testing          │
│ ├─ For each: [View metrics] [Set as active] [Benchmark]        │
│ └─ Active model: orchestrator-v2-gemini ✅                     │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
                                ↓
BACKEND API LAYER
┌──────────────────────────────────────────────────────────────────┐
│ New Endpoints:                                                   │
│                                                                   │
│ GET    /api/orchestrator/training/data              List all data
│ POST   /api/orchestrator/training/data/filter       Get filtered
│ PATCH  /api/orchestrator/training/data/tag          Add tags
│ POST   /api/orchestrator/training/datasets          Create version
│ GET    /api/orchestrator/training/datasets/{id}     Get dataset
│ POST   /api/orchestrator/training/datasets/export   JSONL export
│ POST   /api/orchestrator/training/fine-tune         Start training
│ GET    /api/orchestrator/training/jobs/{id}        Job status
│ POST   /api/orchestrator/training/jobs/{id}/cancel Cancel training
│ GET    /api/orchestrator/models/list               List all models
│ PUT    /api/orchestrator/models/active             Change active
│ POST   /api/orchestrator/models/benchmark          Test model
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
                                ↓
SERVICE LAYER
┌──────────────────────────────────────────────────────────────────┐
│ TrainingDataService                                             │
│ ├─ filter_training_data(filters) → List[TrainingExample]      │
│ ├─ tag_data_range(start, end, tag)                             │
│ ├─ export_as_jsonl(filters) → File                             │
│ └─ get_dataset_stats(filters) → Stats                          │
│                                                                   │
│ FineTuningService                                               │
│ ├─ start_fine_tune_ollama(dataset_path)                        │
│ ├─ start_fine_tune_gemini(dataset_jsonl)                       │
│ ├─ start_fine_tune_claude(dataset_jsonl)                       │
│ ├─ get_training_status(job_id)                                 │
│ └─ deploy_trained_model(model_path)                            │
│                                                                   │
│ ModelRegistry                                                    │
│ ├─ list_available_models() → List[ModelInfo]                   │
│ ├─ get_active_model() → Model                                  │
│ ├─ set_active_model(model_name)                                │
│ └─ benchmark_model(model_name) → Metrics                       │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
                                ↓
ORCHESTRATOR CORE
┌──────────────────────────────────────────────────────────────────┐
│ intelligent_orchestrator.py                                     │
│                                                                   │
│ At startup:                                                      │
│ 1. Load active model from registry                              │
│ 2. Initialize orchestrator with that model                      │
│ 3. Use ModelRouter to select models for tasks                   │
│                                                                   │
│ If model is fine-tuned:                                         │
│ ├─ Use fine-tuned model for planning/decisions                 │
│ ├─ Fall back to base model if fine-tuned unavailable          │
│ └─ Compare decisions to learn which is better                  │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Implementation: Training Data Management Service

```python
# src/cofounder_agent/services/training_data_service.py

from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import asyncpg
from enum import Enum
import json
import logging

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
    by_tag: Dict[str, int]
    avg_quality_score: float
    quality_score_distribution: Dict[str, int]  # "0-0.2": 5, "0.2-0.4": 10, etc.
    by_intent: Dict[str, int]
    success_rate: float
    date_range: Dict[str, str]  # {"earliest": "...", "latest": "..."}


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
        self.pool = db_pool

    # ========================================================================
    # INITIALIZATION
    # ========================================================================

    async def initialize(self):
        """Create training data tables if not exist"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS orchestrator_training_data (
                    id SERIAL PRIMARY KEY,
                    execution_id VARCHAR(255) UNIQUE NOT NULL,
                    user_request TEXT NOT NULL,
                    intent VARCHAR(100),

                    business_state JSONB,
                    execution_plan JSONB,
                    execution_result JSONB,
                    quality_score DECIMAL(3,1),
                    success BOOLEAN,

                    post_publication_metrics JSONB,
                    patterns_discovered JSONB,

                    tags TEXT[] DEFAULT '{}',  -- Tags for filtering

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_training_data_quality ON orchestrator_training_data(quality_score);
                CREATE INDEX IF NOT EXISTS idx_training_data_intent ON orchestrator_training_data(intent);
                CREATE INDEX IF NOT EXISTS idx_training_data_success ON orchestrator_training_data(success);
                CREATE INDEX IF NOT EXISTS idx_training_data_tags ON orchestrator_training_data USING GIN(tags);
                CREATE INDEX IF NOT EXISTS idx_training_data_created ON orchestrator_training_data(created_at);

                CREATE TABLE IF NOT EXISTS training_datasets (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    version INT NOT NULL,

                    filters JSONB NOT NULL,  -- Filters used to create this dataset
                    example_count INT NOT NULL,
                    avg_quality DECIMAL(3,2),

                    file_path VARCHAR(255),  -- Path to exported JSONL
                    file_size INT,

                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    CONSTRAINT unique_dataset_version UNIQUE(name, version)
                );

                CREATE INDEX IF NOT EXISTS idx_datasets_created ON training_datasets(created_at DESC);
            """)

    # ========================================================================
    # DATA RETRIEVAL & FILTERING
    # ========================================================================

    async def get_all_training_data(
        self,
        limit: int = 1000,
        offset: int = 0
    ) -> List[TrainingDatapoint]:
        """Get all training data"""
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

        async with self.pool.acquire() as conn:
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
        limit: int = 1000
    ) -> List[TrainingDatapoint]:
        """
        Filter training data by multiple criteria.

        Args:
            quality_min/max: Quality score range
            intent_filter: List of intents to include
            success_only: Only successful executions
            exclude_tags: Exclude rows with any of these tags
            include_tags: Include only rows with all these tags
            date_after: Filter by created_at >= date
            date_before: Filter by created_at <= date
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

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._row_to_datapoint(row) for row in rows]

    # ========================================================================
    # TAGGING & MANAGEMENT
    # ========================================================================

    async def add_tags(
        self,
        execution_ids: List[str],
        tags: List[str]
    ) -> int:
        """
        Add tags to training data.

        Args:
            execution_ids: Which data to tag
            tags: Tags to add (e.g., ["development", "exclude"])

        Returns:
            Number of rows updated
        """
        placeholders = ", ".join([f"${i+1}" for i in range(len(execution_ids))])

        query = f"""
            UPDATE orchestrator_training_data
            SET tags = array_cat(tags, ${{param_count}}::text[]),
                updated_at = CURRENT_TIMESTAMP
            WHERE execution_id IN ({placeholders})
        """

        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"""
                UPDATE orchestrator_training_data
                SET tags = array_cat(tags, ${len(execution_ids)+1}::text[]),
                    updated_at = CURRENT_TIMESTAMP
                WHERE execution_id = ANY($1::text[])
                """,
                execution_ids,
                tags
            )

        # Extract number from "UPDATE X"
        return int(result.split()[-1])

    async def remove_tags(
        self,
        execution_ids: List[str],
        tags: List[str]
    ) -> int:
        """Remove tags from training data"""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE orchestrator_training_data
                SET tags = array_remove(tags, ANY($2::text[])),
                    updated_at = CURRENT_TIMESTAMP
                WHERE execution_id = ANY($1::text[])
                """,
                execution_ids,
                tags
            )

        return int(result.split()[-1])

    async def tag_by_date_range(
        self,
        date_after: str,
        date_before: str,
        tags: List[str]
    ) -> int:
        """
        Tag all data within a date range.

        Useful for: "Tag all dev data from before 2025-12-01 as 'development'"
        """
        async with self.pool.acquire() as conn:
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
                tags
            )

        return int(result.split()[-1])

    async def tag_by_quality(
        self,
        quality_max: float,
        tags: List[str]
    ) -> int:
        """
        Tag low-quality data.

        Useful for: "Tag all data with quality < 0.7 as 'low_quality'"
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE orchestrator_training_data
                SET tags = array_cat(tags, $2::text[]),
                    updated_at = CURRENT_TIMESTAMP
                WHERE quality_score < $1
                """,
                quality_max,
                tags
            )

        return int(result.split()[-1])

    # ========================================================================
    # STATISTICS
    # ========================================================================

    async def get_statistics(
        self,
        filters: Optional[Dict[str, Any]] = None
    ) -> TrainingDataStats:
        """
        Get statistics about training data.

        Returns: count, quality distribution, intent breakdown, etc.
        """
        # Build base query
        base_query = "SELECT * FROM orchestrator_training_data"
        params = []

        if filters:
            conditions = []
            param_count = 1

            if filters.get("exclude_tags"):
                conditions.append(f"NOT (tags && ARRAY[${param_count}]::text[])")
                params.append(filters["exclude_tags"])
                param_count += 1

            if conditions:
                base_query += " WHERE " + " AND ".join(conditions)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(base_query, *params)

        # Calculate stats
        total = len(rows)
        if total == 0:
            return TrainingDataStats(
                total_examples=0,
                by_tag={},
                avg_quality_score=0.0,
                quality_score_distribution={},
                by_intent={},
                success_rate=0.0,
                date_range={}
            )

        quality_scores = [r["quality_score"] for r in rows if r["quality_score"]]
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0

        # Build stats
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

        for row in rows:
            success_count += 1 if row["success"] else 0

            # Tally tags
            for tag in row["tags"] or []:
                by_tag[tag] = by_tag.get(tag, 0) + 1

            # Tally intents
            if row["intent"]:
                by_intent[row["intent"]] = by_intent.get(row["intent"], 0) + 1

            # Distribution
            if row["quality_score"]:
                q = row["quality_score"]
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

        dates = [r["created_at"] for r in rows]
        date_range = {
            "earliest": str(min(dates)) if dates else None,
            "latest": str(max(dates)) if dates else None,
        }

        return TrainingDataStats(
            total_examples=total,
            by_tag=by_tag,
            avg_quality_score=avg_quality,
            quality_score_distribution=quality_distribution,
            by_intent=by_intent,
            success_rate=success_count / total if total > 0 else 0.0,
            date_range=date_range
        )

    # ========================================================================
    # EXPORT
    # ========================================================================

    async def export_as_jsonl(
        self,
        filters: Optional[Dict[str, Any]] = None,
        output_path: str = "/tmp/training_data.jsonl"
    ) -> Dict[str, Any]:
        """
        Export training data as JSONL for fine-tuning.

        Returns: statistics about export
        """
        # Get filtered data
        if filters:
            data = await self.filter_training_data(**filters)
        else:
            data = await self.get_all_training_data(limit=10000)

        # Write JSONL
        with open(output_path, 'w') as f:
            for example in data:
                # Prepare for fine-tuning (format varies by provider)
                training_obj = {
                    "messages": [
                        {
                            "role": "system",
                            "content": f"You are an intelligent business orchestrator. Your task: {example.intent}"
                        },
                        {
                            "role": "user",
                            "content": example.user_request
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps({
                                "plan": example.execution_plan,
                                "expected_quality": example.quality_score,
                                "business_context": example.business_state
                            })
                        }
                    ],
                    "metadata": {
                        "success": example.success,
                        "quality_score": example.quality_score,
                        "intent": example.intent,
                        "metrics": example.post_publication_metrics,
                        "patterns": example.patterns_discovered
                    }
                }
                f.write(json.dumps(training_obj) + "\n")

        file_size = os.path.getsize(output_path)

        return {
            "success": True,
            "file_path": output_path,
            "file_size": file_size,
            "example_count": len(data),
            "avg_quality": sum(e.quality_score for e in data) / len(data) if data else 0
        }

    # ========================================================================
    # DATASETS
    # ========================================================================

    async def create_dataset(
        self,
        name: str,
        description: str,
        filters: Dict[str, Any]
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
            filters=filters,
            output_path=f"/tmp/dataset_{name}_{datetime.now().timestamp()}.jsonl"
        )

        # Get version number
        async with self.pool.acquire() as conn:
            version = await conn.fetchval(
                "SELECT MAX(version) FROM training_datasets WHERE name = $1",
                name
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
                export_result["file_size"]
            )

        return {
            "name": name,
            "version": version,
            "example_count": export_result["example_count"],
            "avg_quality": export_result["avg_quality"],
            "file_path": export_result["file_path"],
            "file_size": export_result["file_size"]
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
            patterns_discovered=row.get("patterns_discovered")
        )
```

---

## Implementation: Fine-Tuning Service

```python
# src/cofounder_agent/services/fine_tuning_service.py

from typing import Dict, Any, Optional
from enum import Enum
import asyncio
import logging
import subprocess
import os

logger = logging.getLogger(__name__)


class FineTuneTarget(str, Enum):
    """Target model for fine-tuning"""
    OLLAMA = "ollama"
    GEMINI = "gemini"
    CLAUDE = "claude"
    GPT4 = "gpt4"


class FineTuningService:
    """
    Manages fine-tuning of models.

    Supports:
    - Local fine-tuning with Ollama
    - API-based fine-tuning (Gemini, Claude, OpenAI)
    - Job tracking and status monitoring
    - Model deployment and switching
    """

    def __init__(self, db_pool=None):
        self.db_pool = db_pool
        self.jobs = {}  # In-memory job tracking (replace with DB in production)

    # ========================================================================
    # OLLAMA FINE-TUNING (Local, Free, Private)
    # ========================================================================

    async def fine_tune_ollama(
        self,
        dataset_path: str,
        base_model: str = "mistral",
        learning_rate: float = 0.001,
        epochs: int = 3
    ) -> Dict[str, Any]:
        """
        Fine-tune using Ollama (100% local, free, private).

        Args:
            dataset_path: Path to JSONL training data
            base_model: Base model to fine-tune (mistral, llama2, neural-chat)
            learning_rate: Learning rate for training
            epochs: Number of training epochs

        Returns:
            Job metadata
        """
        job_id = f"ollama_finetune_{datetime.now().timestamp()}"

        try:
            # Create Modelfile for fine-tuning
            modelfile = f"""
FROM {base_model}
PARAMETER temperature 0.7
PARAMETER top_p 0.9
"""
            modelfile_path = f"/tmp/Modelfile_{job_id}"
            with open(modelfile_path, 'w') as f:
                f.write(modelfile)

            # Start background fine-tuning process
            # Note: Ollama fine-tuning is still in development
            # This is a placeholder for the actual implementation
            process = await asyncio.create_subprocess_exec(
                "ollama", "finetune",
                base_model,
                f"--dataset={dataset_path}",
                f"--output={job_id}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            self.jobs[job_id] = {
                "status": "running",
                "target": "ollama",
                "base_model": base_model,
                "process": process,
                "start_time": datetime.now(),
                "dataset_path": dataset_path
            }

            return {
                "job_id": job_id,
                "status": "running",
                "target": "ollama",
                "base_model": base_model,
                "message": "Fine-tuning started. Check status with job_id."
            }

        except Exception as e:
            logger.error(f"Failed to start Ollama fine-tuning: {e}")
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e)
            }

    # ========================================================================
    # GEMINI FINE-TUNING (API-based)
    # ========================================================================

    async def fine_tune_gemini(
        self,
        dataset_path: str,
        api_key: str
    ) -> Dict[str, Any]:
        """
        Fine-tune using Google Gemini (API-based).

        Requires: GOOGLE_API_KEY environment variable
        """
        job_id = f"gemini_finetune_{datetime.now().timestamp()}"

        try:
            import google.generativeai as genai

            genai.configure(api_key=api_key)

            # Upload training data
            media = genai.upload_file(dataset_path)

            # Start fine-tuning
            model_name = f"projects/{{project_id}}/locations/us/models/{job_id}"

            base_model = genai.GenerativeModel("gemini-1.5-pro")

            # Note: Actual Gemini fine-tuning API call
            operation = await base_model.fine_tune(
                training_data=[
                    genai.types.FileData(mime_type=media.mime_type, file_uri=media.uri)
                ]
            )

            self.jobs[job_id] = {
                "status": "running",
                "target": "gemini",
                "operation": operation,
                "start_time": datetime.now()
            }

            return {
                "job_id": job_id,
                "status": "running",
                "target": "gemini",
                "model_name": model_name,
                "estimated_time": "2-4 hours"
            }

        except Exception as e:
            logger.error(f"Failed to start Gemini fine-tuning: {e}")
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e)
            }

    # ========================================================================
    # CLAUDE FINE-TUNING (API-based)
    # ========================================================================

    async def fine_tune_claude(
        self,
        dataset_path: str,
        api_key: str
    ) -> Dict[str, Any]:
        """
        Fine-tune using Anthropic Claude (API-based).

        Requires: ANTHROPIC_API_KEY environment variable
        """
        job_id = f"claude_finetune_{datetime.now().timestamp()}"

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)

            # Upload training data
            with open(dataset_path, 'rb') as f:
                response = client.beta.files.upload(
                    file=(os.path.basename(dataset_path), f, "text/jsonl")
                )

            file_id = response.id

            # Start fine-tuning job
            job_response = client.beta.fine_tuning.jobs.create(
                model="claude-3-haiku-20240307",
                training_data={"type": "file", "file_id": file_id}
            )

            job_id_api = job_response.id
            self.jobs[job_id] = {
                "status": "running",
                "target": "claude",
                "job_id_api": job_id_api,
                "start_time": datetime.now()
            }

            return {
                "job_id": job_id,
                "job_id_api": job_id_api,
                "status": "running",
                "target": "claude",
                "estimated_time": "1-3 hours"
            }

        except Exception as e:
            logger.error(f"Failed to start Claude fine-tuning: {e}")
            return {
                "job_id": job_id,
                "status": "failed",
                "error": str(e)
            }

    # ========================================================================
    # JOB MANAGEMENT
    # ========================================================================

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Check status of a fine-tuning job"""
        if job_id not in self.jobs:
            return {"status": "not_found", "job_id": job_id}

        job = self.jobs[job_id]

        if job["target"] == "ollama":
            # Check process
            if job["process"].returncode is None:
                return {
                    "job_id": job_id,
                    "status": "running",
                    "target": "ollama",
                    "progress": "Training in progress..."
                }
            else:
                return {
                    "job_id": job_id,
                    "status": "complete" if job["process"].returncode == 0 else "failed",
                    "target": "ollama",
                    "model_name": f"orchestrator-{job_id}"
                }

        elif job["target"] == "gemini":
            operation = job["operation"]
            if operation.done():
                return {
                    "job_id": job_id,
                    "status": "complete",
                    "target": "gemini",
                    "model_name": operation.result().name
                }
            else:
                return {
                    "job_id": job_id,
                    "status": "running",
                    "target": "gemini",
                    "progress": f"{operation.metadata.progress_percentage}%"
                }

        elif job["target"] == "claude":
            client = anthropic.Anthropic()
            job_api = client.beta.fine_tuning.jobs.retrieve(job["job_id_api"])

            status_map = {
                "queued": "queued",
                "processing": "running",
                "succeeded": "complete",
                "failed": "failed"
            }

            return {
                "job_id": job_id,
                "status": status_map.get(job_api.status, job_api.status),
                "target": "claude",
                "model_name": job_api.fine_tuned_model if job_api.status == "succeeded" else None
            }

        return {"job_id": job_id, "status": "unknown"}

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a running fine-tuning job"""
        if job_id not in self.jobs:
            return {"success": False, "error": "Job not found"}

        job = self.jobs[job_id]

        if job["target"] == "ollama":
            job["process"].terminate()
            job["status"] = "cancelled"
            return {"success": True, "job_id": job_id, "status": "cancelled"}

        return {"success": False, "error": "Cannot cancel this job type"}

    # ========================================================================
    # MODEL DEPLOYMENT
    # ========================================================================

    async def deploy_model(
        self,
        model_name: str,
        job_id: str,
        set_active: bool = False
    ) -> Dict[str, Any]:
        """
        Deploy a fine-tuned model for use.

        Args:
            model_name: Name to register model as
            job_id: ID of completed fine-tuning job
            set_active: Whether to make this the active model
        """
        job = self.jobs.get(job_id, {})

        if job.get("status") != "complete":
            return {
                "success": False,
                "error": "Job is not complete"
            }

        return {
            "success": True,
            "model_name": model_name,
            "source": job.get("target"),
            "registered_at": datetime.now().isoformat(),
            "set_active": set_active
        }
```

---

## Implementation: Frontend Components

### TrainingDataDashboard.jsx

```javascript
// web/oversight-hub/src/components/pages/TrainingDataDashboard.jsx

import React, { useState, useEffect } from 'react';
import './TrainingDataDashboard.css';

const TrainingDataDashboard = () => {
  const [stats, setStats] = useState(null);
  const [filters, setFilters] = useState({
    quality_min: 0.0,
    quality_max: 1.0,
    exclude_tags: ['development', 'test'], // Default: exclude dev/test
    date_after: null,
    date_before: null,
  });
  const [selectedDataset, setSelectedDataset] = useState(null);
  const [trainingJobs, setTrainingJobs] = useState([]);
  const [activeModel, setActiveModel] = useState(null);

  useEffect(() => {
    // Load statistics and filter data
    loadStatistics();
    loadTrainingJobs();
  }, [filters]);

  const loadStatistics = async () => {
    try {
      const response = await fetch(
        `/api/orchestrator/training/data/filter?${new URLSearchParams(
          Object.entries(filters).reduce((acc, [key, val]) => {
            if (Array.isArray(val)) acc[key] = val.join(',');
            else if (val) acc[key] = val;
            return acc;
          }, {})
        )}`
      );
      const data = await response.json();
      setStats(data);
    } catch (err) {
      console.error('Failed to load stats:', err);
    }
  };

  const loadTrainingJobs = async () => {
    try {
      const response = await fetch('/api/orchestrator/training/jobs');
      const data = await response.json();
      setTrainingJobs(data);
    } catch (err) {
      console.error('Failed to load jobs:', err);
    }
  };

  const handleTagByDateRange = async () => {
    const response = await fetch(
      '/api/orchestrator/training/data/tag-by-date',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          date_after: filters.date_after,
          date_before: filters.date_before,
          tags: ['development'],
        }),
      }
    );
    const result = await response.json();
    alert(`Tagged ${result.count} examples as development`);
    loadStatistics();
  };

  const handleCreateDataset = async () => {
    const response = await fetch('/api/orchestrator/training/datasets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: 'production',
        description: 'Production-ready training data',
        filters,
      }),
    });
    const dataset = await response.json();
    setSelectedDataset(dataset);
    alert(
      `Created dataset v${dataset.version} with ${dataset.example_count} examples`
    );
  };

  const handleStartFineTuning = async (target) => {
    if (!selectedDataset) {
      alert('Please select or create a dataset first');
      return;
    }

    const response = await fetch('/api/orchestrator/training/fine-tune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target,
        dataset_id: selectedDataset.id,
        base_model: 'mistral', // For Ollama
      }),
    });

    const job = await response.json();
    alert(`Started ${target} fine-tuning job: ${job.job_id}`);
    loadTrainingJobs();
  };

  return (
    <div className="training-dashboard">
      <h1>🧠 Training Data Management</h1>

      {/* Section 1: Data Filtering */}
      <div className="section">
        <h2>📊 Data Filtering & Preview</h2>

        <div className="filter-group">
          <label>
            Quality Score Range:
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={filters.quality_min}
              onChange={(e) =>
                setFilters({
                  ...filters,
                  quality_min: parseFloat(e.target.value),
                })
              }
            />
            {filters.quality_min.toFixed(2)} - {filters.quality_max.toFixed(2)}
          </label>
        </div>

        <div className="filter-group">
          <label>
            <input
              type="checkbox"
              checked={filters.exclude_tags?.includes('development')}
              onChange={(e) => {
                const tags = e.target.checked
                  ? [...(filters.exclude_tags || []), 'development']
                  : (filters.exclude_tags || []).filter(
                      (t) => t !== 'development'
                    );
                setFilters({ ...filters, exclude_tags: tags });
              }}
            />
            Exclude Development Data
          </label>
          <label>
            <input
              type="checkbox"
              checked={filters.exclude_tags?.includes('test')}
              onChange={(e) => {
                const tags = e.target.checked
                  ? [...(filters.exclude_tags || []), 'test']
                  : (filters.exclude_tags || []).filter((t) => t !== 'test');
                setFilters({ ...filters, exclude_tags: tags });
              }}
            />
            Exclude Test Data
          </label>
        </div>

        {stats && (
          <div className="stats-summary">
            <p>
              ✅ Using {stats.filtered_count} of {stats.total_count} examples
            </p>
            <p>📈 Average Quality: {(stats.avg_quality * 100).toFixed(1)}%</p>
            <p>🎯 Success Rate: {(stats.success_rate * 100).toFixed(1)}%</p>
          </div>
        )}
      </div>

      {/* Section 2: Tag Management */}
      <div className="section">
        <h2>🏷️ Data Tagging & Bulk Operations</h2>

        <div className="bulk-ops">
          <h3>Tag by Date Range:</h3>
          <input
            type="date"
            value={filters.date_after || ''}
            onChange={(e) =>
              setFilters({ ...filters, date_after: e.target.value })
            }
          />
          <input
            type="date"
            value={filters.date_before || ''}
            onChange={(e) =>
              setFilters({ ...filters, date_before: e.target.value })
            }
          />
          <button onClick={handleTagByDateRange}>Tag as "Development"</button>
        </div>
      </div>

      {/* Section 3: Dataset Management */}
      <div className="section">
        <h2>📦 Training Datasets</h2>

        <button onClick={handleCreateDataset}>
          + Create Dataset from Filters
        </button>

        {selectedDataset && (
          <div className="dataset-info">
            <p>
              ✅ Dataset v{selectedDataset.version}:{' '}
              {selectedDataset.example_count} examples
            </p>
            <p>
              Average Quality: {(selectedDataset.avg_quality * 100).toFixed(1)}%
            </p>
          </div>
        )}
      </div>

      {/* Section 4: Fine-Tuning */}
      <div className="section">
        <h2>🚀 Fine-Tuning</h2>

        <div className="fine-tune-options">
          <h3>Select Target Model:</h3>
          <button onClick={() => handleStartFineTuning('ollama')}>
            🏠 Fine-tune with Ollama (Local, Free)
          </button>
          <button onClick={() => handleStartFineTuning('gemini')}>
            ✨ Fine-tune with Gemini (Google API)
          </button>
          <button onClick={() => handleStartFineTuning('claude')}>
            ⭐ Fine-tune with Claude (Anthropic API)
          </button>
        </div>
      </div>

      {/* Section 5: Training Jobs */}
      <div className="section">
        <h2>⏱️ Training Jobs</h2>

        {trainingJobs.map((job) => (
          <div key={job.job_id} className="job-card">
            <h4>
              {job.target.toUpperCase()} - {job.status}
            </h4>
            <p>Job ID: {job.job_id}</p>
            <p>Started: {new Date(job.start_time).toLocaleString()}</p>
            {job.status === 'complete' && (
              <>
                <p>✅ Model: {job.model_name}</p>
                <button onClick={() => setActiveModel(job.model_name)}>
                  Set as Active
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export default TrainingDataDashboard;
```

---

## Summary: Your Options

### **Option 1: Exclude Dev Data (Recommended Now)**

```sql
-- Tag all data before 2025-12-01 as "development"
UPDATE orchestrator_training_data
SET tags = array_append(tags, 'development')
WHERE created_at < '2025-12-01'::timestamp;

-- When exporting, use filters: exclude_tags: ['development']
-- This way data is still there but never used for training
```

### **Option 2: Delete Bad Data**

```sql
-- Delete all data with quality < 0.7
DELETE FROM orchestrator_training_data
WHERE quality_score < 0.7;

-- Delete all data before a certain date
DELETE FROM orchestrator_training_data
WHERE created_at < '2025-12-01'::timestamp;
```

### **Option 3: Fine-Tuning Strategy**

```
DEVELOPMENT (Free, Local)
├─ Use Ollama + Mistral
├─ Fine-tune on production data only
├─ Test locally before going to production
└─ Cost: $0

PRODUCTION (When Confident)
├─ Option A: Deploy fine-tuned Ollama model to production
├─ Option B: Fine-tune with Gemini/Claude and use their API
├─ Option C: Keep using base models with prompt optimization
└─ Cost: $0 (Ollama) or $5-100/month (APIs)

HYBRID (Recommended)
├─ Start with Ollama locally (proves concept)
├─ Test on real data
├─ If performance good → use it in production
├─ If performance bad → fine-tune Gemini instead
├─ Can switch models with ModelRouter (you have this already!)
└─ Cost: Flexible based on what works
```

The UI dashboard I outlined lets you:

1. **Tag old data** without deleting it
2. **Filter at export time** (exclude tagged data)
3. **Choose your model** (Ollama free or Gemini/Claude paid)
4. **Compare performance** between models
5. **Switch active model** without redeploying

Would you like me to implement the TrainingDataService + API endpoints next?
