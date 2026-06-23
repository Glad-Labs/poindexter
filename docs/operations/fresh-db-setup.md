# Fresh DB Setup — End-to-End Walkthrough

**Last Updated:** 2026-06-22
**Verified Against:** Glad-Labs/poindexter Phase F squash (0000_baseline.py + 20260622_200222_drop_pipeline_tasks_category.py)
**Verifier:** dispatched code-writing agent (#378), refreshed 2026-05-23; Phase F counts updated 2026-06-22

This doc walks through standing up Poindexter against a fresh,
empty Postgres database. It's the canonical reference for:

- A new operator install (laptop or VPS) — public Poindexter user.
- A test/staging environment that should mirror production.
- A disaster-recovery rebuild from `pg_dump` + bootstrap.toml.
- The acceptance test for any change touching `services/migrations/`,
  `services/database_service.py`, `poindexter/cli/setup.py`, or `utils/startup_manager.py`.

If any step in this doc fails, the system is not in a shippable
state. File a hard issue on Glad-Labs/poindexter with the failing
output.

---

## Pre-flight

You need:

- **Docker** (Desktop or daemon) — Postgres + pgvector run in a
  container. The migration runner targets `pgvector/pgvector:pg16` in
  CI; `pg17` works locally. The container image is the only OS-level
  dep — Postgres itself is bundled.
- **Python ≥ 3.12** with `pip`. The setup wizard installs the rest
  via `poetry`.
- **About 5 minutes** for the full chain (90 % of the time is the
  Postgres pull on a cold cache).

Optional but recommended:

- **`tailscale`** for Grafana access from your phone (use your own
  tailnet IP, e.g. `<your-tailnet-ip>:3000`). Public Poindexter
  ships Grafana on `localhost:3000`; tailnet is operator-overlay only.
- **`gh`** CLI authenticated to `Glad-Labs/poindexter` if you want
  to file bug reports inline.

---

## Step 1 — Spin a fresh Postgres container

```bash
docker run -d --name poindexter-fresh \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=poindexter_brain \
    -p 5433:5432 \
    pgvector/pgvector:pg16

# Wait until pg_isready reports OK (1-3 seconds on warm cache).
until docker exec poindexter-fresh pg_isready -U postgres -d poindexter_brain >/dev/null 2>&1; do
    sleep 1
done
echo "postgres ready"
```

The container exposes Postgres on host port 5433 — that's the
standard local-dev port for Poindexter (not 5432, to avoid clashing
with a system Postgres). Pick any free port if you already use 5433
for the live DB.

> **Don't run this against a populated DB.** The migration runner is
> idempotent at the row level (each migration records itself in
> `schema_migrations` after success) but a half-applied migration on
> a populated DB is dangerous to re-run blind.

**Verify:**

```bash
docker exec poindexter-fresh psql -U postgres -d poindexter_brain \
    -c "SELECT version();"
# → PostgreSQL 16.x (pgvector 0.x) on x86_64-pc-linux-musl…
```

---

## Step 2 — Apply migrations

The CI smoke script is the right tool here — it's what production
relies on, it asserts row-count equality, and it's dependency-light.

```bash
DATABASE_URL=postgres://postgres:postgres@localhost:5433/poindexter_brain \
    python scripts/ci/migrations_smoke.py
```

**Expected tail output:**

```
[smoke] runner returned ok=True
[smoke] schema_migrations rows: N / files: N
[smoke] OK — all N migrations applied cleanly
```

The migration history is squashed into a single
`0000_baseline.py` (plus `0000_baseline.schema.sql` +
`0000_baseline.seeds.sql`) — re-rolled most recently by the Phase F
squash (2026-06-22), which folded the Phase E baseline + its 73
post-baseline migrations and retired `pipeline_tasks.category` — so the
post-baseline count is now just **2** (the baseline + the one surviving
`drop_pipeline_tasks_category` convergence migration). Derive the
expected count dynamically — the `-1` subtracts `__init__.py`:

```bash
python -c "from pathlib import Path; n=len(list(Path('src/cofounder_agent/services/migrations').glob('*.py')))-1; print(f'expect ~{n} migrations applied')"
```

Then verify against the DB:

```bash
docker exec poindexter-fresh psql -U postgres -d poindexter_brain \
    -c "SELECT COUNT(*) FROM schema_migrations;"
```

The two numbers should match.

**What this verifies:**

- Every migration file applies without raising.
- Every migration records exactly one `schema_migrations` row.
- No orphan rows in `schema_migrations` (rows without a matching file).
- No row count mismatch (would catch a silently-skipped migration).

**Common failure modes:**

| Symptom                                        | Likely cause                                  | Fix                                                                                       |
| ---------------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `MISSING INTERFACE: <file>` from the lint step | Migration lacks `up()` AND `run_migration()`  | Add one — see [`migrations.md`](migrations.md#1-generate-the-file).                       |
| `INVALID TIMESTAMP: <file>`                    | Timestamp prefix isn't a real datetime        | Regenerate with `python scripts/new-migration.py "<slug>"`.                               |
| Migration N raises, N+1 succeeds               | Runner does NOT halt on per-migration failure | Inspect the logged exception. The runner returned `False`; the smoke test exits non-zero. |
| `applied N — already up-to-date 0 — failed M`  | Same as above; M migrations failed.           | Read the logs above the summary. Each failure has an `exc_info=True` trace.               |
| `extension "vector" does not exist`            | Wrong Postgres image (vanilla `postgres:16`)  | Use `pgvector/pgvector:pg16` or `pg17`. Migration `0000_base_schema.py` requires it.      |

After step 2 the database should look like:

```bash
docker exec poindexter-fresh psql -U postgres -d poindexter_brain -c "
SELECT
  (SELECT COUNT(*) FROM schema_migrations) AS migrations,
  (SELECT COUNT(*) FROM app_settings)      AS app_settings_seeded,
  (SELECT COUNT(*) FROM qa_gates)          AS qa_gates_seeded,
  (SELECT COUNT(*) FROM niches)            AS niches_seeded,
  (SELECT COUNT(*) FROM oauth_clients)     AS oauth_clients_seeded,
  (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public') AS tables;
"
```

**Expected (verified 2026-06-22 against the Phase F baseline):**

```
 migrations | app_settings_seeded | qa_gates_seeded | niches_seeded | oauth_clients_seeded | tables
------------+---------------------+-----------------+---------------+----------------------+--------
          2 |                 761 |              16 |             2 |                    0 |     94
```

Note: `app_settings_seeded = 761` is the **full non-secret set** —
since the Phase E/F baselines are generated fold-forward from real DB
state, `0000_baseline.seeds.sql` now ships every `is_secret = false`
key (no longer just a minimal migration-seeded subset). Secret keys and
per-operator identity are NOT seeded; `poindexter setup` writes those.
`settings_defaults.py` still runs `seed_all_defaults()` at every boot as
an idempotent backstop, but on a fresh install the seeds file has
already populated the non-secret defaults.

`oauth_clients_seeded = 0` is correct — the initial CLI client gets
provisioned by `poindexter setup` per-installation (each install
gets unique credentials), not by a shared migration.

---

## Step 3 — Run the setup wizard

```bash
poindexter setup --db-url postgres://postgres:postgres@localhost:5433/poindexter_brain
```

The wizard runs four steps:

1. **DB connection** — opens a connection, fetches `version()`.
2. **Migrations check** — verifies `app_settings` table exists (proxy
   for "migrations already ran"). Will succeed because step 2 above
   ran them.
3. **Write `bootstrap.toml`** — to `~/.poindexter/bootstrap.toml`.
4. **Provision initial OAuth client** — generates a CLI client_id +
   client_secret pair, registers it in `oauth_clients`, and stores
   the credentials in `app_settings.cli_oauth_client_id` +
   `app_settings.cli_oauth_client_secret` (the secret is encrypted
   via `plugins.secrets.set_secret`).

**Expected tail output:**

```
1/4 — testing database connection…
OK — PostgreSQL 16.x …

2/4 — checking migrations…
OK — app_settings table already present — migrations already run

3/4 — writing /home/you/.poindexter/bootstrap.toml…
OK — wrote /home/you/.poindexter/bootstrap.toml

4/4 — provisioning initial OAuth client…
OK — initial OAuth client provisioned
  client_id:      poindexter_cli_…
  client_secret:  …                  ← capture this NOW

Setup complete.
```

**Verify:**

```bash
ls -la ~/.poindexter/bootstrap.toml
# → -rw------- (mode 600 — wizard sets safe perms)

docker exec poindexter-fresh psql -U postgres -d poindexter_brain -c \
    "SELECT key FROM app_settings WHERE key LIKE 'cli_oauth%' ORDER BY key;"
# → cli_oauth_client_id
#   cli_oauth_client_secret

docker exec poindexter-fresh psql -U postgres -d poindexter_brain -c \
    "SELECT client_id, client_name, scope FROM oauth_clients;"
# → poindexter_cli_… | poindexter-cli (initial) | api:read api:write
```

**Common failure modes:**

| Symptom                                                              | Cause / fix                                                                                                                                                                                           |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bootstrap.toml already exists`                                      | Existing install — back it up and re-run with `--force`, or run `--check` to verify the existing one.                                                                                                 |
| OAuth provisioning step prints `Could not provision OAuth client: …` | `mcp.shared.auth` import failed. Ensure `pip install mcp` has run (it's a dep of `pyproject.toml`). bootstrap.toml is still saved — you can run `poindexter auth migrate-cli` after the worker boots. |
| `Connection failed: …`                                               | DB URL wrong, or container not yet ready. Re-check step 1.                                                                                                                                            |

---

## Step 4 — Run `poindexter setup --check`

```bash
poindexter setup --check
```

`--check` is the no-op verification path — it touches no files,
provisions nothing, just reports per-component status.

**Expected on a fresh-but-not-booted system:**

```
Poindexter system check

  OK   bootstrap.toml         /home/you/.poindexter/bootstrap.toml
  OK   database URL           postgresql://postgres:***@localhost:5433/poindexter_brain
  OK   postgres connection    PostgreSQL 16.x …
  OK   migrations             app_settings table already present — migrations already run
 SKIP  worker API             api_base_url unset in app_settings
 SKIP  ollama                 ollama_url unset (worker will use hardcoded fallback if set)
 FAIL  brain daemon           brain_decisions table missing (brain daemon has never run)
 SKIP  telegram               unset — operator won't be paged
 SKIP  discord webhook        unset
```

The `FAIL` for the brain daemon is expected pre-boot — the brain
hasn't run yet so it's never recorded a decision. After step 5 it
flips to OK.

---

## Step 5 — Boot the worker against the fresh DB

This is the integration-test step. With the fresh DB, the worker's
lifespan should:

1. Connect to Postgres.
2. Run migrations (no-op — already applied).
3. Set up Redis cache (if `REDIS_URL` is set; falls back to no-op).
4. Initialize the rest of the services.
5. Log `Application started successfully!`.

```bash
cd src/cofounder_agent
DATABASE_URL=postgres://postgres:postgres@localhost:5433/poindexter_brain \
    poetry run uvicorn main:app --host 0.0.0.0 --port 8002
```

**Expected** (truncated for legibility):

```
🚀 Starting Poindexter application...
  Connecting to PostgreSQL (REQUIRED)...
  [INFO] Running database migrations...
   [OK] Database migrations completed successfully
  …
  [INFO] Initializing background task executor…
   [OK] All services initialized
 Application started successfully!
```

Hit `http://localhost:8002/health` from another terminal:

```bash
curl -s http://localhost:8002/health | python -m json.tool
```

Should return `200 OK` with a JSON body containing `"status": "ok"` (or
similar — the `/health` handler is defined in `main.py`).

**Then re-run the check** to confirm everything is wired:

```bash
poindexter setup --check
```

`brain daemon` will still `FAIL` (the brain is a separate process —
start it with `python -m brain.daemon` or your supervisor of choice).
`worker API` should now `OK` — the worker registered its own
`api_base_url` setting via the StartupManager.

---

## Step 6 — Tear down

```bash
docker rm -f poindexter-fresh
rm ~/.poindexter/bootstrap.toml   # ONLY for the test install
```

For a real install you obviously keep the bootstrap.toml + container.

---

## Summary — what gets verified

| Component                        | Step | Verification                                      |
| -------------------------------- | ---- | ------------------------------------------------- |
| pgvector extension available     | 1    | Container image                                   |
| Database accepts connections     | 1    | `pg_isready`                                      |
| Migration runner applies cleanly | 2    | `schema_migrations` row count matches file count  |
| No orphan / missing rows         | 2    | `migrations_smoke.py` row-set diff                |
| Filename convention              | 2    | `migrations_lint.py` (CI)                         |
| Bootstrap file write             | 3    | `~/.poindexter/bootstrap.toml` exists, mode 600   |
| Initial OAuth client provisioned | 3    | `oauth_clients` has 1 row, `cli_oauth_*` settings |
| `--check` passes                 | 4    | Per-component status                              |
| Worker lifespan succeeds         | 5    | `Application started successfully!` in logs       |
| Health endpoint responds         | 5    | `GET /health` returns 200                         |

If all pass on a clean Docker host, the system is shippable.

---

## Follow-ups and known gaps

These were discovered during the #378 fresh-DB verification pass and
filed as separate issues — they don't block this PR but improve the
fresh-DB experience.

- ~~**Lazy `app_settings` seeding.**~~ **Resolved (Phase F squash, 2026-06-22).**
  The Phase F baseline (`0000_baseline.seeds.sql`) now seeds all 761 non-secret
  `app_settings` keys generated fold-forward from real DB state, so a fresh
  install immediately has the full non-secret default set after step 2 — no
  lazy-load gap. `services/settings_defaults.py::seed_all_defaults()` (run by
  `StartupManager._run_migrations()` at every boot) remains as an idempotent
  backstop for keys added after the most recent squash.

- **`--auto` uses `pgvector/pgvector:pg16`** while CI uses the same
  image. Locally Matt's `docker-compose.local.yml` runs `pg17`. Worth
  consolidating on one Postgres major across CI + local + auto-setup.

- **`_run_migrations` in `cli/setup.py` is a proxy check, not a real
  apply.** It only verifies `app_settings` exists; it doesn't run the
  pending migrations. Step 2 above runs the smoke script as the real
  apply path. The setup wizard should ideally drive the runner
  directly so `poindexter setup` on a fresh DB Just Works without the
  separate `migrations_smoke.py` invocation.

These are tracked in the PR body as "follow-up issues to file."
