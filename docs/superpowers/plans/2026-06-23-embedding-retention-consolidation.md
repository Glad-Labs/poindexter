# Embedding Retention Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold `prune_stale_embeddings`, `prune_orphan_embeddings`, and `collapse_old_embeddings` plugin jobs into the `retention_policies` declarative framework as two new handlers, retiring the three jobs so embedding hygiene is controlled entirely through DB rows.

**Architecture:** Add `min_interval_hours` to `retention_policies` so the runner can skip policies that aren't due yet (collapse stays weekly; TTL/orphan run every 6-hour cycle). Write two new retention handlers (`embeddings_orphan_prune`, `embeddings_collapse`) that port SQL and k-means logic from the retiring jobs. Retire the jobs and their settings keys.

**Tech Stack:** Python/asyncpg, APScheduler (via existing PluginScheduler), pure-Python k-means, OllamaClient (for collapse summaries), `@register_handler("retention", ...)` decorator pattern matching `retention_ttl_prune.py`.

## Global Constraints

- All config lives in the policy row's `config` jsonb — never read `app_settings` keys in the new handlers.
- Handler return dict must include `"deleted"` (int) key — `retention_runner._record_success` reads it.
- New rows seeded `enabled = FALSE` — operator enables deliberately post-deploy.
- No `_NEVER_COLLAPSE` guard in the handler — row absence is the policy.
- Migration files: `python scripts/new-migration.py "<description>"` to generate timestamped filename. Runner sorts lexically; `0000_baseline.py` always runs first.
- New `app_settings` keys → `settings_defaults.py`, not migrations. These tasks add none.
- Tests live under `src/cofounder_agent/tests/unit/`; run with `cd src/cofounder_agent && poetry run pytest <path> -v`.
- Pre-commit hook runs prettier on `.sql` files — commit baseline changes as a single staged set.

---

## File Map

**Create:**

- `src/cofounder_agent/services/migrations/20260623_HHMMSS_embedding_retention_consolidation.py` — combined schema + data migration (use `python scripts/new-migration.py "embedding retention consolidation"` for real timestamp)
- `src/cofounder_agent/services/integrations/handlers/retention_embeddings_orphan_prune.py` — new `embeddings_orphan_prune` handler
- `src/cofounder_agent/services/integrations/handlers/retention_embeddings_collapse.py` — new `embeddings_collapse` handler
- `src/cofounder_agent/tests/unit/services/integrations/handlers/test_retention_embeddings_orphan_prune.py`
- `src/cofounder_agent/tests/unit/services/integrations/handlers/test_retention_embeddings_collapse.py`
- `src/cofounder_agent/tests/unit/services/integrations/test_retention_runner_interval.py`

**Modify:**

- `src/cofounder_agent/services/migrations/0000_baseline.schema.sql` — add `min_interval_hours` column + drop check constraint from CREATE TABLE
- `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` — update 3 TTL rows + add 6 new rows + remove 2 dead `embedding_collapse_*` settings
- `src/cofounder_agent/services/integrations/retention_runner.py` — add `from datetime import datetime, timedelta, timezone` + skip-if-not-due loop check
- `src/cofounder_agent/plugins/registry.py` — remove 3 job entries from `_SAMPLES`

**Delete:**

- `src/cofounder_agent/services/jobs/prune_stale_embeddings.py`
- `src/cofounder_agent/services/jobs/prune_orphan_embeddings.py`
- `src/cofounder_agent/services/jobs/collapse_old_embeddings.py`
- `src/cofounder_agent/tests/unit/services/test_prune_stale_embeddings_job.py`
- `src/cofounder_agent/tests/unit/services/test_prune_orphan_embeddings_job.py`
- `src/cofounder_agent/tests/unit/services/jobs/test_collapse_old_embeddings_job.py`

---

## Task 1: Schema, Baseline Seeds, and Convergence Migration

Three coordinated changes that must land together:

1. `0000_baseline.schema.sql` — add `min_interval_hours` column and drop the check constraint that would block orphan/collapse rows (they have no `ttl_days`).
2. `0000_baseline.seeds.sql` — update 3 existing TTL rows (new names, correct TTLs, fixed JSON) and add 6 new rows.
3. New migration — for prod: drops the constraint, adds the column, renames/fixes existing rows, inserts new rows.

**Files:**

- Modify: `src/cofounder_agent/services/migrations/0000_baseline.schema.sql:2700-2723`
- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql:1019-1021`
- Create: `src/cofounder_agent/services/migrations/20260623_HHMMSS_embedding_retention_consolidation.py`

**Interfaces:**

- Produces: `retention_policies` table with `min_interval_hours REAL` column, no parameter-required check constraint, 9 correctly-seeded rows (3 TTL rows with new names + 6 new rows).

- [ ] **Step 1: Update baseline schema — add column, remove constraint**

In `0000_baseline.schema.sql`, find the `CREATE TABLE IF NOT EXISTS public.retention_policies` block (around line 2700). Make two changes:

First, add `min_interval_hours real,` after the `ttl_days integer,` line:

```sql
    ttl_days integer,
    min_interval_hours real,
    downsample_rule jsonb,
```

Second, remove this constraint line entirely from the table definition:

```sql
    CONSTRAINT retention_policies_parameter_required_chk CHECK (((ttl_days IS NOT NULL) OR (downsample_rule IS NOT NULL) OR (summarize_handler IS NOT NULL)))
