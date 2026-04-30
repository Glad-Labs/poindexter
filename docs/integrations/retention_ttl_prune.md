# Handler: `retention.ttl_prune`

Generic TTL-based row deleter. Drops rows from `row.table_name` where `row.age_column` is older than `row.ttl_days` days. Optional `row.filter_sql` narrows the scope so the same table can host multiple policies (e.g. embeddings partitioned by `source_table`).

## Row configuration

```
name:                stable slug, e.g. "embeddings.claude_sessions", "audit_log"
handler_name:        ttl_prune
table_name:          target PostgreSQL table
filter_sql:          optional WHERE fragment (null = no extra filtering)
age_column:          timestamp column to compare against (default: created_at)
ttl_days:            delete rows older than this many days
enabled:             false until operator flips on
config.batch_size:   optional (default 10000) — per-batch DELETE LIMIT
config.dry_run:      optional (default false) — count only, no delete
```

## Safety

- **Batched deletes** prevent long exclusive locks on large tables. The handler loops with `ctid`-keyed `DELETE` statements until a batch returns fewer rows than `batch_size`.
- **Identifier validation** rejects anything non-alphanumeric in `table_name` / `age_column` at runtime. String interpolation is used to build SQL (asyncpg can't parameterize identifiers), but a malformed seed migration can't slip SQL injection through.
- **Dry run** gives a count-only preview before enabling.

## Operator runbook

### Enabling a seeded policy

```
# 1. Verify what would be deleted
poindexter retention run embeddings.claude_sessions --dry-run

# 2. Enable and run
poindexter retention enable embeddings.claude_sessions
poindexter retention run embeddings.claude_sessions

# 3. Check Grafana -> Integration Health -> Retention policies
#    last_run_deleted should match the dry-run count
```

### Creating a new policy

```sql
INSERT INTO retention_policies
  (name, handler_name, table_name, filter_sql, age_column, ttl_days, enabled, metadata)
VALUES (
  'my_new_policy',
  'ttl_prune',
  'some_table',
  NULL,                -- or "column = 'value'"
  'created_at',
  60,
  FALSE,
  jsonb_build_object('description', 'Why this policy exists')
);
```

Then:

```
poindexter retention run my_new_policy --dry-run
poindexter retention enable my_new_policy
```

### Seeded policies (all disabled)

| Name                         | Table             | Filter                             | TTL      |
| ---------------------------- | ----------------- | ---------------------------------- | -------- |
| `embeddings.claude_sessions` | `embeddings`      | `source_table = 'claude_sessions'` | 30 days  |
| `embeddings.audit`           | `embeddings`      | `source_table = 'audit'`           | 90 days  |
| `embeddings.brain`           | `embeddings`      | `source_table = 'brain'`           | 180 days |
| `audit_log`                  | `audit_log`       | —                                  | 90 days  |
| `brain_decisions`            | `brain_decisions` | —                                  | 90 days  |

### Disabling

```
poindexter retention disable my_policy
```

## Expected outcome

Running the seeded `embeddings.claude_sessions` policy once against the current DB (13,105 total embeddings, 7,095 from claude_sessions, most from the Apr 20 backfill) will delete roughly 6,369 rows — the Apr 20 backfill dump — and leave the ~200/day steady-state untouched.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- DB plan: `docs/architecture/database-and-embeddings-plan-2026-04-24.md`
- Sibling handlers: `retention.downsample`, `retention.temporal_summarize` (deferred)
- GH-110 (retention framework issue)
- GH-106 (original embeddings retention issue)
