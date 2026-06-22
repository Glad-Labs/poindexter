# Phase F migration squash + `pipeline_tasks.category` retirement

**Date:** 2026-06-22
**Branch:** `claude/phase-f-squash-category-retire`
**Predecessor:** #1867 (reconciled `category` to base-table-only)
**Status:** design approved (verbal) — this doc is the plan of record + execution checklist

---

## 1. Goal

1. **Flatten** the migration tree: fold the ~73 post-baseline timestamped migrations into a fresh `0000_baseline.{py,schema.sql,seeds.sql}` (a "Phase F squash", following Phase D 2026-05-29 and Phase E 2026-06-06).
2. **Retire `pipeline_tasks.category`** entirely, so the add→drop→re-add churn (`baseline` add → `20260622_032938` drop → `20260622_055500` re-add) collapses to nothing in the new baseline.

Motivation (operator): the add/drop/re-add is wasted churn, and the file count is cadence-due for a squash (Phase E fired at 55 accumulated; we're at 73).

## 2. Why retire `category` is safe (recap from #1867)

- Empirically dead: **0 of 1,830** rows non-NULL on prod `poindexter_brain`; superseded by `niche_slug` (#796).
- The **only base-table reader** is `claim_pending_task` (`services/flows/content_generation.py`); everything else reads the `content_tasks` / `pipeline_tasks_view` views, which project a **literal** `NULL::character varying` (not `pt.category`). So dropping the base column does not touch the views.
- The claimed value is immediately defaulted to `"technology"` in `content_router_service`, so removing the read is behaviourally inert.

## 3. The key correctness wrinkle — a baseline can't drop a column

A squash baseline only ever `CREATE TABLE IF NOT EXISTS`. On prod (which already has the `category` column from `…055500`) that **no-ops**, so a baseline that simply omits `category` would leave prod with the column while fresh installs lack it — **prod/fresh schema drift**, the exact thing a squash is meant to prevent.

**Resolution:** the squash ships **one surviving post-baseline migration**, `…_drop_pipeline_tasks_category.py` =
`ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS category`.

- On **fresh installs**: the new baseline omits `category`, so this is a no-op (`IF EXISTS`).
- On **existing installs (prod)**: the baseline no-ops, then this migration performs the real drop.
- Both converge to a category-free `pipeline_tasks`. The next squash (Phase G) folds this away once every install has dropped it.

The views keep their literal-`NULL` `category` shim (back-compat for `SELECT *` / `TaskRecord.category` / `?category=`), per the "breaking changes ship with shims" rule. No view/trigger change.

**Deploy ordering is safe:** the merged PR carries the code change (claim no longer selects `category`) _and_ the drop migration together. The worker restart runs migrations before serving; the new code never reads the column. No crash window.

## 4. Generation methodology — fold-forward from a fresh-migrated DB

The new baseline is generated from a **throwaway DB that ran the existing chain**, not from a prod re-dump. Rationale: a baseline's job is to reproduce what the chain produces on a clean DB; generating it from the chain is correct-by-construction and avoids baking in any prod hand-drift or operator-tuned values. A read-only diff against prod is done only as an informational drift check.

**No destructive operation ever touches prod (`poindexter_brain`).** All generation/verification uses fresh `pgvector/pgvector:pg16` containers or `poindexter_unit_*` throwaway DBs. The category `DROP` for baseline generation happens on a throwaway DB. Prod only changes at deploy time, via the surviving drop migration.

## 5. Step plan

**Stage A — code + drop migration + tests** (reversible; on branch)

- `content_generation.py::claim_pending_task`: remove `category` from the SELECT column list and the `category = claimed.get("category")` + threading into `process_content_generation_task`. Confirm (grep) no other direct `pipeline_tasks` reader selects `category`.
- New migration `YYYYMMDD_HHMMSS_drop_pipeline_tasks_category.py` (DROP COLUMN IF EXISTS).
- Flip `TestAddTaskAgainstRealDb` guards: base column **absent**, view shim **present**, `add_task` still NULL via view. (Replaces the #1867 "base-table-only present" assertions.)

**Stage B — golden dump + new `0000_baseline.schema.sql`**

1. Fresh container `poindexter-squash-gen` (pg16).
2. Apply the **old** chain via `migrations_smoke.py` (baseline + 73).
3. `ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS category;` on it.
4. `pg_dump --schema-only --no-owner --no-privileges` → **golden reference** (`/tmp/golden_schema.sql`, kept for verification).
5. Sanitize the dump into `0000_baseline.schema.sql` (see §6).

**Stage C — `0000_baseline.seeds.sql` + Phase F docstring**

- `pg_dump --data-only --inserts` of the non-secret seed tables (§7) from the same DB → `0000_baseline.seeds.sql`.
- Rewrite `0000_baseline.py` docstring: Phase F, what it absorbs (Phase E baseline + the 73 `20260607_*…20260622_*` files), key schema/seed deltas, and the `category` retirement.

**Stage D — delete folded files**

- `git rm` all 73 post-baseline files **except** the new drop-category migration.

**Stage E — verify** (§8). **Stage F — docs + PR** (§9, §10).

## 6. Sanitization rules (pg_dump → idempotent baseline)

Match the existing `0000_baseline.schema.sql` conventions (verified by reading it):

- `CREATE TABLE ` → `CREATE TABLE IF NOT EXISTS `
- `CREATE SEQUENCE ` → `CREATE SEQUENCE IF NOT EXISTS `
- `CREATE INDEX ` / `CREATE UNIQUE INDEX ` → `… IF NOT EXISTS `
- `CREATE FUNCTION ` → `CREATE OR REPLACE FUNCTION `
- `CREATE VIEW ` → `CREATE OR REPLACE VIEW ` (or `DROP VIEW IF EXISTS` + `CREATE`)
- `CREATE TRIGGER ` → preface with `DROP TRIGGER IF EXISTS … ;` (pg16 has no `CREATE OR REPLACE TRIGGER` for constraint triggers; the existing baseline uses drop-then-create)
- `CREATE TYPE ` (enums) → wrap in `DO $$ … EXCEPTION WHEN duplicate_object THEN null; $$` or `DROP TYPE IF EXISTS … CASCADE` guard, matching current baseline
- `ALTER TABLE ONLY … ADD CONSTRAINT` → guard with `IF NOT EXISTS` where the dump form allows, else accept (constraints on freshly-created tables are fine; on prod the table already has them → wrap or tolerate via the existing baseline's approach)
- Strip `pg_dump` preamble (`SET statement_timeout`, `SELECT pg_catalog.set_config`, ownership) — keep only DDL.
- Extensions (`vector`, `pgcrypto`, `pg_trgm`, `pg_stat_statements`): `CREATE EXTENSION IF NOT EXISTS` (the conftest installs these pre-baseline, but keep for completeness).

The authoritative check is **not** the sanitization being perfect by eye — it's the §8 golden-dump diff. Any slip surfaces there.

## 7. Seed tables for `seeds.sql` (non-secret only)

From the existing `0000_baseline.seeds.sql` header: `app_settings` (WHERE `is_secret = false`), `qa_gates`, `content_validator_rules`, `niches`, `pipeline_templates` (carries the **final** `canonical_blog` + `dev_diary` `graph_def`), `external_taps`, `publishing_adapters`, `webhook_endpoints`, `retention_policies`, `fact_overrides`. Use `ON CONFLICT DO NOTHING` form. Exclude any `is_secret = true` rows and per-operator credentials (`cli_oauth_*`, etc.).

## 8. Verification (the hard gate)

1. **Golden-dump diff (correctness gate):** fresh container `poindexter-squash-verify` → apply **only** `0000_baseline.py` + the drop-category migration → `pg_dump --schema-only --no-owner --no-privileges` → `diff` against `/tmp/golden_schema.sql`. **Must be empty.** (Order-normalize if pg_dump ordering differs: sort objects or use `pg_dump`'s stable ordering on both sides — both produced by the same pg_dump version.)
2. **`migrations_smoke.py`** against a fresh DB → `schema_migrations` count == file count (now 2: baseline + drop migration), no orphans.
3. **Full backend suite:** `poetry run pytest tests/unit -q` (the `db_pool` fixture now builds from the new baseline) — green, incl. the flipped `TestAddTaskAgainstRealDb`.
4. **fresh-db-setup counts:** tables / seeded-row sanity vs the documented expectations (update the doc's figures).
5. **Prod drift check (informational):** read-only `pg_dump`/MCP compare of prod schema (minus `category`) against the golden reference; log any drift, don't block.

## 9. What changes / what's deleted

- **Rewritten:** `0000_baseline.{py,schema.sql,seeds.sql}`.
- **Added:** `…_drop_pipeline_tasks_category.py` (the one survivor).
- **Deleted:** the 73 `20260607_*` … `20260622_*` post-baseline files (incl. `…032938` drop + `…055500` re-add — the churn vanishes).
- **Code:** `content_generation.py` (claim read removed); `test_tasks_db.py` (guards flipped).
- **Docs:** `CLAUDE.md` migration narrative, `docs/operations/migrations.md` (Phase F TL;DR), `docs/operations/fresh-db-setup.md` (count figures).

## 10. Rollback & safety

- Entirely on a branch; **prod untouched until merge+deploy**. Rollback = abandon the branch.
- The PR will **not** be auto-merged (unlike #1867): it re-rolls the schema source of truth, so it gets explicit operator review + full CI (`migrations-smoke`, `integration-db`, `test-backend`, `public-mirror-safety`).
- Post-merge deploy: worker restart runs `…drop_pipeline_tasks_category` on prod (drops the dead column); the new claim code is already category-free → no crash window.

## 11. Risks

| Risk                                                             | Mitigation                                                                                       |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Sanitization introduces a schema diff                            | Golden-dump diff gate (§8.1) catches it mechanically                                             |
| Seed regeneration drops/mangles the `canonical_blog` graph_def   | Diff seed row counts + spot-check `pipeline_templates.graph_def` node count vs current prod      |
| `category` drop crashes claim on prod during deploy              | Code + migration ship together; new code never reads the column                                  |
| Orphan `schema_migrations` rows for the 73 deleted files on prod | Harmless — runner skips by filename, never reconciles the reverse; documented in `migrations.md` |
| pg_dump 18.3 dumping a pg16 server                               | Forward-compatible (newer client dumps older server); both diff sides use the same client        |
