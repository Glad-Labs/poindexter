# Embedding Retention Consolidation

**Date:** 2026-06-23  
**Status:** Approved  
**Scope:** Fold `prune_stale_embeddings`, `prune_orphan_embeddings`, and `collapse_old_embeddings` plugin jobs into the `retention_policies` declarative framework as first-class handlers, retiring the three standalone jobs.

---

## Motivation

Two independent systems currently manage embedding hygiene on the same table:

1. **Three plugin jobs** (`_SAMPLES` in `plugins/registry.py`) — TTL prune, orphan prune, collapse — each with their own app_settings config keys, schedules, and observability paths.
2. **`retention_policies` rows** — the canonical framework since #699 (2026-06-17) when `RetentionJanitor` was emptied. The three embeddings TTL rows exist here but race with the TTL job (job fires first and wins, so the rows always report 0 deletes). The rows also encode conflicting TTLs.

The framework is already the right abstraction. The jobs are stragglers. This consolidation establishes a single canonical path: **operator controls embedding retention entirely through `retention_policies` rows**, with no parallel job path.

---

## Architecture

Five coordinated changes:

| Layer          | Change                                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------ |
| Schema         | Add `min_interval_hours REAL NULL` to `retention_policies`                                                   |
| Runner         | 5-line skip-if-not-due check per policy before dispatch                                                      |
| Handlers       | Two new: `embeddings_orphan_prune` + `embeddings_collapse`; TTL reuses existing `ttl_prune` via `filter_sql` |
| Policy rows    | Fix 3 existing malformed TTL rows; seed 6 new rows (3 orphan + 3 collapse)                                   |
| Job retirement | Remove 3 job classes from `_SAMPLES`; delete job files; remove superseded app_settings defaults              |

All configuration — TTL values, age thresholds, cluster sizes, batch limits, cadence — lives in the policy row's columns (`ttl_days`, `min_interval_hours`) and `config` jsonb. Nothing is hardcoded in handler logic except one safety invariant: the per-source JOIN SQL in the orphan handler is schema plumbing (tied to each table's key type) and belongs in code, not in a user-controlled JSON field.

**The absence of a row is the policy.** If there is no `embeddings.collapse.posts` row, posts never get collapsed. Operators add rows to opt in to new behaviour, disable rows to pause it.

---

## Schema

### Migration: `20260623_HHMMSS_add_retention_min_interval_hours.py`

```sql
ALTER TABLE retention_policies
  ADD COLUMN IF NOT EXISTS min_interval_hours REAL;
```

`NULL` = run every cycle (backward-compatible default). `168` = weekly, `24` = daily, etc.

No index needed — the column is evaluated once per row during the runner's in-memory loop, not in a query predicate.

---

## Runner Change (`services/integrations/retention_runner.py`)

Add a skip check at the top of the `for row in rows` loop in `run_all()`:

```python
# Top-of-file import (alongside existing datetime imports if any):
from datetime import datetime, timedelta, timezone

# Inside run_all(), at the top of the `for row in rows` loop:
min_h = row.get("min_interval_hours")
last_ran = row.get("last_run_at")
if min_h and last_ran:
    next_due = last_ran.replace(tzinfo=timezone.utc) + timedelta(hours=float(min_h))
    if datetime.now(timezone.utc) < next_due:
        logger.debug("[retention-runner] %s: not due for %.0fh", name, min_h)
        continue
```

`RunRetentionJob` keeps its 6-hour APScheduler cadence. The throttle lives in the runner — the job wakes frequently, individual policies decide whether to act.

---

## Handlers

### Existing: `ttl_prune` (no changes)

The generic `ttl_prune` handler already supports `filter_sql`. The embedding TTL rows use it to scope deletes to one `source_table` and exclude summary rows:

```
filter_sql = "source_table = 'claude_sessions' AND COALESCE(is_summary, FALSE) = FALSE"
```

No new handler needed for the TTL case.

### New: `embeddings_orphan_prune`

**File:** `src/cofounder_agent/services/integrations/handlers/retention_embeddings_orphan_prune.py`

**Dispatch key:** `embeddings_orphan_prune`

**Row config keys:**

