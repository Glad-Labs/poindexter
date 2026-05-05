# Database Migrations

**Last Updated:** 2026-05-05
**Owner:** Glad-Labs/poindexter#378
**Runner:** `src/cofounder_agent/services/migrations/__init__.py`

This is the canonical reference for adding, naming, and running
migrations in Poindexter. If you are adding a migration, read sections
[Naming Convention](#naming-convention) and
[Adding a Migration](#adding-a-migration) before you write code.

## TL;DR

- **New migrations use a UTC timestamp prefix:** `YYYYMMDD_HHMMSS_<slug>.py`
- Old `0xxx_<slug>.py` files stay as-is (renaming would invalidate the
  `schema_migrations` rows of every operator's local DB).
- The runner sorts lexically — timestamp prefixes (starting with `2`)
  always sort after the legacy integer prefixes (starting with `0`),
  so the relative order is preserved.
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

| Option                              | Why we passed                                             |
| ----------------------------------- | --------------------------------------------------------- |
| `services/migrations/.next` lockfile | Brittle — every PR conflicts on the lockfile.             |
| Pre-commit hook validating sequence  | Catches collisions but doesn't prevent them; still needs a tie-breaker. |
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

| Element     | Format        | Source                     | Example       |
| ----------- | ------------- | -------------------------- | ------------- |
| Date        | `YYYYMMDD`    | UTC                        | `20260505`    |
| Separator   | `_`           | literal                    | `_`           |
| Time        | `HHMMSS`      | UTC, 24-hour               | `081530`      |
| Separator   | `_`           | literal                    | `_`           |
| Slug        | `[a-z0-9_]+`  | what the migration does    | `add_x_table` |
| Extension   | `.py`         | Python module              | `.py`         |

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
template at `src/cofounder_agent/services/migrations/`. The slug is
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

Tear down with `docker rm -f pg-test`. CI runs the same script
against a fresh `pgvector/pgvector:pg16` service container on every
PR — see `.github/workflows/migrations-smoke.yml`.

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
2. Lists `services/migrations/*.py` excluding `__init__.py` and sorts
   lexically by filename.
3. For each file: skip if filename is already in `schema_migrations`,
   otherwise `importlib.util` it and call `up(pool)` or
   `run_migration(conn)`.
4. Inserts the filename into `schema_migrations` ON success only.
5. **Per-file failures do not halt the batch** — errors are logged
   and the runner moves on. Returns `False` if any failure occurred.

Implication: **a failing migration does not block subsequent ones.**
This is intentional — a transient SQL error on a seed migration
shouldn't prevent a critical schema migration further down the list
from applying. The trade-off is that a migration that depends on a
PRIOR migration's columns can fail silently if the prior migration
errored. The CI smoke test catches that pattern (the row-count
assertion would fail).

---

## Common patterns

### Seed an `app_settings` row

```python
async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
              (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            "my_setting_key",
            "my_default_value",
            "my_category",
            "Description visible in the settings UI.",
        )
```

`ON CONFLICT (key) DO NOTHING` — never blow away an operator's tuned
value. Use `ON CONFLICT (key) DO UPDATE` only when the description /
category changed and you want the canonical text reflected.

Per `feedback_db_first_config`: every tunable goes in `app_settings`,
not as a hardcoded constant. Per `feedback_no_silent_defaults`:
required settings should fail loudly at lookup time when missing —
seed them with sane defaults so that lookup never errors on a fresh
DB.

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

### Drop a deprecated table

```python
async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS my_table CASCADE")
```

`CASCADE` only when you've audited the FK references. The audit
should be in the module docstring — what was the table for, why is
it being dropped, what replaces it.

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
- **Don't** assume a PRIOR migration applied successfully. The runner
  continues on failure; defensive `IF NOT EXISTS` / `IF EXISTS` is
  cheap insurance.

---

## Related docs

- [Fresh DB setup walkthrough](fresh-db-setup.md) — end-to-end test
  of the full chain on a clean slate.
- [`docs/operations/extending-poindexter.md`](extending-poindexter.md)
  — broader plugin / extension guide.
- [`docs/operations/migrations-audit-2026-04-27.md`](migrations-audit-2026-04-27.md)
  — historical audit (pre-#378). Some recommendations there are
  superseded by this doc.
- [Glad-Labs/poindexter#378](https://github.com/Glad-Labs/poindexter/issues/378)
  — the source RFC for this convention.
