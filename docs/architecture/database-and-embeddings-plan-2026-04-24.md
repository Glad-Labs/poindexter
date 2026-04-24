# Database + Embeddings Plan

**Date:** 2026-04-24
**Status:** Phase 4 framework split to [GH-110](https://github.com/Glad-Labs/poindexter/issues/110) and deferred. Phases 1/3/5 proceeding.
**Covers:** GH-27 (feedback-loop tables), GH-57 (schema audit), GH-106 (embedding retention)
**Scope:** Everything in the PostgreSQL database that needs attention before we add more features

## Operator decisions (2026-04-24)

| Decision                     | Choice                                                                                                                                                     |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TTL values (30 / 90 / 180 d) | Approved as proposed                                                                                                                                       |
| Temporal summarization       | On-demand + threshold trigger (not weekly batch)                                                                                                           |
| `gpu_metrics` specifically   | **Deferred:** standardized retention-job framework split to [GH-110](https://github.com/Glad-Labs/poindexter/issues/110). Not blocking embeddings cleanup. |
| `experiments` table          | Leave dormant (GH-96)                                                                                                                                      |

---

## Situation (real numbers as of 2026-04-24)

### Embeddings table — 126 MB, 13,105 rows

126 MB total breaks down as:

| Component                            | Size   | Note                                                                |
| ------------------------------------ | ------ | ------------------------------------------------------------------- |
| Raw table                            | 15 MB  | The row metadata                                                    |
| `idx_embeddings_hnsw`                | 51 MB  | pgvector HNSW index — sized right for 13k vectors                   |
| `idx_embeddings_source`              | 2 MB   | 1.75M scans — heavily used                                          |
| Other btrees + PK                    | 5 MB   |                                                                     |
| `idx_embeddings_collapse_candidates` | 0.6 MB | **0 scans — dead index from a retention system that never shipped** |

Row breakdown by source:

| source_table    | rows  | oldest     | newest     | growth pattern                                                          |
| --------------- | ----- | ---------- | ---------- | ----------------------------------------------------------------------- |
| claude_sessions | 7,095 | 2026-04-20 | 2026-04-24 | **one-time backfill 6,369 rows on Apr 20**, steady-state ~200/day since |
| brain           | 2,607 | 2026-04-16 | 2026-04-24 | ~300/day when active                                                    |
| audit           | 1,990 | 2026-04-02 | 2026-04-24 | ~90/day                                                                 |
| issues          | 741   | 2026-04-02 | 2026-04-16 | done — Gitea issues, static                                             |
| memory          | 470   | 2026-04-01 | 2026-04-22 | ~20/day                                                                 |
| posts           | 201   | 2026-04-01 | 2026-04-24 | 1-3/day                                                                 |
| samples         | 1     | —          | —          | explicit seed                                                           |

**Critical insight:** the 7,095 claude_sessions count is dominated by one backfill day (6,369 on Apr 20). Steady state is ~200/day. Today's count looks alarming but it's heavily front-loaded.

### Other tables worth retention

| Table               | Size   | Rows   | Growth | Notes                                                |
| ------------------- | ------ | ------ | ------ | ---------------------------------------------------- |
| `pipeline_versions` | 11 MB  | 684    | slow   | ~6 KB/row (content snapshots per stage) — OK for now |
| `audit_log`         | 4.4 MB | 9,257  | fast   | ~250/day, append-only, no retention                  |
| `gpu_metrics`       | 3.1 MB | 20,388 | fast   | ~900/day at 1-min sampling → 330k/year               |
| `brain_decisions`   | 3.2 MB | 6,923  | medium | Append-only, no retention                            |
| `action_task`       | 5.6 MB | 876    | slow   | Gitea CI job log                                     |

Everything else (`posts`, `content_tasks`, `app_settings`, etc.) is small and operator-controlled.

### Feedback-loop tables (GH-27) — 3 of 7 populated

| Table               | Rows  | Status                                          |
| ------------------- | ----- | ----------------------------------------------- |
| `content_revisions` | 402   | ✅ cross_model_qa writes each rewrite iteration |
| `model_performance` | 1,381 | ✅ stages log model + score + time per run      |
| `post_performance`  | 42    | ✅ sync_page_views rolls up daily               |
| `experiments`       | 0     | ❌ tracked in GH-96                             |
| `external_metrics`  | 0     | ❌ no Singer tap (blocked on GH-103)            |
| `revenue_events`    | 0     | ❌ needs Lemon Squeezy webhook                  |
| `subscriber_events` | 0     | ❌ needs Resend webhook + newsletter send-log   |

---

## Proposed strategy

The core principle: **compress, don't just delete**. Matt's explicit ask — and the right call for memory systems that might need historical context later.

### 1. Embeddings — four consolidation mechanisms

Each mechanism applies to a different source class.

#### A. Source-specific TTL (simplest, lowest risk)

Per-source retention in `app_settings`:

| Source            | TTL      | Why                                                       |
| ----------------- | -------- | --------------------------------------------------------- |
| `claude_sessions` | 30 days  | Ephemeral working notes — recent context matters, old rot |
| `audit`           | 90 days  | Long enough for quarterly reviews, not forever            |
| `brain`           | 180 days | System reasoning — slower decay                           |
| `issues`          | no TTL   | Authoritative external reference                          |
| `memory`          | no TTL   | Operator-curated — never touch without operator approval  |
| `posts`           | no TTL   | Core business content                                     |

Implementation: `services/jobs/collapse_old_embeddings.py` already exists as a registered job (scheduled weekly). Needs the TTL table driver + actual prune logic.

**Expected reduction:** 7,095 → ~600 claude_sessions, 1,990 → ~1,500 audit. Total 13,105 → ~7,500 rows. One-shot.

#### B. Temporal summarization (the "compress" path Matt asked about)

For `claude_sessions` specifically — group N consecutive chunks into time windows and summarize each window into a single distilled-memory embedding.

- Group `claude_sessions` by 6-hour windows (or by conversation-boundary markers if detectable)
- Concatenate the chunk text per window
- LLM-summarize (`ollama/gemma3:27b`, cheap, local)
- Embed the summary
- Insert into `embeddings` as a new row with `source_table='claude_sessions_summary'` + `metadata.window_start` / `window_end` / `source_ids` (list of collapsed rows)
- Delete the originals

**Compression ratio:** 10-20:1. The Apr 20 backfill (6,369 rows) covering ~18 operating hours = ~3 windows × 6 hours, so 6,369 → ~3 summaries. Then steady-state 200/day → ~4 summaries/day.

**Loss:** you can't grep for the exact phrase anymore. Semantic retrieval still works ("what have we decided about X") because the summary captures intent.

**When to run:** on-demand, triggered by threshold — not a weekly batch.

- Default threshold: `claude_sessions` rows older than 7 days exceeding **1,000**. Tunable via `app_settings.embedding_summarize_threshold_rows` + `embedding_summarize_threshold_days`.
- Runner checks the threshold on the scheduler tick; only executes when tripped.
- Manual trigger available via `poindexter embeddings summarize --source=claude_sessions` for operator-initiated runs.
- Per-run cap (default 500 source rows) so the first few passes stay observable.

#### C. Orphan cleanup (zero-risk cleanup)

Embeddings whose source row was deleted (task cancelled, post archived) still linger. Separate nightly job:

```sql
DELETE FROM embeddings e
WHERE NOT EXISTS (
  SELECT 1 FROM {source_table_name} src
  WHERE src.id::text = e.source_id
);
```

Driven per source_table. Cheap index scans. **Cannot** run for `audit` / `brain` / `claude_sessions` / `issues` (those have no strict FK — the source records live in different places or are ephemeral). Can run for `posts` and `memory`.

#### D. Dimensional compression (later, only if needed)

pgvector's default HNSW at 768 dimensions × 13k rows = 51 MB index. At 100k rows that's 400 MB. At some point (probably 500k+) we'll want to reduce dimensions via PCA or switch to a quantized index (pgvector `halfvec` halves the size).

Not needed today. Mark as a follow-up for when we cross 100k rows.

### 2. Schema hygiene (GH-57 scope)

#### Indexes to drop

- **`idx_embeddings_collapse_candidates`** — 584 kB, 0 scans, dead placeholder. Drop.

#### Indexes to add

Based on query patterns observed today:

- `embeddings (created_at)` — used by retention jobs but not indexed. Add.
- Optional: `content_tasks (status, created_at DESC)` — dashboard queries hit this pattern a lot.

#### Retention for append-only tables

| Table               | Current rows | Proposed retention                         | Why                                         |
| ------------------- | ------------ | ------------------------------------------ | ------------------------------------------- |
| `audit_log`         | 9,257        | 90 days                                    | Aligns with audit embeddings                |
| `gpu_metrics`       | 20,388       | 30 days (1-min) + 365 days (1-hour rollup) | Classic downsampling                        |
| `brain_decisions`   | 6,923        | 90 days                                    | Decisions mostly matter for recent context  |
| `cost_logs`         | 5,125        | 365 days                                   | Monthly cost reports need a year of history |
| `pipeline_versions` | 684          | keep all                                   | Low volume, useful for debugging            |

#### FK + constraint audit

- `media_assets.task_id` is `character varying(255)` — should probably be UUID + FK to `content_tasks.task_id`. Today it's a loose string. See GH-108.
- `embeddings.source_id` is also loose text — no FK to any source table. This is intentional (it points at multiple source tables) but means orphan cleanup has to be per-source.
- Several tables have `tenant_id` / `site_id` nullable and mostly NULL. Either enforce them (multi-tenancy) or drop them (we're single-operator).

#### Vacuum / analyze

- Nothing alarming in pg_stat_user_tables. Postgres autovacuum is keeping up. No manual intervention needed.

### 3. Feedback-loop tables (GH-27) — what to wire next

Three tables are truly blocked on external factors:

- **`external_metrics`** — needs a Singer-style tap ingestion (blocked on GH-103). Can't wire until the framework lands.
- **`revenue_events`** — needs actual paying customers. Wire the Lemon Squeezy webhook handler now so the table starts populating on first sale. ~2h work.
- **`subscriber_events`** — needs Resend webhook (email open/click/bounce). ~3h work.
- **`experiments`** — tracked in GH-96 as its own scope; A/B variant creation is non-trivial.

**My recommendation for GH-27:**

- Ship the Lemon Squeezy + Resend webhook handlers now (they're bounded, low-risk, and unlock the tables as traffic materializes).
- Defer `external_metrics` until GH-103 (Singer) lands — otherwise we're writing a custom ingest that'll be thrown away.
- Keep `experiments` in GH-96 — it's a separate product decision, not a DB wiring decision.

---

## Phased execution plan

Each phase is independent. Pick any order.

### Phase 1 — Quick wins (1-2 hours)

- [ ] Drop `idx_embeddings_collapse_candidates` (dead)
- [ ] Add `idx_embeddings_created_at` (for retention queries)
- [ ] Verify the `collapse_old_embeddings` job implementation — if it's a placeholder, replace with TTL prune logic
- [ ] Set `embedding_retention_days.*` app_settings with the proposed TTLs

After Phase 1: cleaner indexes, retention policy is policy-as-data (tunable without code changes).

### Phase 2 — TTL-based pruning (1 hour)

- [ ] Run prune job once manually: 13,105 → expected ~7,500
- [ ] Verify on a staging DB snapshot first so we can roll back
- [ ] Schedule weekly via PluginScheduler

After Phase 2: 40% reduction in embeddings rows. Primarily targets claude_sessions.

### Phase 3 — Temporal summarization for claude_sessions (half day)

- [ ] New job `services/jobs/summarize_old_sessions.py`
- [ ] Groups claude_sessions older than 7 days into 6-hour windows
- [ ] LLM-summarizes each window via Ollama (`gemma3:27b`, cheap)
- [ ] Inserts summary embedding with `source_table='claude_sessions_summary'`
- [ ] Deletes collapsed originals only after summary is verified embedded
- [ ] Unit test covers round-trip: 100 sessions → 1 summary → still retrievable by topic

After Phase 3: 7,095 → ~500 claude_sessions rows (approx). The 6,369-row Apr 20 backfill dump becomes 3 summaries.

### Phase 4 — Standardized retention-job framework (deferred, tracked in [GH-110](https://github.com/Glad-Labs/poindexter/issues/110))

**Split to its own issue on 2026-04-24.** Not blocking embeddings cleanup — proceed with Phase 1 and Phase 5 independently.

The rest of this section is retained for reference but execution is deferred.

---

**Rescoped from "add a gpu_metrics prune job" to "build the framework so every tap/source declares retention as data, not code."**

Problem being solved: each new tap today requires a hand-rolled prune job. That doesn't scale and breaks Matt's DB-first-configuration rule. A tap should ship with its retention policy declared alongside, and enabling/disabling retention should be a config flip.

**Shape:**

- New table `retention_policies`:
  - `source_name` (text, PK) — e.g. `gpu_metrics`, `audit_log`, `embeddings.claude_sessions`
  - `table_name` (text) — actual PG table
  - `filter_sql` (text, nullable) — optional WHERE fragment for per-source filtering inside shared tables (the embeddings case)
  - `age_column` (text) — which column to compare against
  - `ttl_days` (int, nullable) — null means no TTL
  - `downsample_rule` (jsonb, nullable) — e.g. `{"keep_raw_days": 30, "rollup_table": "gpu_metrics_hourly", "rollup_interval": "1 hour"}`
  - `summarize_handler` (text, nullable) — name of a registered summarization plugin (e.g. `claude_sessions_temporal`)
  - `enabled` (bool, default false)
  - `last_run_at`, `last_run_deleted_count`, `last_run_summarized_count`
- New job `services/jobs/retention_runner.py`:
  - Single scheduled job (daily) that walks `retention_policies WHERE enabled=true`
  - For each policy: TTL prune, downsample, or summarize based on which columns are set
  - Logs to `audit_log` per policy run, writes back `last_run_*` fields
- New handler registry `services/retention/handlers.py`:
  - `ttl_prune_handler` (generic DELETE WHERE age > TTL)
  - `downsample_handler` (keep raw N days + rollup beyond)
  - `temporal_summarize_handler` (Phase 3 logic, reused here)

**Seed policies on migrate:**

| Source                       | ttl_days | downsample_rule                                | summarize_handler          | enabled |
| ---------------------------- | -------- | ---------------------------------------------- | -------------------------- | ------- |
| `embeddings.claude_sessions` | 30       | —                                              | `claude_sessions_temporal` | false   |
| `embeddings.audit`           | 90       | —                                              | —                          | false   |
| `embeddings.brain`           | 180      | —                                              | —                          | false   |
| `embeddings.issues`          | —        | —                                              | —                          | false   |
| `embeddings.memory`          | —        | —                                              | —                          | false   |
| `embeddings.posts`           | —        | —                                              | —                          | false   |
| `audit_log`                  | 90       | —                                              | —                          | false   |
| `gpu_metrics`                | —        | `{keep_raw_days:30, rollup_interval:"1 hour"}` | —                          | false   |
| `brain_decisions`            | 90       | —                                              | —                          | false   |

All seeded as `enabled=false`. Flip them on individually via `UPDATE retention_policies SET enabled=true WHERE source_name=...` so every activation is observable and reversible.

**Adding a new tap later:** the tap's migration declares its `retention_policies` row. Zero code change in the runner. Meets "standardized jobs so enabling/disabling retention for a new tap is trivial."

After Phase 4: one runner, one policy table, N sources all declared as data. Phase 2's one-shot TTL prune is retired in favor of the runner's declarative version.

### Phase 5 — Feedback-loop webhook handlers (half day)

- [ ] Lemon Squeezy webhook → `revenue_events`
- [ ] Resend webhook → `subscriber_events`
- [ ] Verify webhook signing for both (HMAC-SHA256)
- [ ] Grafana panels: daily revenue, monthly recurring, newsletter open rate

After Phase 5: 5 of 7 feedback-loop tables populating. Only `external_metrics` and `experiments` left, both with external dependencies.

---

## Guardrails

**Do not touch without explicit operator approval:**

- `memory` source_table embeddings — operator-curated knowledge spine
- `posts` table or its embeddings — core business content
- `app_settings` rows — everything lives here, ad-hoc deletes break the system
- Any table flagged in backup scripts as "critical" (see `scripts/db-backup.ps1`)

**Test before you run retention in production:**

1. Take a fresh `pg_dump` snapshot
2. Restore into a throwaway DB
3. Run the retention job
4. Verify counts + that `memory` source is untouched
5. Check a sample of embeddings still return sensible `search_memory()` results
6. Only then flip the production job

**Every retention job MUST log before-counts + after-counts + kept/dropped ratio.** Silent deletion is not acceptable.

---

## Execution order

**Proceeding now:**

1. **Phase 1** — drop dead index + add `idx_embeddings_created_at`. ~1 hour. No data touched; safe.
2. **Phase 5** — Lemon Squeezy + Resend webhook handlers. Independent of retention work. ~half day.

**Deferred:**

3. **Phase 4** — standardized retention-job framework → [GH-110](https://github.com/Glad-Labs/poindexter/issues/110). When this lands it will replace what was originally drafted as Phases 2 and 3 (one-shot prune + bespoke summarizer) with declarative policies.
4. **Phase 2 / Phase 3 (embedding cleanup itself)** — deferred until Phase 4 ships. 13k rows / 126 MB is not urgent; the right move is to wait for the framework rather than hand-roll a prune now that we know we'd throw it away.

**Rationale:** storage isn't the pain. Repeated one-off scripts are the pain. Do the low-risk index work now, build the feedback-loop webhook handlers, and let the retention runner be the first (and only) retention job we write.