| key            | type | required | description                                                  |
| -------------- | ---- | -------- | ------------------------------------------------------------ |
| `source_table` | str  | yes      | Which embeddings source to clean (`posts`, `audit`, `brain`) |
| `batch_size`   | int  | no       | Max deletes per run (default 1000)                           |

**Logic:** Dispatches to an internal `_HANDLERS` dict keyed by `source_table`. Each handler runs a single JOIN-based DELETE matching the source table's key semantics:

- `posts` — `source_id` is UUID, joins `posts.id`
- `audit` — `source_id` is integer-as-text, joins `audit_log.id`
- `brain` — `source_id` is compound `"brain_decisions/<id>"`, parses prefix then joins `brain_decisions.id`

Sources with no handler (`claude_sessions`, `memory`, `samples`) raise `ValueError` at dispatch time with a clear message — TTL pruning is the only mechanism for those sources.

Adding a new source requires a code PR to add the JOIN SQL, plus a new DB row. This is correct: the JOIN semantics are facts about the DB schema, not operator-tunable parameters.

**Returns:** `{"deleted": int, "source_table": str, "batch_size": int}`

### New: `embeddings_collapse`

**File:** `src/cofounder_agent/services/integrations/handlers/retention_embeddings_collapse.py`

**Dispatch key:** `embeddings_collapse`

**Row config keys:**

| key                | type | required | description                                           |
| ------------------ | ---- | -------- | ----------------------------------------------------- |
| `source_table`     | str  | yes      | Which embeddings source to collapse                   |
| `age_days`         | int  | no       | Min age of rows to include in clustering (default 90) |
| `cluster_size`     | int  | no       | Target rows per cluster for k-means (default 8)       |
| `summary_provider` | str  | no       | `"ollama"` or `"template"` (default `"ollama"`)       |

**Logic:**

1. Fetch embeddings rows for `source_table` older than `age_days` where `is_summary = FALSE`
2. Run k-means clustering (`cluster_size` rows per cluster target)
3. For each cluster: generate LLM summary via `resolve_tier_model(pool, "budget")` + `OllamaClient(site_config=site_config)`, write summary row with `is_summary=TRUE`, delete original rows — all in a single `conn.transaction()` for rollback safety
4. Skip the run if no eligible rows exist (idempotent no-op)

**No `_NEVER_COLLAPSE` guard.** The declarative plane is the policy. If there is no row for a source, that source is not collapsed. Operators who want to prevent collapse of a source simply don't add the row (or disable the row).

**Returns:** `{"deleted": int, "summarized": int, "source_table": str, "clusters": int}`

---

## Policy Rows

All rows seeded `enabled = FALSE`. Operator enables deliberately after verifying deployment is healthy.

### Updated TTL rows (fix malformed `config`/`metadata` JSON + canonical TTLs)

| name                                   | handler     | ttl_days | filter_sql                                                                 | min_interval_hours |
| -------------------------------------- | ----------- | -------- | -------------------------------------------------------------------------- | ------------------ |
| `embeddings.ttl_prune.claude_sessions` | `ttl_prune` | 30       | `source_table = 'claude_sessions' AND COALESCE(is_summary, FALSE) = FALSE` | NULL               |
| `embeddings.ttl_prune.brain`           | `ttl_prune` | 365      | `source_table = 'brain' AND COALESCE(is_summary, FALSE) = FALSE`           | NULL               |
| `embeddings.ttl_prune.audit`           | `ttl_prune` | 90       | `source_table = 'audit' AND COALESCE(is_summary, FALSE) = FALSE`           | NULL               |

**Canonical TTL resolution:** `claude_sessions` was 21d (job) vs 30d (row) — **30d wins**. `brain` was 365d (job) vs 180d (row) — **365d wins**. `audit` was 90d (both agree) — **90d**.

### New orphan rows

| name                            | handler                   | config                                          | min_interval_hours |
| ------------------------------- | ------------------------- | ----------------------------------------------- | ------------------ |
| `embeddings.orphan_prune.posts` | `embeddings_orphan_prune` | `{"source_table": "posts", "batch_size": 1000}` | NULL               |
| `embeddings.orphan_prune.audit` | `embeddings_orphan_prune` | `{"source_table": "audit", "batch_size": 1000}` | NULL               |
| `embeddings.orphan_prune.brain` | `embeddings_orphan_prune` | `{"source_table": "brain", "batch_size": 1000}` | NULL               |

