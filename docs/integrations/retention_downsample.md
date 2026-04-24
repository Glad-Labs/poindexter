# Handler: `retention.downsample`

Keeps recent raw rows, aggregates older rows into a coarser rollup table, then deletes the raw rows the rollup now represents. Classic time-series pattern — useful for `gpu_metrics`, future `pipeline_metrics`, any high-frequency append-only table where you want long history but not long-at-full-fidelity history.

## Row configuration

```
name:                slug, e.g. "gpu_metrics"
handler_name:        downsample
table_name:          raw table to downsample from
age_column:          timestamp column (default: created_at; gpu_metrics uses sampled_at)
downsample_rule:     JSONB — see shape below
enabled:             false until operator flips on
config.dry_run:      optional (default false)
```

### `downsample_rule` JSONB shape

```json
{
  "keep_raw_days": 30,
  "rollup_table": "gpu_metrics_hourly",
  "rollup_interval": "1 hour",
  "aggregations": [
    { "col": "utilization_pct", "fn": "avg", "as": "avg_utilization_pct" },
    { "col": "memory_used_mb", "fn": "avg", "as": "avg_memory_used_mb" },
    { "col": "power_watts", "fn": "max", "as": "peak_power_watts" }
  ]
}
```

Allowed aggregation functions: `avg`, `min`, `max`, `sum`, `count`. Additional functions require extending the whitelist in `services/integrations/handlers/retention_downsample.py`.

Allowed intervals: a positive integer and a unit from `second`/`minute`/`hour`/`day`/`week`/`month`/`year` (singular or plural). E.g. `"30 minutes"`, `"1 day"`.

## Precondition: rollup table must exist

The handler does **not** auto-create the rollup table. Every rollup schema decision (indexes, PK on `bucket_start`, column types) is a deliberate choice the operator should make. Create the table explicitly before enabling the policy:

```sql
CREATE TABLE gpu_metrics_hourly (
    bucket_start          timestamptz PRIMARY KEY,  -- MUST be unique for ON CONFLICT
    avg_utilization_pct   double precision,
    avg_memory_used_mb    double precision,
    peak_power_watts      double precision
);
CREATE INDEX idx_gpu_metrics_hourly_bucket ON gpu_metrics_hourly (bucket_start DESC);
```

Column names must match the `as` aliases in `aggregations`. The handler's INSERT assumes `bucket_start` is the unique constraint for `ON CONFLICT DO NOTHING` — so overlapping invocations don't double-insert.

## Operator runbook

### Enabling `gpu_metrics` (seeded, disabled by default)

```
# 1. Create the rollup table per the schema above.
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain <<'SQL'
CREATE TABLE IF NOT EXISTS gpu_metrics_hourly (
    bucket_start          timestamptz PRIMARY KEY,
    avg_utilization_pct   double precision,
    avg_memory_used_mb    double precision,
    peak_power_watts      double precision
);
SQL

# 2. Dry-run to see how many rows would be affected.
poindexter retention run gpu_metrics --dry-run

# 3. Enable and run.
poindexter retention enable gpu_metrics
poindexter retention run gpu_metrics

# 4. Verify in Grafana that last_run_deleted and last_run_duration_ms populate.
```

### How the handler works

1. **Count** raw rows older than `keep_raw_days`. If zero, short-circuit.
2. **Insert** aggregated buckets into `rollup_table` using `date_trunc('hour', age_column)` as the bucket key. `ON CONFLICT (bucket_start) DO NOTHING` makes overlapping runs safe.
3. **Delete** raw rows older than `keep_raw_days`.

Failure between steps 2 and 3 is rare but possible (DB hiccup, manual kill). If it happens, the rollup table already has the bucket from step 2; re-running the policy will `ON CONFLICT DO NOTHING` on the existing bucket and proceed to step 3's delete. Idempotent.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Sibling handler: `retention.ttl_prune`
- GH-110 (retention framework issue)
