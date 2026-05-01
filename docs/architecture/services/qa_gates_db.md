# QA Gates DB

**File:** `src/cofounder_agent/services/qa_gates_db.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_qa_gates.py`
**Last reviewed:** 2026-04-30

## What it does

`load_qa_gate_chain(pool, stage_name="qa")` reads the `qa_gates`
table and returns an ordered list of `QAGateSpec` records that
describe the QA reviewer chain in DB-driven, declarative form.
`MultiModelQA` walks this list to know which reviewers (citation
verifier, web fact-check, vision gate, etc.) to run in what order,
which are required-to-pass, and what their per-gate config looks like.

This module is the runtime read-side of migrations 0093 + 0094
(`0093_create_qa_gates_table.py`, `0094_seed_qa_gates_default_chain.py`)
which moved the QA chain out of hardcoded Python and into a
declarative table — part of the broader "declarative data plane"
work (see `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`).

Three deliberate design choices:

1. **Read-only here.** Inserts/updates live in the
   `poindexter qa-gates ...` CLI (`poindexter/cli/qa_gates.py`) so
   the runtime can never accidentally mutate the catalog. Counter
   columns get updated via the audit pipeline, not through this
   module.
2. **Stage scoping.** v1 ships only the `qa` stage chain; the
   `stage_name` column exists so future `pre_research` /
   `post_publish` chains can reuse the same table without a schema
   bump.
3. **Graceful fallback.** When `pool is None` (unit tests, callers
   without a DB) or the table doesn't exist (fresh checkout that
   hasn't migrated), the function returns `[]`. `MultiModelQA`
   interprets the empty list as "use the legacy hardcoded chain" so
   tests and pre-migration installs keep working.

> **Status:** as of 2026-04-30 the table + CLI + read layer are in
> place but the runtime consumer wiring in `MultiModelQA` is not yet
> calling `load_qa_gate_chain()`. v1 is scaffolding for the imminent
> declarative cutover. TBD — needs operator confirmation when the
> runtime swap actually lands; until then the legacy hardcoded chain
> is what's running in prod.

## Public API

