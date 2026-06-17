# Design: close epic #1340 — transport-adapter convergence (data-plane service, purity guard, CLI consolidation)

**Date:** 2026-06-17
**Status:** Approved (design); pending spec review
**Author:** Claude (Opus 4.8) with Matt

## Problem

The [transport-adapter contract ADR](../../architecture/2026-06-10-transport-adapter-contract.md)
(epic [#1340](https://github.com/Glad-Labs/glad-labs-stack/issues/1340)) decided
the **service / module layer is the single contract** and the HTTP API, the
`poindexter` CLI, and the MCP servers are **thin adapters** over it — no adapter
holds business logic or raw SQL. The epic is ~75% delivered:

| Sub-issue | What                                                               | Status                   |
| --------- | ------------------------------------------------------------------ | ------------------------ |
| #1341     | extract `posts_service`; CMS routes delegate (Category B)          | ✅ closed                |
| #1342     | converge MCP tools onto services; kill DIRECT-SQL (Category A)     | ✅ closed                |
| #1343     | mirror operator surfaces over HTTP (Category C)                    | ✅ closed (5/6 in #1491) |
| **#1522** | declarative data-plane **service + HTTP routes** (the 6th surface) | 🔲 open                  |
| **#1344** | bootstrap-exception docs + **adapter-purity CI guard**             | 🔲 open                  |

Two things still block a clean close:

1. **#1522 — the data-plane surface has no service at all.** The 5 declarative
   data-plane tables (`external_taps` / `retention_policies` / `webhook_endpoints`
   / `publishing_adapters` / `qa_gates`) are reachable only through the CLI, which
   hand-rolls raw SQL straight to the tables (e.g.
   [`cli/taps.py`](../../../src/cofounder_agent/poindexter/cli/taps.py) opens its
   own `asyncpg` connection and runs `SELECT … FROM external_taps`). There is no
   service to delegate to and no HTTP mirror — so a remote/SaaS consumer can't
   reach these surfaces, and the CLI is a textbook contract violation.
2. **#1344 — nothing enforces the contract.** Once the backlog lands, there is no
   guard stopping a future adapter from re-introducing inline SQL, and the
   permanent bootstrap-direct exception (`setup`/`migrate`/`auth`) is not written
   down as an allowlist, so a future reader could "fix" it into broken HTTP calls.

A third, **adjacent** problem surfaced while auditing the CLI for #1522: the
top-level command surface is crowded with **9 flat verb-commands**
(`approve` / `reject` / `list-pending` / `show-pending`,
`approve-publish` / `reject-publish` / `list-pending-publish` /
`show-pending-publish`, `publish-at`) that should be noun-grouped. This is an
**ergonomics** concern, distinct from transport purity, so it is tracked as a
**new sibling issue** rather than folded into the epic.

## Goals

1. **#1522:** a thin generic declarative-config service + OAuth-protected HTTP
   routes mirroring it, and the data-plane CLI groups refactored off raw SQL onto
   that service. Closes Category C.
2. **#1344:** bootstrap allowlist documented + a ratcheting adapter-purity CI
   guard that lands advisory and graduates to **required**. Locks the contract.
3. **CLI consolidation (sibling):** fold the 9 flat commands into existing noun
   groups so the base namespace is clean, with deprecated aliases for backcompat.
4. Close epic #1340 once #1522 + #1344 land.

## Non-goals

- **Not** forcing local in-process callers onto HTTP — direct service calls _are_
  the contract (ADR rule, non-goal #1).
- **Not** touching the bootstrap-direct paths except to document them.
- **Not** a big-bang — each workstream is an independent PR.
- **Not** rewriting the data-plane _handlers_ (`services/integrations/`); only the
  config-row CRUD gets a service. Handler execution (`tap_runner`, etc.) is
  unchanged and the data-plane CLI's `run` verbs keep delegating to it.

---

## Workstream 1 — #1522: data-plane service + routes + CLI de-SQL

### 1.1 Generic declarative-config service

New `src/cofounder_agent/services/declarative_config_service.py`. Per #1522's
explicit steer, **one generic service keyed on table + schema**, not seven
near-identical CRUD modules. A surface registry describes each table:

```python
@dataclass(frozen=True)
class SurfaceSpec:
    table: str                      # e.g. "external_taps"
    key_column: str                 # natural key, e.g. "name"
    mutable_columns: tuple[str, ...]  # columns upsert/patch may write
    # optional per-surface validation hook (returns normalized payload or raises)
    validate: Callable[[dict], dict] | None = None

_SURFACES: dict[str, SurfaceSpec] = {
    "taps":       SurfaceSpec("external_taps", "name", (...)),
    "retention":  SurfaceSpec("retention_policies", "name", (...)),
    "webhooks":   SurfaceSpec("webhook_endpoints", "name", (...)),
    "publishers": SurfaceSpec("publishing_adapters", "name", (...)),
    "qa-gates":   SurfaceSpec("qa_gates", "name", (...)),
}
```

Public functions (all `async`, all take `pool`; `surface: str` selects the spec;
unknown surface raises `UnknownSurfaceError`):

- `list_rows(pool, surface, *, filters: dict | None = None) -> list[dict]`
- `get_row(pool, surface, key: str) -> dict | None`
- `upsert_row(pool, surface, payload: dict) -> dict` — validate → whitelist to
  `mutable_columns` → `INSERT … ON CONFLICT (key_column) DO UPDATE`. **The one
  place** that knows the data-plane table SQL.
- `delete_row(pool, surface, key: str) -> bool`

Column identifiers come **only** from the registry (never from request
payloads), so the dynamic SQL can't be injected. Values are always bound
params. Typed errors (`UnknownSurfaceError`, `SurfaceValidationError`,
`RowNotFoundError`) map to HTTP status codes in the route layer (the
`gates_routes.py` exception-mapping pattern).

**Open items to resolve during implementation:**

- **Reconcile with `services/qa_gates_db.py`** (already owns `qa_gates`
  read/definition logic). Options: (a) register `qa-gates` to delegate its
  mutations into `qa_gates_db` rather than the generic path, or (b) fold
  `qa_gates_db`'s writers behind the generic service. Pick whichever avoids two
  writers for one table; do **not** duplicate.
- **Surface count: 5 vs 7.** The ADR's Category C listed seven
  (taps/retention/webhooks/qa-gates/**stores**/publishers/**validators**); #1522
  scopes to five tables. The CLI also has `stores` and `validators` groups.
  Confirm whether `data_stores` / content-validator config are declarative-config
  tables that belong in `_SURFACES`, or separate concerns. Default: ship the 5
  named in #1522; add `stores`/`validators` only if they are the same row-CRUD
  shape.
- Confirm exact `mutable_columns` per table from the live schema before writing
  the registry (don't guess columns).

### 1.2 HTTP routes

New `src/cofounder_agent/routes/data_plane_routes.py`, modeled exactly on
[`routes/gates_routes.py`](../../../src/cofounder_agent/routes/gates_routes.py):

- `APIRouter(prefix="/api/data-plane", tags=["data-plane"])`
- Every handler: `token: str = Depends(verify_api_token)` +
  `db_service: DatabaseService = Depends(get_database_dependency)`; **lazy**
  `from services.declarative_config_service import …` inside the handler; no
  logic in the route.
- Endpoints (surface is a path param validated against `_SURFACES`):
  - `GET    /api/data-plane/{surface}` → `list_rows`
  - `GET    /api/data-plane/{surface}/{key}` → `get_row` (404 if none)
  - `PUT    /api/data-plane/{surface}/{key}` → `upsert_row`
  - `DELETE /api/data-plane/{surface}/{key}` → `delete_row` (404 if none)
- Pydantic body model for upsert; service `*Error`s → `HTTPException` (400/404/409).
- Register in [`utils/route_registration.py`](../../../src/cofounder_agent/utils/route_registration.py)
  `_WORKER_ROUTES`; bump the manifest count in
  `tests/unit/utils/test_route_registration.py` (the #1491 pattern).

### 1.3 CLI refactor (off raw SQL)

The 5 data-plane CLI modules (`cli/{taps,retention,webhooks,qa_gates,publishers}.py`)
keep their current command _names_ (already grouped — no UX change), but their
bodies collapse onto a shared helper that calls the service:

- New `cli/_dataplane.py` helper: `run_service(coro)` pool wiring +
  `render_rows(surface, rows)` table formatting + shared `--state`/JSON output,
  so the 5 modules stop re-implementing `_connect()` + raw SQL + bespoke column
  printing. Each module shrinks to Click wiring + a `surface=` constant.
- The `run`/execution verbs (e.g. `taps run`) **keep** delegating to
  `services/integrations/tap_runner.py` — unchanged.

### 1.4 Tests

- Unit tests for `declarative_config_service` (per-surface CRUD round-trip;
  unknown-surface raises; payload whitelisting drops non-mutable columns;
  injection attempt via key/column name is rejected).
- Route contract tests (auth required; surface 404; CRUD happy-path) mirroring
  the #1491 route tests.
- CLI tests updated to assert delegation (no `asyncpg.connect` / SQL literal in
  the 5 modules) and unchanged output shape.

---

## Workstream 2 — #1344: bootstrap docs + adapter-purity guard

### 2.1 Documentation

- In the ADR, expand rule #4 into an explicit **allowlist** of the
  permanently-direct commands: `setup`; `migrate up|down|status`; `auth
register-client` / `migrate-cli` / `migrate-mcp` / `migrate-mcp-gladlabs` /
  `migrate-openclaw` / `migrate-brain` / `migrate-scripts`.
- One-line pointer in CLAUDE.md (Configuration / migrations section) so a future
  reader doesn't "fix" them into HTTP calls.

### 2.2 The guard — `scripts/ci/adapter_purity_lint.py`

Modeled directly on
[`scripts/ci/lint_silent_excepts.py`](../../../scripts/ci/lint_silent_excepts.py)
(AST scan + per-file baseline ratchet + inline override + `--update-baseline`).

**Scan roots (adapter trees only):**

- `src/cofounder_agent/routes/`
- `src/cofounder_agent/poindexter/cli/` **minus the bootstrap allowlist**
  (`setup.py`, `migrate.py`, `auth.py`, and the `_bootstrap.py` helper)
- `mcp-server/`
- **Excluded:** `mcp-server-gladlabs/` — the baseline JSON ships in the public
  mirror, exactly as `lint_silent_excepts.py` excludes the private overlay.

**What counts as a violation (inline business SQL in an adapter):**

- A `conn.fetch* / conn.execute* / conn.fetchrow / conn.fetchval` call (or
  `pool.fetch*`/`pool.execute*`) whose first argument is a **string literal that
  looks like SQL** (`^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP)`),
  **or**
- a bare SQL string literal passed to `asyncpg`/cursor execution.

**What is explicitly NOT a violation (avoid false positives):**

- `asyncpg.create_pool(...)` / `asyncpg.connect(...)` on their own — opening a
  pool to **hand to a service** is the correct adapter pattern (see
  `cli/approval.py`). The guard keys on _SQL execution_, not connection creation.
- Calls into `services.*` / `modules.*` (delegation).

**Mechanics:** per-file `scripts/ci/adapter_purity_baseline.json` (ratchet — may
only shrink); `# noqa: adapter-ok <reason>` inline override; exit 0 = no new
violations, exit 1 = new violation, printed as `file:line` + the SQL snippet.

### 2.3 Graduation to required

- Land the guard **advisory** first (CI step `continue-on-error` / non-required),
  run `--update-baseline` to snapshot whatever remains, and triage that list:
  fix the cheap ones (e.g. the `schedule.py` `app_settings` read — see WS3),
  baseline only genuinely-deferred entries.
- Add it to the **required** check set (alongside `test-backend` /
  `migrations-smoke`) **after PR A (#1522) and PR B (CLI) merge**, so the tree is
  clean and the baseline is honest, not padded with about-to-be-deleted debt.

---

## Workstream 3 — new sibling issue: CLI command-surface consolidation

Tracked as a **new issue** (sibling under #1340; ergonomics, not transport
purity). Low-risk — the flat commands already delegate to services, so this is
mostly Click rewiring.

### 3.1 Regroup (Matt's taxonomy — fold into existing nouns, no new top-level commands)

| Today (flat)                                                                           | Becomes                                             | Source                                          |
| -------------------------------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------- |
| `approve` / `reject` / `list-pending` / `show-pending`                                 | `poindexter gates {approve,reject,pending,show}`    | `cli/approval.py` (already holds `gates_group`) |
| `approve-publish` / `reject-publish` / `list-pending-publish` / `show-pending-publish` | `poindexter schedule {approve,reject,pending,show}` | `cli/publish_approval.py`                       |
| `publish-at`                                                                           | `poindexter schedule at`                            | `cli/schedule.py`                               |

### 3.2 Backcompat

- Keep the old flat names as **hidden, deprecated aliases** (`hidden=True`,
  print a one-line deprecation warning, delegate to the new grouped command).
  The poindexter CLI is part of the public product, so per the backcompat rule a
  rename ships with a shim. Removal scheduled in a later cleanup.

### 3.3 Bundled cleanups

- Fix the `cli/schedule.py:93` inline `SELECT key, value FROM app_settings`
  straggler → route through the settings service (also clears one adapter-purity
  baseline entry).
- **Merge `post` (singular) into `posts` (plural)** — two post groups is itself
  base-namespace clutter. Keep `post` as a hidden alias.

### 3.4 Tests / docs

- CLI tests for the new group paths + alias deprecation.
- Update `--help` text and any CLI docs / Mintlify references to the new paths.

---

## Sequencing — 3 PRs, this order

1. **PR A → #1522** — data-plane service + routes + CLI de-SQL + tests + manifest
   bump. _Removes the data-plane raw SQL before any baseline is taken._
2. **PR B → CLI-consolidation issue** — regroup flat commands + deprecated
   aliases + `post`/`posts` merge + `schedule.py` straggler fix.
3. **PR C → #1344** — bootstrap docs + advisory guard + baseline + flip to
   required (required-flip gated on A + B merged).
4. **Close epic #1340** (satisfied by A + C; B is the bonus ergonomics win and
   can trail).

Each PR is independent and CI-gated; per Matt's workflow each lands on its own
branch off `main` via squash/rebase, no direct-to-main. PR A and PR B have no
code dependency and could be built in parallel; PR C must be last.

## Testing strategy

- **Unit:** service CRUD + validation + injection-rejection; guard AST detection
  (fixture files: a pure delegation, a `create_pool`-passthrough that must
  _not_ flag, an inline-SQL adapter that _must_ flag, a `# noqa: adapter-ok`
  that must be respected).
- **Contract:** data-plane routes (auth, 404, CRUD) following #1491.
- **CI:** `migrations-smoke` unaffected (no schema change expected — verify);
  `adapter_purity_lint` runs advisory in PR C, required after A+B.
- Follow the repo default: every change ships contract tests + doc updates.

## Risks & open questions

- **Unknown baseline size.** The guard's first run enumerates _all_ current
  adapter violations tree-wide; the count isn't predictable until it runs.
  Mitigation: advisory-first + triage step (2.3).
- **`qa_gates_db.py` overlap** (1.1 open item) — must not create a second writer
  for `qa_gates`.
- **Surface scope 5 vs 7** (1.1 open item) — confirm `stores`/`validators`.
- **`get_database_dependency` pool** must be the same pool the service expects;
  reuse the route DI exactly as `gates_routes.py` does.
- **Public-mirror safety:** guard + baseline exclude `mcp-server-gladlabs/`; ADR
  - CLAUDE.md edits are public-safe; run `check_public_mirror_safety.py` locally
    before pushing PR C.

## Acceptance criteria

**#1522 (PR A):**

- [ ] `declarative_config_service.py` with generic CRUD over the 5 surfaces; no
      raw data-plane SQL anywhere but this module.
- [ ] `/api/data-plane/*` routes (OAuth-protected, thin) registered + manifest
      count bumped.
- [ ] 5 data-plane CLI modules contain no `asyncpg.connect`/SQL literals (config
      CRUD); `run` verbs still delegate to `tap_runner`.
- [ ] Unit + contract tests green.

**#1344 (PR C):**

- [ ] ADR + CLAUDE.md name the bootstrap allowlist.
- [ ] `adapter_purity_lint.py` runs in CI; flags net-new inline SQL with
      `file:line`; respects baseline + `# noqa: adapter-ok`; excludes bootstrap +
      private overlay.
- [ ] Guard is a **required** check after A + B merge.

**CLI consolidation (PR B):**

- [ ] 9 flat commands regrouped under `gates`/`schedule`; old names work as
      hidden deprecated aliases.
- [ ] `post` merged into `posts` (alias kept); `schedule.py` SQL straggler fixed.
- [ ] CLI tests + help/doc text updated.

**Epic #1340:**

- [ ] #1522 + #1344 closed; epic closed with a clean (honest) purity baseline.
