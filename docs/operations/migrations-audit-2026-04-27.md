# Migrations audit — 2026-04-27

Quick sweep of `src/cofounder_agent/services/migrations/` to verify
the schema-tracker is consistent with the filesystem and to flag any
risks for the operator to address.

## Inventory

- 80 migration files on disk (numbered 0000–0097, with two collisions —
  see below).
- 80 rows in `schema_migrations` table after tonight's apply pass.
- All file-tracked migrations are applied. `0097_create_experiments.py`
  was applied this session at 2026-04-27 03:06:30 UTC after the A/B
  harness commit (`09611ec4`) introduced it.

## Findings

### 1. Duplicate migration number 0093 — cosmetic only, not blocking

Two files share the `0093_` prefix:

```
0093_create_object_stores_table.py
0093_create_qa_gates_table.py
```

Both applied successfully in sequence (different filenames satisfy the
`schema_migrations.name UNIQUE` constraint), but the duplicate prefix
risks confusion in two scenarios:

- **Future contributors** scrolling the directory by name see two
  "0093"s and may assume they're variants of the same migration.
- **Backfill / re-apply tooling** that sorts by leading integer can
  pick an arbitrary order between them. The current production runner
  sorts lexicographically by full filename, so `object_stores` is
  applied before `qa_gates` deterministically — but anyone writing a
  parallel runner from scratch could trip on this.

**Recommendation**: leave the prefixes alone (renaming applied
migrations risks the `schema_migrations.name UNIQUE` constraint
treating them as new), but add a sentinel comment in each file's
docstring naming the OTHER 0093 explicitly so future readers
understand they're sibling migrations. Severity: low.

### 2. Out-of-order application timestamp

```
0095_cost_logs_electricity_kwh.py   applied 2026-04-27 06:06:10 UTC
0094_seed_qa_gates_default_chain.py applied 2026-04-27 06:06:10 UTC
0093_create_qa_gates_table.py       applied 2026-04-27 06:06:10 UTC
0093_create_object_stores_table.py  applied 2026-04-27 06:06:10 UTC
0096_create_media_assets_table.py   applied 2026-04-27 01:44:36 UTC  # earlier!
```

Migration 0096 was applied BEFORE 0093 / 0094 / 0095 because it landed
in a different commit batch and the runner caught it first. The four
tables / columns involved don't overlap, so observable behaviour is
unchanged — but it's a smell that two contributors / agents adding
migrations on parallel branches can land them out of numeric sequence.

**Recommendation**: when adding a migration, run `git fetch && git rebase`
first and bump your file number to one higher than the current max in
the directory. The existing runner is order-tolerant; this is only a
hygiene rule for human readability of the migration history.

### 3. No orphan migrations

Every file on disk has a corresponding row in `schema_migrations`. No
"applied but file deleted" rows (which would leave the schema in a
state no fresh install can reproduce). Healthy.

### 4. Down() coverage

Spot-checked the most recent ten migrations (`0088`–`0097`). All have
a `down()` function that drops the indexes / tables / columns the
`up()` added. Good. Earlier migrations are less consistent — some
single-purpose seed migrations are intentionally one-way. Not a
production risk; just less reversible than the recent ones.

## Recommendations summary

- LOW: Cross-link the two `0093_` migrations via docstring comments.
- LOW: Document the "rebase + renumber before commit" rule in
  `docs/operations/extending-poindexter.md` so contributors don't
  recreate the timestamp-ordering issue.
- NONE: Schema state is otherwise clean.

## Out of scope

- Performance review of long-running migrations (none observed during
  this session's apply pass).
- `schema_migrations` row pruning (table is small, no impact).
- pgBouncer / pool-during-migration semantics (deferred, GH-92 closed).