### New collapse rows

| name                                  | handler               | config                                                                                                 | min_interval_hours |
| ------------------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------ | ------------------ |
| `embeddings.collapse.claude_sessions` | `embeddings_collapse` | `{"source_table": "claude_sessions", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}` | 168                |
| `embeddings.collapse.brain`           | `embeddings_collapse` | `{"source_table": "brain", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}`           | 168                |
| `embeddings.collapse.audit`           | `embeddings_collapse` | `{"source_table": "audit", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama"}`           | 168                |

### Seed targets

- **`0000_baseline.seeds.sql`** — update the 3 existing TTL row `INSERT` statements: correct `config`/`metadata` from `'"{}"'::jsonb` to `'{}'::jsonb`, set canonical `ttl_days`, set `filter_sql`. Add 6 new rows. (The schema migration for `min_interval_hours` runs before seeds, so the column exists when the seeds file runs.)
- **Prod convergence migration** — a separate timestamped migration is needed alongside the schema migration to fix the 3 already-inserted malformed rows on prod. The baseline `INSERT ... ON CONFLICT DO NOTHING` won't touch them. The migration runs `UPDATE retention_policies SET config = '{}', metadata = '{}', ttl_days = <N>, filter_sql = '...' WHERE name IN (...)` for the three rows.

---

## Job Retirement

Once handlers and policy rows are deployed:

1. Remove `PruneStaleEmbeddingsJob`, `PruneOrphanEmbeddingsJob`, `CollapseOldEmbeddingsJob` from `_SAMPLES` in `plugins/registry.py`
2. Delete `services/jobs/prune_stale_embeddings.py`, `prune_orphan_embeddings.py`, `collapse_old_embeddings.py`
3. Remove superseded defaults from `services/settings_defaults.py`:
   - `embedding_retention_days.*` prefix (replaced by `ttl_days` on rows)
   - `embedding_orphan_check.*` prefix (replaced by row `enabled` flag + `config.batch_size`)
   - `embedding_collapse_*` keys (replaced by row `config` jsonb and `enabled` flag)

---

## Operator Runbook (post-deploy)

```bash
# Enable TTL prune (safe first — cheap deletes, data already covered by retiring job)
poindexter retention enable embeddings.ttl_prune.claude_sessions
poindexter retention enable embeddings.ttl_prune.brain
poindexter retention enable embeddings.ttl_prune.audit

# Enable orphan prune per source when confident
poindexter retention enable embeddings.orphan_prune.posts
poindexter retention enable embeddings.orphan_prune.audit
poindexter retention enable embeddings.orphan_prune.brain

# Enable collapse (LLM-heavy; first run no-ops if data < age_days)
poindexter retention enable embeddings.collapse.claude_sessions
poindexter retention enable embeddings.collapse.brain
poindexter retention enable embeddings.collapse.audit

# Tune a value without code deploy (direct DB or declarative_config_service upsert)
# UPDATE retention_policies SET config = '{"source_table":"brain","age_days":180,...}' WHERE name = 'embeddings.collapse.brain';
```

---

## Testing

- **Unit tests for each new handler** — mock pool + site_config, verify SQL dispatched and return shape
- **Unit tests for runner skip logic** — row with `min_interval_hours=1` and `last_run_at=now()` is skipped; row with `last_run_at=8_days_ago` is not skipped
- **Migration smoke test** — `python scripts/ci/migrations_smoke.py` covers the new column
- **Contract tests** — handler return dict matches `PolicyResult` fields (`deleted`, `summarized`)
- Existing `ttl_prune` tests cover the TTL rows unchanged

---

## In Scope: CLI additions

Add `enable` and `disable` subcommands to the existing `poindexter retention` CLI group:

```
poindexter retention enable <name>   # SET enabled = TRUE WHERE name = <name>
poindexter retention disable <name>  # SET enabled = FALSE WHERE name = <name>
```

These are the only new CLI commands. Config tuning (updating `config` jsonb) uses direct DB access for now.

## Out of Scope

- `poindexter retention config` CLI subcommand
- Grafana panels for the new handler types (existing `RunRetentionJob` metrics still surface via `job_run_state`)
- Additional `embeddings_orphan_prune` sources beyond posts/audit/brain