- `QAGateSpec` (frozen dataclass) — one row of `qa_gates`,
  materialized:
  - `name: str` — unique identifier, used in logs + audit events.
  - `stage_name: str` — pipeline stage (`qa` in v1).
  - `execution_order: int` — ordering key.
  - `reviewer: str` — reviewer type: matches one of the reviewer
    identifiers in `MultiModelQA` (`citation_verifier`,
    `vision_gate`, `web_factcheck`, `consistency_gate`,
    `url_verifier`, `programmatic_validator`, `critic`, etc.).
  - `required_to_pass: bool` — whether failure of this gate vetoes
    the post regardless of aggregated score.
  - `enabled: bool`
  - `config: dict[str, Any]` — per-gate JSONB config (merged into
    the reviewer's runtime kwargs).
- `QAGateSpec.applies_to_style(writing_style_id) -> bool` — filter
  helper for per-niche QA variation. Empty / missing
  `config["applies_to_styles"]` means "applies to all styles";
  otherwise only fires when `writing_style_id` is in the list. The
  caller does the filtering — the loaded chain is reusable across
  requests with different styles.
- `await load_qa_gate_chain(pool, *, stage_name="qa", only_enabled=True) -> list[QAGateSpec]` —
  ordered by `(execution_order ASC, name ASC)`. Pass
  `only_enabled=False` from CLI commands that want to list disabled
  rows too.

`__all__ = ["QAGateSpec", "load_qa_gate_chain"]`

## Configuration

This module has NO `app_settings` keys — its config IS the
`qa_gates` table. The seed migration (`0094`) populates the default
v1 chain. Edits go through the CLI:

- `poindexter qa-gates list` — show the chain
- `poindexter qa-gates show <name>` — full row + JSONB config
- `poindexter qa-gates enable <name>` / `disable <name>`
- `poindexter qa-gates reorder ...`

Runtime cache invalidation is handled by `ReloadSiteConfigJob` in the
broader settings reloader; the `load_qa_gate_chain` call itself does
no caching — every invocation hits the DB.

## Dependencies

- **Reads from:**
  - `qa_gates` table (PostgreSQL, asyncpg). Schema: `name`,
    `stage_name`, `execution_order`, `reviewer`, `required_to_pass`,
    `enabled`, `config jsonb`. Plus counter columns updated outside
    this module (audit pipeline).
- **Writes to:** nothing.
- **External APIs:** none.
- **Sister-service callers:**
  - `services.multi_model_qa` (intended consumer once the declarative
    cutover lands — currently still using hardcoded chain).
  - `poindexter.cli.qa_gates` — the CLI uses raw SQL for mutations
    rather than going through this module.

## Failure modes

- **`pool is None`** — short-circuits to `[]`. Documented contract:
  callers fall back to legacy hardcoded chain. No log line.
- **`qa_gates` table missing** (fresh clone, migrations not applied)
  — `conn.fetch` raises, the broad `except Exception` at line 115
  catches it, logs at DEBUG (`qa_gates lookup failed (...) — runtime
will use legacy chain`), returns `[]`. Intentionally DEBUG-level
  noise: it's a known transient on first boot, and screaming about
  it on every poll would drown the real signal.
- **Transient connection blip during startup** — same path as
  table-missing. Same DEBUG log. Same fallback.
- **`config` returned as JSON string** (some asyncpg / typecodec
  versions return jsonb as text rather than dict) — module re-parses
  with `json.loads`; on parse failure, sets `config = {}` and the
  spec still loads with empty config. The reviewer just runs with
  defaults.
- **Style-scoped row, no `writing_style_id` passed** — filtering is
  caller's job. `applies_to_style(None)` returns False unless the
  config list is empty/missing (in which case True for all styles).
  If a caller forgets to filter, all rows fire — that may be
  acceptable for v1 single-style setups.

## Common ops

- **List the active chain:**
  `poindexter qa-gates list`
- **Inspect a gate's config JSON:**
  `poindexter qa-gates show citation_verifier`
- **Disable a noisy gate temporarily:**
  `poindexter qa-gates disable web_factcheck`
- **Reorder gates** (e.g. cheap-first):
  `poindexter qa-gates reorder programmatic_validator,citation_verifier,critic,web_factcheck`
  (CLI exact syntax — see `poindexter/cli/qa_gates.py`.)
- **Add a new gate type** — requires (1) implementing the reviewer
  in `MultiModelQA`, then (2) inserting a `qa_gates` row that names
  it via the `reviewer` column. The CLI can do the insert; the table
  itself is the authoritative declaration.
- **Diagnose "why is the legacy chain still running?"** — check
  whether the runtime is actually calling `load_qa_gate_chain`. If
  not, the declarative cutover hasn't been wired yet (see status
  note above).
- **Verify migrations applied:**
  ```sql
  SELECT name, applied_at FROM schema_migrations
  WHERE name LIKE '%qa_gates%' ORDER BY applied_at;
  ```
  Expect `0093_create_qa_gates_table` and
  `0094_seed_qa_gates_default_chain`.

## See also

- `docs/architecture/services/multi_model_qa.md` — the consumer of
  this chain (currently calling its hardcoded fallback).
- `docs/architecture/declarative-data-plane-rfc-2026-04-24.md` —
  broader RFC that motivated moving the chain into the DB.
- `poindexter/cli/qa_gates.py` — write-side CLI (the only sanctioned
  way to mutate `qa_gates`).
- Migrations `0093_create_qa_gates_table.py` and
  `0094_seed_qa_gates_default_chain.py` — schema + default rows.
- `docs/operations/migrations-audit-2026-04-27.md` — confirms both
  migrations applied 2026-04-27.
