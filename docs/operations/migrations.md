# Database Migrations

**Last Updated:** 2026-09-28
**Owner:** Glad-Labs/poindexter#378
**Runner:** `src/cofounder_agent/poindexter/services/migrations/__init__.py`

This is the canonical reference for adding, naming, and running
migrations in Poindexter. If you are adding a migration, read sections
[Naming Convention](#naming-convention) and
[Adding a Migration](#adding-a-migration) before you write code.

## TL;DR

- **The migration history is squashed into `0000_baseline.py`** (+
  `0000_baseline.schema.sql` + `0000_baseline.seeds.sql`). The baseline has
  been re-rolled as the tree grew — most recently the **Phase G squash
  (2026-07-11)**, which folds in every migration through `20260711_*` (the
  Phase F baseline + 42 post-baseline files; Phase F itself superseded the
  2026-06-06 Phase E #1194, 2026-05-29 Phase D, and the original 2026-05-08
  squashes); the docstring lists what each generation absorbed. That single
  file captures the whole pre-squash schema and seeds. The runner sorts
  lexically so `0000_baseline.py` runs first (`0` < `2`); on Matt's prod
  where the schema is already in place every `CREATE TABLE IF NOT EXISTS`
  no-ops and every seed `INSERT ... ON CONFLICT DO NOTHING` no-ops, leaving
  only the row recording the baseline as applied. New migrations use the
  timestamp convention.
- **A table that exists before the baseline runs is converged, not skipped
  (poindexter#1097).** On a compose-first install the brain seeds
  `app_settings` before the worker has migrated anything, so the table already
  exists when the baseline's `CREATE TABLE IF NOT EXISTS` reaches it. The
  brain's table used to have 8 of the 14 columns, and baseline statement #401
  (`idx_app_settings_is_active`) failed on every such install, from May until
  2026-09-28. Now, while `schema_migrations` is still empty, the baseline adds
  the declared columns and CHECK constraints an existing table lacks and
  applies its declared `NOT NULL`s. It never drops, retypes, re-defaults or
  rewrites anything: a NULL where `NOT NULL` is declared fails the run and names
  the column. Once any migration is recorded it converges nothing, because a
  missing column was then dropped on purpose, so prod never takes this path.
  The brain creates the declared shape itself (`seed_loader.APP_SETTINGS_DDL`).
  A squash that changes `app_settings` must update that copy too;
  `tests/unit/brain/test_seed_loader_app_settings_ddl.py` fails until it does.
- **A squash can't drop a column — so a destructive migration may have to
  survive until prod catches up.**
  A baseline only ever `CREATE TABLE IF NOT EXISTS`, which no-ops on installs
  that already have the table (prod), so a baseline that merely _omits_ a
  column would leave prod schema-drifted from fresh installs. Phase F therefore
  had to keep one surviving post-baseline migration —
  `20260622_200222_drop_pipeline_tasks_category.py`
  (`ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS category`). **Phase G
  (2026-07-11) folded it away**: once prod is verified current on the drop
  (every install has applied it — checked against `schema_migrations`), the new
  baseline simply omits the column with no survivor, so Phase G is true
  baseline-only. Orphan `schema_migrations` rows for the deleted files are
  harmless — the runner skips by filename and never reconciles the reverse
  direction.
- **A squash regenerates the seeds from a LIVE DB — so it re-imports operator
  drift. Reconcile before merging.**
  `0000_baseline.seeds.sql` is regenerated fold-forward from a real database,
  so it captures whatever prod held that day — including values that have since
  drifted from `settings_defaults.py` (operator-tuned rates, stale defaults the
  code has moved past). After any squash, run
  `python scripts/ci/settings_seed_value_drift_lint.py` and reconcile every key
  it reports **before** merging the squash PR. The lint reds precisely because
  the regen re-imported drift — **that is the guard working, not a bug in the
  squash.** Resolve each key toward the reference default (set the same value in
  both `settings_defaults.py` and `0000_baseline.seeds.sql`), or — only for a
  `poindexter/brain/seed_app_settings.json` value that is a deliberate free-tier choice —
  add a `TIER_POLICY` entry with a reason. This is the CI check that would
  otherwise let the squash silently re-break the 30 keys reconciled in the
  2026-07-17 pass (and the single key of poindexter#819 before it).
- **New migrations use a UTC timestamp prefix:** `YYYYMMDD_HHMMSS_<slug>.py`
- `0000_baseline.py` is the only legacy 4-digit file left in tree —
  renaming it would invalidate the `schema_migrations` rows of every
  operator's local DB.
- The runner sorts lexically — timestamp prefixes (starting with `2`)
  always sort after the baseline (starting with `0`), so the relative
  order is preserved.
- Generate one with `python scripts/new-migration.py "<slug>"`.
- The CI lint script (`scripts/ci/migrations_lint.py`) catches
  collisions, missing `up()`/`run_migration()`, and prefix-format
  drift before a PR can merge.

---

## Why timestamp prefixes

Until 2026-05-05, every migration used a `0xxx` integer prefix. The
contributor's job was to pick `max(existing) + 1`. That works for a
single contributor working serially. It fails the moment two PRs are
in flight at the same time:

> Tonight three migrations were authored in parallel agents (#370,
> #373, #371) — all three claimed `0158` because each agent
> independently checked `main`, saw `0157` was the highest, and
> reserved `0158`.
> — Glad-Labs/poindexter#378

The runner is filename-keyed and per-file idempotent, so the collision
is mechanically harmless — both files apply, both rows land in
`schema_migrations`. But it breaks the convention, makes the directory
hard to read, and there's no guarantee the next collision will be as
benign (e.g., two migrations that BOTH try to add the same column).

**Timestamp prefixes make collisions essentially impossible** —
two contributors would have to start their migration files in the
same second on the same day. The runner's lexical sort still produces
a deterministic, chronological order without any coordination.

We considered alternatives:

| Option                               | Why we passed                                                                                                                              |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `services/migrations/.next` lockfile | Brittle — every PR conflicts on the lockfile.                                                                                              |
| Pre-commit hook validating sequence  | Catches collisions but doesn't prevent them; still needs a tie-breaker.                                                                    |
| Hard-cutover renaming all 0xxx files | Invalidates `schema_migrations` rows on every operator's local DB. Migration to rewrite those rows is feasible but high-risk for low gain. |

Soft adoption (keep old, new uses timestamp) wins on risk-adjusted
return: zero existing-DB churn, eliminates future collisions,
mechanical lex-sort still works.

---

## Naming Convention

### New migrations (after 2026-05-05)

```
YYYYMMDD_HHMMSS_<lowercase_slug>.py
```

| Element   | Format       | Source                  | Example       |
| --------- | ------------ | ----------------------- | ------------- |
| Date      | `YYYYMMDD`   | UTC                     | `20260505`    |
| Separator | `_`          | literal                 | `_`           |
| Time      | `HHMMSS`     | UTC, 24-hour            | `081530`      |
| Separator | `_`          | literal                 | `_`           |
| Slug      | `[a-z0-9_]+` | what the migration does | `add_x_table` |
| Extension | `.py`        | Python module           | `.py`         |

Full example:

```
20260505_081530_add_writer_self_review_settings.py
```

The slug should describe the change (`add_X_column`, `seed_Y`,
`drop_Z_table`, `backfill_W`). Don't put the issue number in the slug
— put it in the module docstring instead.

### Legacy migrations (before 2026-05-05)

```
NNNN_<lowercase_slug>.py
```

These remain untouched. The two `0158_*.py` files documented in #378
are accepted as a historical wart — they ran cleanly on every fresh
DB, every PR-CI smoke run, and every operator install.

### Sort order across both schemes

Lexical sort places legacy `0xxx_` files BEFORE timestamp `2xxx_`
files because `0` < `2` as a character. Within each scheme, sort is
chronological. Verified by `scripts/ci/migrations_lint.py`.

```
0157_drop_prompt_templates_table.py
0158_seed_langfuse_tracing_setting.py
0158_task_failure_alert_dedup.py
0159_seed_template_runner_postgres_checkpointer.py
20260505_081530_add_writer_self_review_settings.py
20260505_092212_seed_my_other_thing.py
```

---

## Adding a Migration

### 1. Generate the file

```bash
python scripts/new-migration.py "add writer self review settings"
```

This stamps the current UTC timestamp into the filename and writes a
template at `src/cofounder_agent/poindexter/services/migrations/`. The slug is
auto-lowercased and spaces become underscores.

### 2. Fill in `up()` (and `down()` when the change is reversible)

The runner supports two interfaces — pick whichever fits:

```python
# Convention A — pool-based (preferred for new migrations)
async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE foo ADD COLUMN bar TEXT")

async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE foo DROP COLUMN bar")
```

```python
# Convention B — connection-based (legacy; still supported)
async def run_migration(conn) -> None:
    await conn.execute("ALTER TABLE foo ADD COLUMN bar TEXT")

async def rollback_migration(conn) -> None:
    await conn.execute("ALTER TABLE foo DROP COLUMN bar")
```

The runner checks for `up()` first, then falls back to
`run_migration()`. If neither is present, the file is logged and
skipped (won't be recorded in `schema_migrations`).

### 3. Make it idempotent

Migrations are recorded by filename in `schema_migrations` and only
run once per database. But individual statements should still tolerate
re-execution — `IF NOT EXISTS`, `ON CONFLICT DO NOTHING`, etc. — so a
partial failure (the row failed to insert into `schema_migrations`
after the schema change applied) doesn't break the next attempt.

### 4. Run the smoke test locally

```bash
docker run -d --name pg-test -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=poindexter_test \
    -p 15999:5432 pgvector/pgvector:pg16

DATABASE_URL=postgres://postgres:postgres@localhost:15999/poindexter_test \
    python scripts/ci/migrations_smoke.py
```

The smoke test asserts:

- All discovered migration files apply without error.
- Each file has a corresponding `schema_migrations` row.
- No orphan rows (a row for a file that doesn't exist).

That is the `poindexter setup` order. A compose-first install runs the brain's
boot seed before any migration, so run that order too, on a second empty
database, and compare it with the first:

```bash
docker exec pg-test createdb -U postgres poindexter_test_brain_first

DATABASE_URL=postgres://postgres:postgres@localhost:15999/poindexter_test_brain_first \
    python scripts/ci/migrations_smoke.py --brain-first \
        --compare-schema-to postgres://postgres:postgres@localhost:15999/poindexter_test
```

`--compare-schema-to` fails on any difference between the two schemas:
columns (with position, type, nullability and default), constraints, indexes,
triggers, sequences, views, functions and types.

Tear down with `docker rm -f pg-test`. CI runs both orders against a
fresh `pgvector/pgvector:pg16` service container on every PR — see
`.github/workflows/migrations-smoke.yml`.

### 5. Run the lint script

```bash
python scripts/ci/migrations_lint.py
```

Lint catches:

- Two NEW migrations sharing the same timestamp prefix (extremely
  unlikely but possible if you regenerate within the same second).
- A new migration using the legacy `0xxx_` integer prefix instead of
  the timestamp format.
- Missing `up()` AND `run_migration()` (the runner would silently
  skip the file).

---

## Runner mechanics

`services.migrations.run_migrations(database_service)` does the
following on every worker startup:

1. Ensures `schema_migrations (id, name, applied_at)` exists.
2. Lists `services/migrations/*.py` excluding `__init__.py` and any
   `_`-prefixed helper, and sorts lexically by filename.
3. For each file: skip if filename is already in `schema_migrations`,
   otherwise `importlib.util` it and call `up(pool)` or
   `run_migration(conn)`. The module is exec'd from its path and never
   registered in `sys.modules`, so anything that looks its own module up there
   raises at import. `@dataclass` does, which is why `0000_baseline.py` uses a
   `NamedTuple`.
4. Inserts the filename into `schema_migrations` ON success only.
5. **The first failure halts the batch** (fail closed, #697): the runner logs
   it and re-raises, and the worker's startup fails with it. The failed file is
   not recorded, so the next start retries it.

Implication: **a failing migration blocks every migration after it, and the
worker with it.** That is deliberate. Running later migrations against a
schema an earlier one failed to build produces errors that point at the wrong
file. It also means a baseline that cannot apply keeps the worker in a restart
loop, as poindexter#1097 did on every compose-first fresh install.

---

## Common patterns

### Add an `app_settings` key (not a migration)

A new key is a `DEFAULTS` entry in
`src/cofounder_agent/poindexter/services/settings_defaults.py`, with a
`METADATA` entry for its owner and value type
(`scripts/suggest_settings_metadata.py` derives one). `seed_all_defaults` applies
`DEFAULTS` on every boot with `INSERT ... ON CONFLICT (key) DO NOTHING`, so a
fresh install gets the row and an operator's tuned value is never overwritten.
Don't seed a setting from a migration: it runs once and is never re-evaluated,
so its value drifts from what fresh installs get. If `0000_baseline.seeds.sql`
also seeds the key, the two values must match
(`scripts/ci/settings_seed_value_drift_lint.py`).

Changing an existing key's default does not rewrite rows that already exist,
since `DO NOTHING` skips them. Pair the change with a migration that updates
only rows still holding the old default. The precedent is
`20260928_130429_drop_org_from_research_tier1_domains.py`.

Per `feedback_db_first_config`: every tunable goes in `app_settings`,
not as a hardcoded constant. Per `feedback_no_silent_defaults`:
required settings should fail loudly at lookup time when missing —
seed them with sane defaults so that lookup never errors on a fresh
DB.

### Retire an orphaned `app_settings` key

1. **Prove there is no reader, from the code.** Grep every tracked file:
   Python, SQL, console JS, MCP servers, Grafana panels.
   `app_settings.last_read_at` can back that up but can't settle it either
   way. Raw SQL that skips `record_read`, `site_config.all()` snapshots and
   processes that never flush all leave a live key NULL, and so does a read
   that happens only on request: `rate_limit_podcast_generate_per_ip` is live
   and still NULL on prod, because its limit is read only when a request
   reaches the route. A stamp never expires, so a key whose last reader was
   deleted keeps looking read.
2. **Remove it from every seed source in the same commit:** `DEFAULTS` and
   `METADATA` in `settings_defaults.py`, `0000_baseline.seeds.sql`, and
   `poindexter/brain/seed_app_settings.json`. All three insert
   `ON CONFLICT DO NOTHING`, so a copy left in any one re-inserts the row
   after the migration deletes it. `scripts/ci/settings_seed_drift_lint.py`
   fails CI when a source still carries a key a migration deletes. It checks
   `DEFAULTS` too since 2026-09-28. Three more places name keys and no lint
   reads them, so check them by hand: the `settings_categories.py` override
   map, `scripts/settings_defaults_extract.json` (the #379 extract), and
   other hand-kept key lists in code. `StartupManager`'s
   `_NON_OLLAMA_MODEL_KEYS` is checked now:
   `tests/unit/services/test_ollama_model_validator.py` fails while it names a
   key that no seed source carries.
3. **DELETE by a literal key list, never a pattern.** A `LIKE` sweep takes
   out live keys that share the name, as `rate_limit_%` would:

   ```python
   ORPHANED_KEYS = ("my_dead_key",)  # literal; a deletion-named tuple the lint can see


   async def up(pool) -> None:
       async with pool.acquire() as conn:
           deleted = await conn.fetch(
               "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
               list(ORPHANED_KEYS),
           )
   ```

   `down()` restores the row with its seeded value. The docstring records
   what used to read the key, what removed that reader, and what the check
   found on prod.

4. **Pin it with a regression test.** It asserts the key is absent from each
   seed source and that no module reads it, and it keeps a floor of live
   look-alike keys a sweep must not touch. Precedents:
   `test_drop_orphan_short_video_post_publish_delay.py` (a key seeded by the
   baseline), `test_drop_orphan_rate_limit_video_generate.py` (a key seeded
   only by `DEFAULTS`) and `test_drop_orphan_image_model.py` (a key whose live
   twin, `image_generation_model`, shares its prefix; it scans for the key as
   a quoted literal, so prose that names the retired key is not a reader), all
   in `tests/unit/services/migrations/`.

### Add a column safely

```python
async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE my_table "
            "ADD COLUMN IF NOT EXISTS new_col TEXT DEFAULT ''"
        )
```

`IF NOT EXISTS` and an explicit `DEFAULT` keep this safe to re-run
and avoid the row-rewrite cost on existing data.

### Reseed a `graph_def`

Every active `pipeline_templates` row is stamped per node with its atom's
`contract_fingerprint()`. Change an atom's `AtomMeta` contract (a new
`FieldSpec` input, a changed `requires`/`produces`) or a graph's nodes/edges,
and the stored stamps go stale: `assert_graph_def_current` refuses the row at
load and that whole lane halts (poindexter#1876; glad-labs-stack#3928 halted
every Stage-2 video render on 2026-09-22). **The boot self-heal does not fix
this** — `ensure_active_graph_defs_stamped` restamps only rows carrying no
fingerprint at all — so the change needs a reseed migration:

```python
from poindexter.services.graph_def_reseed import apply_reseeds

# (slug, new_version, spec module, spec attr, graph signature the reseed brings the row to)
_RESEEDS = (
    ("media_pipeline", 7, "poindexter.services.media_pipeline_spec",
     "MEDIA_PIPELINE_GRAPH_DEF", "0d1f3d11b475"),
)


async def up(pool) -> None:
    await apply_reseeds(pool, _RESEEDS, log_prefix="reseed_media_pipeline_v7")
```

`apply_reseeds` writes the RAW in-tree spec (importable in the smoke env,
which has no atom registry) and restamps it through the self-heal where the
registry imports, else on the worker's next boot. Get the fifth field — and
each slug's next version — from

```bash
REGEN_GRAPH_DEF_FP=1 poetry run pytest tests/unit/services/test_graph_def_reseed_gate.py::test__print_graph_signatures -s
```

CI (`test_graph_def_reseed_gate.py`) requires the **newest** `_RESEEDS` entry
for every active graph to declare the signature the live registry produces,
so a contract change cannot merge without the migration prod needs; a
migration that writes `graph_def` without a signature-declaring `_RESEEDS`
fails the same gate. `_RESEEDS` must be a literal (the gate reads it with
`ast.literal_eval`, never by importing the migration). Refresh the per-atom
snapshot too (`graph_def_contract_fingerprints.json`, see
`test_graph_def_contract_freshness.py`) — it names _which_ atom drifted, but
on its own it can be made green without touching prod, which is exactly how
#1876 and #3928 shipped.

### Drop a deprecated table

```python
async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS my_table CASCADE")
```

`CASCADE` only when you've audited the FK references. The audit
should be in the module docstring — what was the table for, why is
it being dropped, what replaces it.

Prefer the plain form when the audit finds nothing that depends on the
table. An unexpected view then fails the migration and names itself,
where `CASCADE` would drop it without a word. The first table drop after
the baseline is
`20260928_231739_drop_the_sync_metrics_table_orphaned_by_the_sync_service_removal.py`.
It counts the rows first so the log says what was removed, leaves
`0000_baseline.schema.sql` alone (a frozen snapshot: a fresh install
creates the table there and drops it here), and its `down()` recreates
the structure only. `test_drop_sync_metrics_table.py` in
`tests/unit/services/migrations/` pins those choices, compares `down()`
with the baseline's column definitions, and fails if a backend module or
dashboard names the table again.

---

## Anti-patterns

- **Don't** edit a migration after it's merged. Land a new one.
- **Don't** rename a migration after it's merged. The
  `schema_migrations` row references the old name; renaming creates
  an orphan-row vs. unrunnable-file mismatch.
- **Don't** put `IF NOT EXISTS` on the `schema_migrations` insert
  — that's the runner's job and it does it correctly. Your migration
  body shouldn't touch the tracker table.
- **Don't** use Python literals for tunable behaviour. Read from
  `app_settings` via `SiteConfig.get()` at runtime.
- **Don't** change an atom's contract or a graph_def's nodes/edges and
  "fix" CI by regenerating `graph_def_contract_fingerprints.json` alone —
  prod's stored row still fails at load. Ship the reseed migration
  (pattern above); the reseed gate stays red until you do.
- **Don't** assume a PRIOR migration applied successfully. The runner
  continues on failure; defensive `IF NOT EXISTS` / `IF EXISTS` is
  cheap insurance.

---

## Related docs

- [Fresh DB setup walkthrough](fresh-db-setup.md) — end-to-end test
  of the full chain on a clean slate.
- [`docs/operations/extending-poindexter.md`](extending-poindexter.md)
  — broader plugin / extension guide.
- `docs/operations/migrations-audit-2026-04-27.md` — historical audit
  (pre-#378), since removed. Some recommendations there were superseded
  by this doc.
- [Glad-Labs/poindexter#378](https://github.com/Glad-Labs/poindexter/issues/378)
  — the source RFC for this convention.