```

The constraint is incompatible with the new handler types (`embeddings_orphan_prune`, `embeddings_collapse`) that carry all config in the `config` jsonb column and have no `ttl_days`. The migration will drop it on prod.

- [ ] **Step 2: Update baseline seeds — fix 3 existing TTL rows**

In `0000_baseline.seeds.sql`, replace the three embedding retention_policies INSERT statements (currently around lines 1019–1021). The changes per row:

- Name: `embeddings.audit` → `embeddings.ttl_prune.audit`; name: `embeddings.brain` → `embeddings.ttl_prune.brain`; name: `embeddings.claude_sessions` → `embeddings.ttl_prune.claude_sessions`
- `ttl_days`: brain changes from `180` → `365`; others unchanged
- `filter_sql`: add `AND COALESCE(is_summary, FALSE) = FALSE` to each (preserves summary rows)
- `config`: fix malformed `'"{}"'::jsonb` → `'{}'::jsonb`
- `metadata`: fix malformed `'"{...}"'::jsonb` → `'{...}'::jsonb` (keep description content, drop extra quotes)
- `enabled`: keep `true` (these rows were already running on prod)
- Add `min_interval_hours` column: `NULL` for all three

Replace those three lines with:

```sql
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('2fdd1942-7965-40dd-b8bd-ab5d8dc69ae1', 'embeddings.ttl_prune.audit', 'ttl_prune', 'embeddings', 'source_table = ''audit'' AND COALESCE(is_summary, FALSE) = FALSE', 'created_at', 90, NULL, NULL, true, '{}'::jsonb, '{"description": "Audit event embeddings — long enough for quarterly reviews"}'::jsonb, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('57ab87c7-5ac4-4ecd-adb5-e23c225f3224', 'embeddings.ttl_prune.brain', 'ttl_prune', 'embeddings', 'source_table = ''brain'' AND COALESCE(is_summary, FALSE) = FALSE', 'created_at', 365, NULL, NULL, true, '{}'::jsonb, '{"description": "Brain decision embeddings — system reasoning, slower decay"}'::jsonb, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('9f0fc40f-6742-4ae2-b71d-50892b7d2ba6', 'embeddings.ttl_prune.claude_sessions', 'ttl_prune', 'embeddings', 'source_table = ''claude_sessions'' AND COALESCE(is_summary, FALSE) = FALSE', 'created_at', 30, NULL, NULL, true, '{}'::jsonb, '{"description": "Claude session chunks — ephemeral working notes, recent context matters"}'::jsonb, NULL) ON CONFLICT (id) DO NOTHING;
```

Note: same UUIDs as before — `ON CONFLICT (id) DO NOTHING` means these are no-ops on prod (where the rows already exist with old names). The migration in Step 4 renames them on prod.

- [ ] **Step 3: Add 6 new rows to baseline seeds**

Immediately after the three rows above, append:

```sql
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('7a000001-0000-0000-0000-000000000001', 'embeddings.orphan_prune.posts', 'embeddings_orphan_prune', 'embeddings', NULL, 'created_at', NULL, NULL, NULL, false, '{"source_table": "posts", "batch_size": 1000}'::jsonb, '{}'::jsonb, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('7a000001-0000-0000-0000-000000000002', 'embeddings.orphan_prune.audit', 'embeddings_orphan_prune', 'embeddings', NULL, 'created_at', NULL, NULL, NULL, false, '{"source_table": "audit", "batch_size": 1000}'::jsonb, '{}'::jsonb, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('7a000001-0000-0000-0000-000000000003', 'embeddings.orphan_prune.brain', 'embeddings_orphan_prune', 'embeddings', NULL, 'created_at', NULL, NULL, NULL, false, '{"source_table": "brain", "batch_size": 1000}'::jsonb, '{}'::jsonb, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('7a000001-0000-0000-0000-000000000004', 'embeddings.collapse.claude_sessions', 'embeddings_collapse', 'embeddings', NULL, 'created_at', NULL, NULL, NULL, false, '{"source_table": "claude_sessions", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}'::jsonb, '{}'::jsonb, 168) ON CONFLICT (id) DO NOTHING;
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('7a000001-0000-0000-0000-000000000005', 'embeddings.collapse.brain', 'embeddings_collapse', 'embeddings', NULL, 'created_at', NULL, NULL, NULL, false, '{"source_table": "brain", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}'::jsonb, '{}'::jsonb, 168) ON CONFLICT (id) DO NOTHING;
INSERT INTO retention_policies (id, name, handler_name, table_name, filter_sql, age_column, ttl_days, downsample_rule, summarize_handler, enabled, config, metadata, min_interval_hours) VALUES ('7a000001-0000-0000-0000-000000000006', 'embeddings.collapse.audit', 'embeddings_collapse', 'embeddings', NULL, 'created_at', NULL, NULL, NULL, false, '{"source_table": "audit", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}'::jsonb, '{}'::jsonb, 168) ON CONFLICT (id) DO NOTHING;
```

- [ ] **Step 4: Create the convergence migration**

Generate the file: `cd src/cofounder_agent && python ../../scripts/new-migration.py "embedding retention consolidation"`. Open the generated file and replace its body with:

```python
"""Embedding retention consolidation.

Prod convergence for:
- Drop parameter_required check constraint (incompatible with config-based handlers)
- Add min_interval_hours column for per-policy cadence control
- Rename 3 existing TTL rows (embeddings.X -> embeddings.ttl_prune.X), fix TTLs and JSON
- Insert 6 new rows (orphan_prune × 3, collapse × 3); min_interval_hours=168 for collapse
"""

from __future__ import annotations

from typing import Any


async def run(conn: Any) -> None:
    # 1. Drop the check constraint — new handler types carry config in jsonb,
    #    not in ttl_days / downsample_rule / summarize_handler.
    await conn.execute(
        "ALTER TABLE retention_policies "
        "DROP CONSTRAINT IF EXISTS retention_policies_parameter_required_chk"
    )

    # 2. Add min_interval_hours column (idempotent — IF NOT EXISTS).
    await conn.execute(
        "ALTER TABLE retention_policies "
        "ADD COLUMN IF NOT EXISTS min_interval_hours REAL"
    )

    # 3. Rename + fix the 3 existing TTL rows.
    #    WHERE matches old name → no-op on fresh installs (baseline already has new name).
    renames = [
        (
            "embeddings.ttl_prune.audit",
            "source_table = 'audit' AND COALESCE(is_summary, FALSE) = FALSE",
            90,
            '{"description": "Audit event embeddings — long enough for quarterly reviews"}',
            "embeddings.audit",
        ),
        (
            "embeddings.ttl_prune.brain",
            "source_table = 'brain' AND COALESCE(is_summary, FALSE) = FALSE",
            365,
            '{"description": "Brain decision embeddings — system reasoning, slower decay"}',
            "embeddings.brain",
        ),
        (
            "embeddings.ttl_prune.claude_sessions",
            "source_table = 'claude_sessions' AND COALESCE(is_summary, FALSE) = FALSE",
            30,
            '{"description": "Claude session chunks — ephemeral working notes, recent context matters"}',
            "embeddings.claude_sessions",
        ),
    ]
    for new_name, filter_sql, ttl_days, metadata_json, old_name in renames:
        await conn.execute(
            """
            UPDATE retention_policies
               SET name = $1,
                   filter_sql = $2,
                   ttl_days = $3,
                   config = '{}'::jsonb,
                   metadata = $4::jsonb,
                   enabled = true
             WHERE name = $5
            """,
            new_name, filter_sql, ttl_days, metadata_json, old_name,
        )

    # 4. Insert 6 new rows (ON CONFLICT (id) DO NOTHING — baseline already
    #    inserted them on fresh installs; UPDATE sets min_interval_hours after).
    new_rows = [
        ("7a000001-0000-0000-0000-000000000001", "embeddings.orphan_prune.posts",
         "embeddings_orphan_prune", '{"source_table": "posts", "batch_size": 1000}', None),
        ("7a000001-0000-0000-0000-000000000002", "embeddings.orphan_prune.audit",
         "embeddings_orphan_prune", '{"source_table": "audit", "batch_size": 1000}', None),
        ("7a000001-0000-0000-0000-000000000003", "embeddings.orphan_prune.brain",
         "embeddings_orphan_prune", '{"source_table": "brain", "batch_size": 1000}', None),
        ("7a000001-0000-0000-0000-000000000004", "embeddings.collapse.claude_sessions",
         "embeddings_collapse",
         '{"source_table": "claude_sessions", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}',
         168),
        ("7a000001-0000-0000-0000-000000000005", "embeddings.collapse.brain",
         "embeddings_collapse",
         '{"source_table": "brain", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}',
         168),
        ("7a000001-0000-0000-0000-000000000006", "embeddings.collapse.audit",
         "embeddings_collapse",
         '{"source_table": "audit", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}',
         168),
    ]
    for row_id, name, handler, config_json, min_interval_hours in new_rows:
        await conn.execute(
            """
            INSERT INTO retention_policies
                (id, name, handler_name, table_name, age_column, enabled,
                 config, metadata, min_interval_hours)
            VALUES ($1, $2, $3, 'embeddings', 'created_at', false,
                    $4::jsonb, '{}'::jsonb, $5)
            ON CONFLICT (id) DO NOTHING
            """,
            row_id, name, handler, config_json, min_interval_hours,
        )

    # 5. Set min_interval_hours=168 for collapse rows that already exist
    #    (covers fresh installs where step 4 was a no-op).
    await conn.execute(
        "UPDATE retention_policies SET min_interval_hours = 168 "
        "WHERE name LIKE 'embeddings.collapse.%' AND min_interval_hours IS NULL"
    )
```

- [ ] **Step 5: Verify migration is valid**

```bash
cd src/cofounder_agent && python ../../scripts/ci/migrations_lint.py
```

Expected: no errors about the new migration file.

- [ ] **Step 6: Smoke test**

```bash
cd src/cofounder_agent && python ../../scripts/ci/migrations_smoke.py
```

Expected: exits 0 with no errors. This runs the full migration chain against a throwaway DB, including the new migration.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/migrations/0000_baseline.schema.sql \
        src/cofounder_agent/services/migrations/0000_baseline.seeds.sql \
        src/cofounder_agent/services/migrations/20260623_*_embedding_retention_consolidation.py
git commit -m "feat(retention): schema + seeds for embedding retention consolidation

Add min_interval_hours to retention_policies, drop legacy parameter-required
constraint, seed 9 embedding hygiene rows (3 TTL-renamed + 6 new orphan/collapse).
Convergence migration renames prod rows and inserts new ones.
"
```

---

## Task 2: Runner Skip-If-Not-Due

**Files:**

- Modify: `src/cofounder_agent/services/integrations/retention_runner.py`
- Create: `src/cofounder_agent/tests/unit/services/integrations/test_retention_runner_interval.py`

**Interfaces:**

- Consumes: `retention_policies.min_interval_hours` (REAL or None), `retention_policies.last_run_at` (datetime or None) — both come from the row dict already loaded by `_load_enabled_policies`.
- Produces: rows whose `min_interval_hours` is not None AND `last_run_at + min_interval_hours hours > now()` are silently skipped (not dispatched, not recorded, not in results).

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/integrations/test_retention_runner_interval.py`:

```python
"""Tests for retention_runner per-policy interval throttle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.integrations.retention_runner import run_all


def _row(name: str, min_interval_hours=None, last_run_at=None):
    return {
        "id": 1,
        "name": name,
        "handler_name": "ttl_prune",
        "min_interval_hours": min_interval_hours,
        "last_run_at": last_run_at,
    }


@pytest.mark.asyncio
async def test_skip_when_recently_run():
    """Policy with min_interval_hours=1 run 30 min ago → skipped."""
    recent = datetime.now(timezone.utc) - timedelta(minutes=30)
    row = _row("test.policy", min_interval_hours=1.0, last_run_at=recent)

    with patch(
        "services.integrations.retention_runner._load_enabled_policies",
        new=AsyncMock(return_value=[row]),
    ):
        summary = await run_all(pool=None)

    assert summary.total_deleted == 0
    assert summary.total_failed == 0
    assert len(summary.policies) == 0  # skipped rows don't appear in results


@pytest.mark.asyncio
async def test_not_skipped_when_overdue():
    """Policy with min_interval_hours=1 last run 2 hours ago → dispatched."""
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    row = _row("test.policy", min_interval_hours=1.0, last_run_at=old)

    dispatch_result = {"deleted": 5}
    with (
        patch(
            "services.integrations.retention_runner._load_enabled_policies",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "services.integrations.retention_runner.registry.dispatch",
            new=AsyncMock(return_value=dispatch_result),
        ),
        patch(
            "services.integrations.retention_runner._record_success",
            new=AsyncMock(),
        ),
    ):
        summary = await run_all(pool=None)

    assert len(summary.policies) == 1
    assert summary.policies[0].name == "test.policy"
    assert summary.total_deleted == 5


@pytest.mark.asyncio
async def test_no_interval_always_runs():
    """Policy with min_interval_hours=None always dispatches."""
    row = _row("test.policy", min_interval_hours=None, last_run_at=None)

    dispatch_result = {"deleted": 0}
    with (
        patch(
            "services.integrations.retention_runner._load_enabled_policies",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "services.integrations.retention_runner.registry.dispatch",
            new=AsyncMock(return_value=dispatch_result),
        ),
        patch(
            "services.integrations.retention_runner._record_success",
            new=AsyncMock(),
        ),
    ):
        summary = await run_all(pool=None)

    assert len(summary.policies) == 1
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/test_retention_runner_interval.py -v
```

Expected: `test_skip_when_recently_run` FAILS (no skip logic yet), others may pass incidentally.

- [ ] **Step 3: Implement the skip check**

In `retention_runner.py`, add `from datetime import datetime, timedelta, timezone` to the import block at the top of the file (alongside `import time`).

Then, inside `run_all()`, insert these lines immediately after `name = row["name"]` and before `start = time.perf_counter()`:

```python
        # Per-policy cadence throttle: skip if not due yet.
        min_h = row.get("min_interval_hours")
        last_ran = row.get("last_run_at")
        if min_h and last_ran:
            next_due = last_ran.replace(tzinfo=timezone.utc) + timedelta(hours=float(min_h))
            if datetime.now(timezone.utc) < next_due:
                logger.debug("[retention-runner] %s: not due for %.0fh", name, min_h)
                continue
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/test_retention_runner_interval.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/integrations/retention_runner.py \
        src/cofounder_agent/tests/unit/services/integrations/test_retention_runner_interval.py
git commit -m "feat(retention): per-policy min_interval_hours skip-if-not-due in runner"
```

---

## Task 3: `embeddings_orphan_prune` Handler

**Files:**

- Create: `src/cofounder_agent/services/integrations/handlers/retention_embeddings_orphan_prune.py`
- Create: `src/cofounder_agent/tests/unit/services/integrations/handlers/test_retention_embeddings_orphan_prune.py`

**Interfaces:**

- Consumes: `row["config"]["source_table"]` (str, required), `row["config"]["batch_size"]` (int, default 1000), `pool` (asyncpg pool).
- Produces: `{"deleted": int, "source_table": str, "batch_size": int}` — `deleted` key is read by `retention_runner._record_success`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/services/integrations/handlers/test_retention_embeddings_orphan_prune.py`:

```python
"""Tests for the embeddings_orphan_prune retention handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


def _make_pool(execute_result: str = "DELETE 3") -> MagicMock:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_result)
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


@pytest.mark.asyncio
async def test_posts_orphan_deletes_and_returns_count():
    """posts handler runs a JOIN DELETE and returns deleted count."""
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, conn = _make_pool("DELETE 7")
    row = {"name": "embeddings.orphan_prune.posts", "config": {"source_table": "posts", "batch_size": 500}}

    result = await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 7
    assert result["source_table"] == "posts"
    assert result["batch_size"] == 500
    # Verify the DELETE used a JOIN against posts table
    sql_called = conn.execute.call_args[0][0]
    assert "posts" in sql_called.lower()
    assert "LEFT JOIN posts" in sql_called or "left join posts" in sql_called.lower()


@pytest.mark.asyncio
async def test_audit_handler_joins_audit_log():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, conn = _make_pool("DELETE 2")
    row = {"name": "embeddings.orphan_prune.audit", "config": {"source_table": "audit"}}

    result = await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 2
    sql_called = conn.execute.call_args[0][0]
    assert "audit_log" in sql_called.lower()


@pytest.mark.asyncio
async def test_brain_handler_uses_compound_key():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, conn = _make_pool("DELETE 0")
    row = {"name": "embeddings.orphan_prune.brain", "config": {"source_table": "brain"}}

    result = await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 0
    sql_called = conn.execute.call_args[0][0]
    assert "brain_decisions" in sql_called.lower()
    assert "split_part" in sql_called.lower()


@pytest.mark.asyncio
async def test_unknown_source_raises():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, _ = _make_pool()
    row = {"name": "embeddings.orphan_prune.memory", "config": {"source_table": "memory"}}

    with pytest.raises(ValueError, match="no handler for source_table"):
        await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)


@pytest.mark.asyncio
async def test_missing_source_table_raises():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, _ = _make_pool()
    row = {"name": "embeddings.orphan_prune.posts", "config": {}}

    with pytest.raises((ValueError, KeyError)):
        await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)
```

- [ ] **Step 2: Run tests — expect ImportError or AttributeError**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_retention_embeddings_orphan_prune.py -v
```

Expected: 5 errors — `ModuleNotFoundError` on the import.

- [ ] **Step 3: Implement the handler**

Create `src/cofounder_agent/services/integrations/handlers/retention_embeddings_orphan_prune.py`:

```python
"""Handler: ``retention.embeddings_orphan_prune``.

Deletes embeddings whose (source_table, source_id) no longer corresponds to a
real row in the underlying source table. Each source has source-specific JOIN
SQL (keyed by source_table name in _HANDLERS). Configuration comes entirely
from the policy row's ``config`` jsonb:

  config.source_table  (str, required) — which embeddings source to clean
  config.batch_size    (int, default 1000) — max deletes per run

Sources without a handler (claude_sessions, memory, samples) raise ValueError
— TTL pruning is the only mechanism for those sources.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 1000


def _deleted_count(execute_result: str) -> int:
    """asyncpg returns 'DELETE N' — extract N."""
    try:
        return int(execute_result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def _orphan_posts(pool: Any, batch_size: int) -> int:
    """Posts: source_id is UUID, joins posts.id."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings e
             WHERE e.source_table = 'posts'
               AND e.id IN (
                   SELECT e2.id
                     FROM embeddings e2
                LEFT JOIN posts p ON p.id::text = e2.source_id
                    WHERE e2.source_table = 'posts'
                      AND p.id IS NULL
                    LIMIT $1
               )
            """,
            batch_size,
        )
    return _deleted_count(result)


async def _orphan_audit(pool: Any, batch_size: int) -> int:
    """Audit: source_id = audit_log.id (integer as text)."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings e
             WHERE e.source_table = 'audit'
               AND e.id IN (
                   SELECT e2.id
                     FROM embeddings e2
                LEFT JOIN audit_log a ON a.id::text = e2.source_id
                    WHERE e2.source_table = 'audit'
                      AND a.id IS NULL
                    LIMIT $1
               )
            """,
            batch_size,
        )
    return _deleted_count(result)


async def _orphan_brain(pool: Any, batch_size: int) -> int:
    """Brain: source_id is compound 'brain_decisions/<id>'."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings e
             WHERE e.source_table = 'brain'
               AND e.id IN (
                   SELECT e2.id
                     FROM embeddings e2
                LEFT JOIN brain_decisions b
                       ON b.id::text = split_part(e2.source_id, '/', 2)
                    WHERE e2.source_table = 'brain'
                      AND e2.source_id LIKE 'brain_decisions/%'
                      AND b.id IS NULL
                    LIMIT $1
               )
            """,
            batch_size,
        )
    return _deleted_count(result)


_HANDLERS: dict[str, Callable[[Any, int], Awaitable[int]]] = {
    "posts": _orphan_posts,
    "audit": _orphan_audit,
    "brain": _orphan_brain,
}


@register_handler("retention", "embeddings_orphan_prune")
async def embeddings_orphan_prune(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Delete embeddings whose source row no longer exists."""
    if pool is None:
        raise RuntimeError("embeddings_orphan_prune: pool unavailable")

    config = row.get("config") or {}
    source_table = config.get("source_table")
    if not source_table:
        raise ValueError("embeddings_orphan_prune: config.source_table is required")

    handler = _HANDLERS.get(source_table)
    if handler is None:
        raise ValueError(
            f"embeddings_orphan_prune: no handler for source_table={source_table!r}. "
            f"Known sources: {sorted(_HANDLERS)}. "
            f"For claude_sessions/memory, use TTL pruning instead."
        )

    batch_size = int(config.get("batch_size") or _DEFAULT_BATCH_SIZE)

    try:
        deleted = await handler(pool, batch_size)
    except Exception as exc:
        logger.exception(
            "[retention.embeddings_orphan_prune] source=%s failed: %s",
            source_table, exc,
        )
        raise

    if deleted:
        logger.info(
            "[retention.embeddings_orphan_prune] source=%s pruned %d orphan(s)",
            source_table, deleted,
        )

    return {"deleted": deleted, "source_table": source_table, "batch_size": batch_size}


__all__ = ["embeddings_orphan_prune", "_HANDLERS"]
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_retention_embeddings_orphan_prune.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/integrations/handlers/retention_embeddings_orphan_prune.py \
        src/cofounder_agent/tests/unit/services/integrations/handlers/test_retention_embeddings_orphan_prune.py
git commit -m "feat(retention): embeddings_orphan_prune handler — JOIN-based orphan DELETE per source"
```

---

## Task 4: `embeddings_collapse` Handler

**Files:**

- Create: `src/cofounder_agent/services/integrations/handlers/retention_embeddings_collapse.py`
- Create: `src/cofounder_agent/tests/unit/services/integrations/handlers/test_retention_embeddings_collapse.py`

**Interfaces:**

- Consumes: `row["config"]["source_table"]` (str, required), `row["config"]["age_days"]` (int, default 90), `row["config"]["cluster_size"]` (int, default 8), `row["config"]["summary_provider"]` (`"ollama"` | `"template"`, default `"ollama"`), `site_config` (SiteConfig or None), `pool` (asyncpg pool).
- Produces: `{"deleted": int, "summarized": int, "source_table": str, "clusters": int}` — `deleted` key is read by `retention_runner._record_success`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/services/integrations/handlers/test_retention_embeddings_collapse.py`:

```python
"""Tests for the embeddings_collapse retention handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Pure-function unit tests (no DB)
# ---------------------------------------------------------------------------

def test_kmeans_cluster_basic():
    """Two distinct clusters of vectors produce two assignments."""
    from services.integrations.handlers.retention_embeddings_collapse import kmeans_cluster

    # Two tight clusters far apart
    cluster_a = [[1.0, 0.0, 0.0]] * 4
    cluster_b = [[0.0, 1.0, 0.0]] * 4
    vectors = cluster_a + cluster_b

    assignments, centroids = kmeans_cluster(vectors, k=2)

    assert len(assignments) == 8
    assert len(centroids) == 2
    # All cluster_a items share an assignment, all cluster_b items share another
    assert len(set(assignments[:4])) == 1
    assert len(set(assignments[4:])) == 1
    assert assignments[0] != assignments[4]


def test_kmeans_single_vector_returns_one_cluster():
    from services.integrations.handlers.retention_embeddings_collapse import kmeans_cluster

    assignments, centroids = kmeans_cluster([[1.0, 2.0]], k=1)
    assert assignments == [0]
    assert len(centroids) == 1


def test_build_summary_text_joins_previews():
    from services.integrations.handlers.retention_embeddings_collapse import build_summary_text

    result = build_summary_text(["Hello world", "foo bar"], chars_per_member=50)
    assert "Hello world" in result
    assert "foo bar" in result
    assert " | " in result


def test_build_summary_text_truncates():
    from services.integrations.handlers.retention_embeddings_collapse import build_summary_text

    long_text = "x" * 300
    result = build_summary_text([long_text], chars_per_member=100)
    assert len(result) < 150
    assert result.endswith("...")


# ---------------------------------------------------------------------------
# Handler integration tests (mocked DB)
# ---------------------------------------------------------------------------

def _make_pool(candidate_rows=None, execute_result="DELETE 0"):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=candidate_rows or [])
    conn.fetchrow = AsyncMock(return_value={"id": 99})
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock(return_value=execute_result)

    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


@pytest.mark.asyncio
async def test_no_eligible_rows_returns_zero():
    """Handler returns zeros when no rows older than age_days exist."""
    from services.integrations.handlers.retention_embeddings_collapse import embeddings_collapse

    pool = _make_pool(candidate_rows=[])
    row = {
        "name": "embeddings.collapse.claude_sessions",
        "config": {"source_table": "claude_sessions", "age_days": 90, "cluster_size": 8},
    }

    result = await embeddings_collapse(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 0
    assert result["summarized"] == 0
    assert result["clusters"] == 0
    assert result["source_table"] == "claude_sessions"


@pytest.mark.asyncio
async def test_missing_source_table_raises():
    from services.integrations.handlers.retention_embeddings_collapse import embeddings_collapse

    pool = _make_pool()
    row = {"name": "embeddings.collapse.x", "config": {}}

    with pytest.raises(ValueError, match="source_table"):
        await embeddings_collapse(None, site_config=None, row=row, pool=pool)


@pytest.mark.asyncio
async def test_collapse_uses_template_provider_when_configured():
    """summary_provider=template skips LLM call and uses joined-preview."""
    from services.integrations.handlers.retention_embeddings_collapse import embeddings_collapse

    vec_str = "[" + ",".join(["0.1"] * 768) + "]"
    fake_row = MagicMock()
    fake_row.__getitem__ = lambda self, k: {
        "id": 1, "source_id": "sid1", "text_preview": "hello",
        "metadata": {}, "embedding": vec_str,
        "embedding_model": "nomic-embed-text", "writer": "test", "origin_path": None,
    }[k]

    # Second candidate so clustering is possible (need >= 2 rows)
    fake_row2 = MagicMock()
    fake_row2.__getitem__ = lambda self, k: {
        "id": 2, "source_id": "sid2", "text_preview": "world",
        "metadata": {}, "embedding": vec_str,
        "embedding_model": "nomic-embed-text", "writer": "test", "origin_path": None,
    }[k]

    pool = _make_pool(candidate_rows=[fake_row, fake_row2], execute_result="DELETE 1")

    row = {
        "name": "embeddings.collapse.audit",
        "config": {
            "source_table": "audit",
            "age_days": 90,
            "cluster_size": 2,
            "summary_provider": "template",
        },
    }

    # With summary_provider=template, no LLM call should be made
    with patch(
        "services.integrations.handlers.retention_embeddings_collapse.build_summary_text_via_llm",
        new=AsyncMock(),
    ) as mock_llm:
        result = await embeddings_collapse(None, site_config=None, row=row, pool=pool)

    mock_llm.assert_not_called()
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_retention_embeddings_collapse.py -v
```

Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the handler**

Create `src/cofounder_agent/services/integrations/handlers/retention_embeddings_collapse.py`. This is a port of the logic from `services/jobs/collapse_old_embeddings.py` — the clustering and summary utilities are copied verbatim; only the entry-point changes from a Job to a `@register_handler` function that reads config from the row dict instead of app_settings.

```python
"""Handler: ``retention.embeddings_collapse``.

Clusters old embeddings per source_table, writes one summary row per cluster,
and deletes the originals inside a transaction. This compresses the tail of the
embeddings distribution for high-churn sources without destroying semantic signal.

Configuration comes entirely from the policy row's ``config`` jsonb:

  config.source_table     (str, required)
  config.age_days         (int, default 90) — rows older than this are candidates
  config.cluster_size     (int, default 8) — target rows per cluster
  config.summary_provider (str, "ollama"|"template", default "ollama")

No hardcoded allow/deny list. The absence of a retention_policies row for a
given source_table IS the policy — that source won't be collapsed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from services.integrations.registry import register_handler
from services.integrations.operator_notify import notify_operator
from services.llm_providers.dispatcher import resolve_tier_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vector utilities
# ---------------------------------------------------------------------------

def _parse_vector(raw: Any) -> list[float]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(v) for v in raw]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return []
    return [float(v) for v in text.split(",") if v.strip()]


def _l2_norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _normalize(v: Sequence[float]) -> list[float]:
    n = _l2_norm(v)
    if n == 0.0:
        return list(v)
    return [x / n for x in v]


def _mean(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            out[i] += v[i]
    return [x / len(vectors) for x in out]


def _sq_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(len(a)))


def _vector_literal(v: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


# ---------------------------------------------------------------------------
# K-means
# ---------------------------------------------------------------------------

def kmeans_cluster(
    vectors: Sequence[Sequence[float]],
    k: int,
    *,
    max_iters: int = 20,
    seed: int = 1337,
) -> tuple[list[int], list[list[float]]]:
    """Lloyd's k-means on L2-normalised vectors. Returns (assignments, centroids)."""
    if not vectors:
        return [], []
    n = len(vectors)
    k = max(1, min(k, n))
    normed = [_normalize(v) for v in vectors]
    rng = random.Random(seed)
    centroids: list[list[float]] = [list(normed[i]) for i in rng.sample(range(n), k)]
    assignments = [0] * n
    for _ in range(max_iters):
        changed = False
        for i, v in enumerate(normed):
            best_c = min(range(k), key=lambda c: _sq_distance(v, centroids[c]))
            if assignments[i] != best_c:
                assignments[i] = best_c
                changed = True
        new_centroids = []
        for c_idx in range(k):
            members = [normed[i] for i, a in enumerate(assignments) if a == c_idx]
            new_centroids.append(_normalize(_mean(members)) if members else centroids[c_idx])
        centroids = new_centroids
        if not changed:
            break
    return assignments, centroids


# ---------------------------------------------------------------------------
# Summary content
# ---------------------------------------------------------------------------

def build_summary_text(previews: Iterable[str], *, chars_per_member: int = 200) -> str:
    parts: list[str] = []
    for p in previews:
        if not p:
            continue
        snippet = p.strip().replace("\n", " ")
        if len(snippet) > chars_per_member:
            snippet = snippet[:chars_per_member].rstrip() + "..."
        parts.append(snippet)
    return " | ".join(parts)


_SUMMARY_PROMPT_KEY = "memory.collapse_old_embeddings.summary"
_DEFAULT_SUMMARY_PROMPT = (
    "You are compressing a cluster of older memories so the system "
    "remembers the gist without storing every detail. Below are "
    "{n} excerpts from the same source ({source_table}), each "
    "separated by '---'.\n\n"
    "Write a single paragraph (3-6 sentences) summarizing what these "
    "excerpts collectively say. Preserve specific names, dates, "
    "decisions, errors, and outcomes. Drop boilerplate, repetition, "
    "and verbose phrasing.\n\n"
    "Excerpts:\n{joined}\n\nSummary:\n"
)


def _resolve_summary_prompt() -> str:
    try:
        from services.prompt_manager import get_prompt_manager
        pm = get_prompt_manager()
        template = pm._fetch_from_langfuse(_SUMMARY_PROMPT_KEY)
        if template is None and _SUMMARY_PROMPT_KEY in pm.prompts:
            template = pm.prompts[_SUMMARY_PROMPT_KEY]["template"]
        if template:
            return template
    except Exception as exc:
        logger.debug("[retention.embeddings_collapse] prompt lookup failed: %s", exc)
    return _DEFAULT_SUMMARY_PROMPT


async def build_summary_text_via_llm(
    previews: Sequence[str],
    *,
    source_table: str,
    model: str,
    timeout_s: int,
    prompt_template: str | None = None,
) -> str | None:
    if not previews:
        return None
    pieces = [p.strip().replace("\r\n", "\n")[:800] for p in previews if p]
    if not pieces:
        return None
    template = prompt_template or _resolve_summary_prompt()
    prompt = template.format(n=len(pieces), source_table=source_table, joined="\n---\n".join(pieces))
    try:
        from services.ollama_client import OllamaClient
        client = OllamaClient(model=model)
        try:
            result = await client.generate(prompt=prompt, temperature=0.3, max_tokens=400, timeout=timeout_s)
            text = (result.get("text") or "").strip().strip('"')
            return text or None
        finally:
            with suppress(Exception):
                await client.close()
    except Exception as exc:
        logger.warning("[retention.embeddings_collapse] LLM summary failed (%s): %s", source_table, exc)
        return None


def _summary_source_id(source_table: str, source_ids: Sequence[str]) -> str:
    digest = hashlib.sha1(
        ("|".join(sorted(source_ids))).encode("utf-8"), usedforsecurity=False,
    ).hexdigest()[:16]
    return f"summary/{source_table}/{datetime.now(timezone.utc).strftime('%Y%m%d')}/{digest}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_summary_metadata(
    source_table: str, source_ids: Sequence[str], *, cluster_index: int, cluster_size: int, age_days: int,
) -> dict[str, Any]:
    return {
        "is_summary": True,
        "collapse_source": source_table,
        "collapsed_source_ids": list(source_ids),
        "collapsed_count": len(source_ids),
        "cluster_index": cluster_index,
        "cluster_size": cluster_size,
        "age_days_cutoff": age_days,
        "collapsed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

async def _write_summary_and_delete(
    pool: Any,
    *,
    source_table: str,
    summary_id: str,
    summary_text: str,
    metadata: dict[str, Any],
    centroid: Sequence[float],
    embedding_model: str,
    raw_row_ids: Sequence[int],
) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    content_hash = _content_hash(summary_text or summary_id)
    vector_str = _vector_literal(centroid)
    text_preview = (summary_text or summary_id)[:500]
    metadata_json = json.dumps(metadata)

    async with pool.acquire() as conn:
        async with conn.transaction():
            summary_row = await conn.fetchrow(
                """
                INSERT INTO embeddings (
                    source_table, source_id, content_hash, chunk_index,
                    text_preview, embedding_model, embedding, metadata,
                    writer, is_summary, created_at, updated_at
                )
                VALUES ($1, $2, $3, 0, $4, $5, $6::vector, $7::jsonb,
                        'collapse_handler', TRUE, $8, $8)
                ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
                DO UPDATE SET content_hash = EXCLUDED.content_hash,
                              embedding    = EXCLUDED.embedding,
                              metadata     = EXCLUDED.metadata,
                              text_preview = EXCLUDED.text_preview,
                              is_summary   = TRUE,
                              updated_at   = EXCLUDED.updated_at
                RETURNING id
                """,
                source_table, summary_id, content_hash, text_preview,
                embedding_model, vector_str, metadata_json, now,
            )
            if summary_row is None:
                raise RuntimeError("summary insert returned no row")
            verify = await conn.fetchval(
                "SELECT 1 FROM embeddings WHERE id = $1 AND is_summary = TRUE",
                summary_row["id"],
            )
            if not verify:
                raise RuntimeError("summary row verification failed — rolling back")

            deleted_count = 0
            for row_id in raw_row_ids:
                result = await conn.execute(
                    "DELETE FROM embeddings WHERE id = $1 AND is_summary = FALSE", row_id,
                )
                try:
                    deleted_count += int(result.split()[-1])
                except (ValueError, IndexError):
                    pass

    logger.info(
        "[retention.embeddings_collapse] source=%s wrote summary %s, deleted %d raw rows",
        source_table, summary_id, deleted_count,
    )
    return 1, deleted_count


# ---------------------------------------------------------------------------
# Handler entry point
# ---------------------------------------------------------------------------

async def _resolve_model(pool: Any) -> str:
    try:
        tier_model = await resolve_tier_model(pool, "budget")
    except (RuntimeError, ValueError, AttributeError) as exc:
        await notify_operator(
            f"embeddings_collapse: cost_tier='budget' has no model — "
            f"falling back to joined-preview: {exc}", critical=False,
        )
        raise RuntimeError("no summary model resolvable via budget tier") from exc
    return str(tier_model).removeprefix("ollama/")


@register_handler("retention", "embeddings_collapse")
async def embeddings_collapse(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Cluster + summarise old embeddings for one source_table."""
    if pool is None:
        raise RuntimeError("embeddings_collapse: pool unavailable")

    config = row.get("config") or {}
    source_table = config.get("source_table")
    if not source_table:
        raise ValueError("embeddings_collapse: config.source_table is required")

    age_days = int(config.get("age_days") or 90)
    cluster_size = int(config.get("cluster_size") or 8)
    summary_provider = str(config.get("summary_provider") or "ollama").lower()
    summary_timeout_s = int(config.get("summary_timeout_s") or 60)

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, age_days))

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, source_id, text_preview, metadata,
                   embedding, embedding_model, writer, origin_path
              FROM embeddings
             WHERE source_table = $1
               AND created_at < $2
               AND is_summary = FALSE
             ORDER BY created_at ASC
            """,
            source_table, cutoff,
        )

    if len(rows) < 2:
        return {"deleted": 0, "summarized": 0, "source_table": source_table, "clusters": 0}

    parsed = [
        {
            "id": r["id"],
            "source_id": r["source_id"],
            "text_preview": r["text_preview"] or "",
            "metadata": r["metadata"],
            "embedding": _parse_vector(r["embedding"]),
            "embedding_model": r["embedding_model"],
        }
        for r in rows
        if _parse_vector(r["embedding"])
    ]
    if len(parsed) < 2:
        return {"deleted": 0, "summarized": 0, "source_table": source_table, "clusters": 0}

    summary_model = ""
    if summary_provider == "ollama":
        try:
            summary_model = await _resolve_model(pool)
        except RuntimeError:
            summary_provider = "template"

    k = min(cluster_size, max(2, len(parsed) // 2))
    assignments, centroids = kmeans_cluster([p["embedding"] for p in parsed], k)

    clusters: dict[int, list[dict[str, Any]]] = {}
    for i, a in enumerate(assignments):
        clusters.setdefault(a, []).append(parsed[i])

    model_counts: dict[str, int] = {}
    for p in parsed:
        m = p.get("embedding_model") or ""
        model_counts[m] = model_counts.get(m, 0) + 1
    dominant_model = (max(model_counts, key=model_counts.get) if model_counts else "") or "nomic-embed-text"

    total_deleted = total_summaries = total_clusters = 0

    for cluster_idx, members in clusters.items():
        if len(members) < 2 or cluster_idx >= len(centroids):
            continue
        centroid = centroids[cluster_idx]
        if not centroid:
            continue

        member_ids = [m["source_id"] for m in members]
        row_ids = [m["id"] for m in members]
        summary_id = _summary_source_id(source_table, member_ids)

        previews = [m["text_preview"] for m in members]
        summary_text: str | None = None
        if summary_provider == "ollama":
            summary_text = await build_summary_text_via_llm(
                previews, source_table=source_table, model=summary_model, timeout_s=summary_timeout_s,
            )
        if not summary_text:
            summary_text = build_summary_text(previews)

        metadata = build_summary_metadata(
            source_table, member_ids,
            cluster_index=int(cluster_idx), cluster_size=len(members), age_days=age_days,
        )
        try:
            summaries_written, deleted = await _write_summary_and_delete(
                pool, source_table=source_table, summary_id=summary_id,
                summary_text=summary_text, metadata=metadata, centroid=centroid,
                embedding_model=dominant_model, raw_row_ids=row_ids,
            )
        except Exception as exc:
            logger.exception(
                "[retention.embeddings_collapse] cluster write failed source=%s cluster=%s: %s",
                source_table, cluster_idx, exc,
            )
            continue

        total_summaries += summaries_written
        total_deleted += deleted
        total_clusters += 1

    logger.info(
        "[retention.embeddings_collapse] source=%s: %d raw → %d summaries (%d clusters)",
        source_table, total_deleted, total_summaries, total_clusters,
    )
    return {
        "deleted": total_deleted,
        "summarized": total_summaries,
        "source_table": source_table,
        "clusters": total_clusters,
    }


__all__ = [
    "embeddings_collapse",
    "kmeans_cluster",
    "build_summary_text",
    "build_summary_text_via_llm",
    "build_summary_metadata",
]
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_retention_embeddings_collapse.py -v
```

Expected: all passed. The DB-integration tests mock pool/conn so no real DB needed.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/integrations/handlers/retention_embeddings_collapse.py \
        src/cofounder_agent/tests/unit/services/integrations/handlers/test_retention_embeddings_collapse.py
git commit -m "feat(retention): embeddings_collapse handler — k-means + LLM summarize per source_table"
```

---

## Task 5: Job Retirement

**Files:**

- Modify: `src/cofounder_agent/plugins/registry.py:749-761`
- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` (remove 2 dead settings)
- Delete: `src/cofounder_agent/services/jobs/prune_stale_embeddings.py`
- Delete: `src/cofounder_agent/services/jobs/prune_orphan_embeddings.py`
- Delete: `src/cofounder_agent/services/jobs/collapse_old_embeddings.py`
- Delete: `src/cofounder_agent/tests/unit/services/test_prune_stale_embeddings_job.py`
- Delete: `src/cofounder_agent/tests/unit/services/test_prune_orphan_embeddings_job.py`
- Delete: `src/cofounder_agent/tests/unit/services/jobs/test_collapse_old_embeddings_job.py`

**Interfaces:**

- Produces: `registry.get_jobs()` no longer includes `prune_stale_embeddings`, `prune_orphan_embeddings`, or `collapse_old_embeddings`.

- [ ] **Step 1: Remove the three jobs from `_SAMPLES`**

In `plugins/registry.py`, find these three lines (around line 759–761):

```python
        ("jobs", "services.jobs.prune_orphan_embeddings", "PruneOrphanEmbeddingsJob"),
        ("jobs", "services.jobs.prune_stale_embeddings", "PruneStaleEmbeddingsJob"),
        ("jobs", "services.jobs.collapse_old_embeddings", "CollapseOldEmbeddingsJob"),
```

Delete all three. Also update the comment block above them (around line 749–752) that references these jobs — remove or rewrite it to note the jobs were retired.

- [ ] **Step 2: Remove dead settings from baseline seeds**

In `0000_baseline.seeds.sql`, find and delete these two lines:

```sql
INSERT INTO app_settings ... VALUES ('embedding_collapse_cluster_size', ...
INSERT INTO app_settings ... VALUES ('embedding_collapse_source_tables', ...
```

These settings were only consumed by `CollapseOldEmbeddingsJob` (now retired). `embeddings_collapse` handler reads cluster_size and source_table from the policy row's `config` jsonb instead.

- [ ] **Step 3: Delete the job files**

```bash
rm src/cofounder_agent/services/jobs/prune_stale_embeddings.py
rm src/cofounder_agent/services/jobs/prune_orphan_embeddings.py
rm src/cofounder_agent/services/jobs/collapse_old_embeddings.py
```

- [ ] **Step 4: Delete the job test files**

```bash
rm src/cofounder_agent/tests/unit/services/test_prune_stale_embeddings_job.py
rm src/cofounder_agent/tests/unit/services/test_prune_orphan_embeddings_job.py
rm src/cofounder_agent/tests/unit/services/jobs/test_collapse_old_embeddings_job.py
```

- [ ] **Step 5: Verify no imports of retired jobs**

```bash
cd src/cofounder_agent && grep -r "prune_stale_embeddings\|prune_orphan_embeddings\|collapse_old_embeddings" . --include="*.py" -l
```

Expected: no output. If files appear, update them to remove the import.

- [ ] **Step 6: Run the full test suite — no failures**

```bash
cd src/cofounder_agent && poetry run pytest tests/unit/ -q --tb=short
```

Expected: 0 failures, 0 errors. The count will be lower than before (the deleted test files are gone).

- [ ] **Step 7: Smoke test migration chain still valid**

```bash
cd src/cofounder_agent && python ../../scripts/ci/migrations_smoke.py
```

Expected: exits 0.

- [ ] **Step 8: Commit**

```bash
git add -u src/cofounder_agent/plugins/registry.py \
           src/cofounder_agent/services/migrations/0000_baseline.seeds.sql
git rm src/cofounder_agent/services/jobs/prune_stale_embeddings.py \
       src/cofounder_agent/services/jobs/prune_orphan_embeddings.py \
       src/cofounder_agent/services/jobs/collapse_old_embeddings.py \
       src/cofounder_agent/tests/unit/services/test_prune_stale_embeddings_job.py \
       src/cofounder_agent/tests/unit/services/test_prune_orphan_embeddings_job.py \
       src/cofounder_agent/tests/unit/services/jobs/test_collapse_old_embeddings_job.py
git commit -m "feat(retention): retire prune_stale/orphan/collapse jobs — replaced by retention_policies handlers"
```
