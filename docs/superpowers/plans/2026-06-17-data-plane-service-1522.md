# Data-plane config service + HTTP routes + CLI de-SQL (#1522) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the 5 declarative data-plane tables a single generic service + an OAuth-protected HTTP mirror, and refactor the data-plane CLI off raw SQL onto that service — closing the last HTTP-coverage gap (#1522) under epic #1340.

**Architecture:** One `declarative_config_service.py` keyed on a `_SURFACES` registry (`SurfaceSpec` per table: table name, key column, mutable-column whitelist, jsonb columns). Four async CRUD functions (`list_rows` / `get_row` / `upsert_row` / `delete_row`) take a `pool` + a `surface` string; the registry is the _only_ source of SQL identifiers (injection-safe), values are bound params. HTTP routes (`/api/data-plane/*`) are thin adapters over it (the `gates_routes.py` pattern). The CLI groups keep their command names but their bodies collapse onto a shared helper that calls the service.

**Tech Stack:** Python 3.12, asyncpg, FastAPI, Click, pytest. Spec: `docs/superpowers/specs/2026-06-17-transport-adapter-convergence-design.md`.

**Sibling plans (later PRs):** CLI command-surface consolidation + #1344 adapter-purity guard each get their own plan when their turn comes. This plan is PR A only.

---

## Worktree test invocation (verify FIRST, before any TDD step)

This runs in a git worktree without its own `poetry install`. Per repo convention, drive pytest through the **repo-root venv + PYTHONPATH override** (no per-worktree install). Establish the working command before Task 1:

```bash
# From the worktree root. Confirm the exact incantation that collects & runs:
cd src/cofounder_agent && poetry run pytest tests/unit/ -q -k route_registration 2>&1 | tail -20
```

If that fails to collect in the worktree, fall back to the repo-root venv with
`PYTHONPATH=src/cofounder_agent` per the worktree-preflight note. **Do not proceed
to Task 1 until a known unit test runs green here.** Record the working command and
reuse it verbatim in every step below (shown as `pytest …`).

---

## File Structure

- **Create** `src/cofounder_agent/services/declarative_config_service.py` — the generic service (registry + CRUD + typed errors). The ONLY module with data-plane table SQL.
- **Create** `src/cofounder_agent/routes/data_plane_routes.py` — thin OAuth routes mirroring the service.
- **Create** `src/cofounder_agent/poindexter/cli/_dataplane.py` — shared CLI helper (pool wiring + row rendering) so the 5 CLI modules stop duplicating `_connect()` + SQL + formatting.
- **Modify** `src/cofounder_agent/utils/route_registration.py` — add `data_plane_routes` to `_WORKER_ROUTES`.
- **Modify** `src/cofounder_agent/poindexter/cli/{taps,retention,webhooks,qa_gates,publishers}.py` — bodies delegate to the service via the helper; command names unchanged; `run` verbs keep delegating to `tap_runner`/`retention_runner`/etc.
- **Create** `src/cofounder_agent/tests/unit/services/test_declarative_config_service.py`
- **Create** `src/cofounder_agent/tests/unit/routes/test_data_plane_routes.py`
- **Modify** `src/cofounder_agent/tests/unit/utils/test_route_registration.py:124` — bump `26` → `27`.

---

## The registry (authoritative — derived from live schema 2026-06-17)

All 5 tables share `name` as the UNIQUE natural key. Mutable = operator-config columns only; telemetry (`last_run_*`, `total_*`, `state`, `created_at`, `updated_at`, `id`) is excluded so an adapter can never write it.

```python
_SURFACES: dict[str, SurfaceSpec] = {
    "taps": SurfaceSpec(
        table="external_taps", key_column="name",
        mutable_columns=("name", "handler_name", "tap_type", "target_table",
                         "record_handler", "schedule", "config", "enabled",
                         "metadata", "niche_id"),
        json_columns=frozenset({"config", "metadata"}),
    ),
    "retention": SurfaceSpec(
        table="retention_policies", key_column="name",
        mutable_columns=("name", "handler_name", "table_name", "filter_sql",
                         "age_column", "ttl_days", "downsample_rule",
                         "summarize_handler", "enabled", "config", "metadata"),
        json_columns=frozenset({"downsample_rule", "config", "metadata"}),
    ),
    "webhooks": SurfaceSpec(
        table="webhook_endpoints", key_column="name",
        mutable_columns=("name", "direction", "handler_name", "path", "url",
                         "signing_algorithm", "secret_key_ref", "event_filter",
                         "enabled", "config", "metadata"),
        json_columns=frozenset({"event_filter", "config", "metadata"}),
    ),
    "publishers": SurfaceSpec(
        table="publishing_adapters", key_column="name",
        mutable_columns=("name", "platform", "handler_name", "credentials_ref",
                         "default_tags", "rate_limit_per_day", "enabled",
                         "config", "metadata"),
        json_columns=frozenset({"default_tags", "config", "metadata"}),
    ),
    "qa-gates": SurfaceSpec(
        table="qa_gates", key_column="name",
        mutable_columns=("name", "stage_name", "execution_order", "reviewer",
                         "required_to_pass", "enabled", "config", "metadata"),
        json_columns=frozenset({"config", "metadata"}),
    ),
}
```

---

## Task 1: SurfaceSpec + registry + typed errors

**Files:** Create `services/declarative_config_service.py`; Test `tests/unit/services/test_declarative_config_service.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from services.declarative_config_service import (
    SurfaceSpec, _SURFACES, resolve_surface, UnknownSurfaceError,
)

def test_all_five_surfaces_registered():
    assert set(_SURFACES) == {"taps", "retention", "webhooks", "publishers", "qa-gates"}

def test_resolve_unknown_surface_raises():
    with pytest.raises(UnknownSurfaceError):
        resolve_surface("nope")

def test_key_column_is_always_in_mutable_columns():
    for spec in _SURFACES.values():
        assert spec.key_column in spec.mutable_columns

def test_json_columns_subset_of_mutable():
    for spec in _SURFACES.values():
        assert spec.json_columns <= set(spec.mutable_columns)
```

- [ ] **Step 2: Run test, verify it fails** — `pytest tests/unit/services/test_declarative_config_service.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement the registry + errors** — `SurfaceSpec` dataclass (`frozen=True`: `table`, `key_column`, `mutable_columns: tuple[str,...]`, `json_columns: frozenset[str] = frozenset()`, `validate: Callable[[dict],dict] | None = None`), the `_SURFACES` dict above, `resolve_surface(name) -> SurfaceSpec` (raises `UnknownSurfaceError`), and the error classes (`DataPlaneError` base; `UnknownSurfaceError`, `SurfaceValidationError`, `RowNotFoundError`).

- [ ] **Step 4: Run test, verify pass.**

- [ ] **Step 5: Commit** — `git commit -m "feat(data-plane): SurfaceSpec registry for the 5 declarative-config tables (#1522)"`

## Task 2: `list_rows` + `get_row` (read path)

**Files:** Modify the service + test. Use a fake pool/conn (async context manager returning canned `asyncpg.Record`-like dicts) — no live DB in unit tests.

- [ ] **Step 1: Write failing tests** — `test_list_rows_selects_from_registry_table` (asserts the SQL hits `external_taps` and returns list of dicts), `test_get_row_returns_none_when_missing`, `test_get_row_deserializes_jsonb_string` (a `config` returned as a JSON _string_ by asyncpg comes back as a dict), `test_list_rows_unknown_surface_raises`.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** `list_rows(pool, surface, *, filters=None)` and `get_row(pool, surface, key)`. SELECT `*`; `_row_to_dict(record, spec)` deserializes `json_columns` that come back as `str` (mirror `qa_gates_db.py:130-137`). Filters limited to equality on registry columns only (ignore unknown keys). Add `# nosec B608` on the f-string SQL with a note that identifiers come from the trusted registry.

- [ ] **Step 4: Run, verify pass.** **Step 5: Commit** — `feat(data-plane): list_rows/get_row read path`.

## Task 3: `upsert_row` (the one place with write SQL)

- [ ] **Step 1: Write failing tests:**
  - `test_upsert_whitelists_columns` — payload with a telemetry col (`total_runs`) and an unknown col is dropped; only `mutable_columns` reach the SQL.
  - `test_upsert_requires_key_column` — missing `name` → `SurfaceValidationError`.
  - `test_upsert_serializes_json_columns` — a dict `config` is `json.dumps`'d and the placeholder is `$N::jsonb`.
  - `test_upsert_injection_attempt_is_inert` — a payload key like `"name; DROP TABLE"` is simply not in `mutable_columns`, so it's dropped (asserts no such identifier reaches SQL).
  - `test_upsert_sets_updated_at` — the generated `ON CONFLICT` clause contains `updated_at = now()`.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** `upsert_row(pool, surface, payload)`:
  1. `data = spec.validate(payload) if spec.validate else dict(payload)`
  2. `cols = [c for c in spec.mutable_columns if c in data]`; require `spec.key_column in cols` else `SurfaceValidationError`.
  3. Build values: for `c in cols`, if `c in spec.json_columns and not isinstance(v, str): v = json.dumps(v)`.
  4. Placeholders: `$i`, append `::jsonb` when `c in spec.json_columns`.
  5. `set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != key) + ", updated_at=now()"`.
  6. `INSERT INTO {table} ({cols}) VALUES ({ph}) ON CONFLICT ({key}) DO UPDATE SET {set_clause} RETURNING *` (`# nosec B608` — identifiers from registry). `fetchrow`, return `_row_to_dict`.

- [ ] **Step 4: Run, verify pass.** **Step 5: Commit** — `feat(data-plane): upsert_row with column whitelist + jsonb casts`.

## Task 4: `delete_row`

- [ ] **Step 1: Failing tests** — `test_delete_returns_true_on_hit` / `test_delete_returns_false_on_miss` (inspect asyncpg `DELETE n` command tag like `cli/taps.py:173`).
- [ ] **Step 2: Fail.** **Step 3:** Implement `delete_row(pool, surface, key) -> bool` (`DELETE FROM {table} WHERE {key_column}=$1`, return tag not `…0`). **Step 4: Pass.** **Step 5: Commit** — `feat(data-plane): delete_row`.

## Task 5: HTTP routes + manifest

**Files:** Create `routes/data_plane_routes.py`; Modify `utils/route_registration.py`; Modify `tests/.../test_route_registration.py:124`; Create `tests/unit/routes/test_data_plane_routes.py`.

- [ ] **Step 1: Write failing tests** mirroring the #1491 route tests: auth required (no token → 401/403); `GET /api/data-plane/taps` → 200 list; `GET /api/data-plane/taps/{key}` missing → 404; `PUT` upsert → 200; `DELETE` missing → 404; unknown surface → 404/422. Plus bump `test_route_registration.py:124` to `assert len(_WORKER_ROUTES) == 27` and add a docstring line dated 2026-06-17 (#1522).

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement `data_plane_routes.py`** — copy the shape of `routes/gates_routes.py` exactly: `APIRouter(prefix="/api/data-plane", tags=["data-plane"])`; every handler `Depends(verify_api_token)` + `Depends(get_database_dependency)`; **lazy** `from services.declarative_config_service import …` inside each handler; map `UnknownSurfaceError`→404, `RowNotFoundError`→404, `SurfaceValidationError`→400. Endpoints: `GET /{surface}`, `GET /{surface}/{key}`, `PUT /{surface}/{key}` (Pydantic body = free-form `dict[str, Any]`, key injected from path), `DELETE /{surface}/{key}`. Then add to `_WORKER_ROUTES`: `("routes.data_plane_routes", "router", "data_plane_router", "declarative data-plane CRUD (taps/retention/webhooks/publishers/qa-gates, #1522)")`.

- [ ] **Step 4: Run, verify pass** (route tests + `test_route_registration.py`).
- [ ] **Step 5: Commit** — `feat(api): /api/data-plane/* routes mirroring the config service (#1522)`.

## Task 6: shared CLI helper

**Files:** Create `poindexter/cli/_dataplane.py`.

- [ ] **Step 1: Failing test** (`tests/unit/cli/test_dataplane_helper.py`) — `render_rows(surface, rows)` returns a table string with the surface's display columns; `run_service` opens a pool, awaits the coro, closes the pool (assert close called).
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** — `run_service(coro_factory)`: `pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)`; `try: return await coro_factory(pool) finally: await pool.close()` (this `create_pool`-then-pass-to-service is the _allowed_ adapter pattern — it executes no SQL itself). `render_rows(surface, rows)`: shared column-aware table print (generalize `cli/taps.py:94-107`). JSON output helper for `show`.
- [ ] **Step 4: Pass.** **Step 5: Commit** — `feat(cli): shared _dataplane helper (pool wiring + rendering)`.

## Task 7: refactor `taps.py` onto the service (worked example)

**Files:** Modify `poindexter/cli/taps.py`; Modify `tests/.../test_*taps*.py` (or add).

- [ ] **Step 1: Failing/guard test** — assert `cli/taps.py` source contains no `asyncpg.connect` and no SQL literal (`SELECT`/`UPDATE`/`INSERT`/`DELETE`) — i.e. it's now pure. Keep an output-shape test for `taps list`.
- [ ] **Step 2: Run, verify fail** (today it still has SQL).
- [ ] **Step 3: Refactor** — `taps_list` → `run_service(lambda p: list_rows(p, "taps", filters=...))` + `render_rows("taps", rows)`; `taps_show` → `get_row`; `taps_enable/disable` → `upsert_row(p, "taps", {"name": name, "enabled": bool})`; **keep `taps_run`** delegating to `tap_runner.run_all` (unchanged — execution, not config). Delete the local `_connect()` + SQL.
- [ ] **Step 4: Run, verify pass** (purity guard + output shape).
- [ ] **Step 5: Commit** — `refactor(cli): taps delegates to declarative_config_service (#1522)`.

## Task 8: refactor `retention` / `webhooks` / `qa_gates` / `publishers`

For EACH file, in its own commit, repeat the Task-7 transform. **Read the file first** to enumerate its exact commands and preserve them (each currently follows the `taps.py` list/show/enable/disable + a domain `run`/secret verb shape):

- [ ] `retention.py` — list/show/enable/disable → service; keep `run` → `retention_runner`. Guard test (no SQL) + commit.
- [ ] `webhooks.py` — list/show/enable/disable + any `set-secret` (keep secret-write path; it uses the secrets helper, not table SQL — verify) → service for config CRUD. Guard test + commit.
- [ ] `qa_gates.py` — list/show/enable/disable/reorder → `upsert_row(p,"qa-gates",…)`. **Runtime read path stays** `services/qa_gates_db.load_qa_gate_chain`; telemetry stays `qa_gates_db_writer` — only the CLI's config-mutation SQL moves. Guard test + commit.
- [ ] `publishers.py` — list/show/enable/disable + secret path → service. Guard test + commit.

## Task 9: open-item resolution + final verification

- [ ] **`stores` / `validators` decision (spec open item 1.1).** Read `cli/stores.py` + `cli/validators.py`. If they're the same row-CRUD shape over a declarative table, add a `SurfaceSpec` + refactor them too (extend Tasks 1/7). If not (different shape / not a config table), leave them and note it in the PR description. Default: ship the 5 named in #1522.
- [ ] **Full suite** — `pytest tests/unit/ -q` green; run the integration subset if data-plane-touching.
- [ ] **Public-mirror safety** — `python scripts/ci/check_public_mirror_safety.py` (no operator leak in the new files).
- [ ] **Lint/type** — `ruff` + `mypy` on the new modules.
- [ ] **Open PR** against `Glad-Labs/glad-labs-stack` titled `feat(api): declarative data-plane service + HTTP routes + CLI de-SQL (#1522)`, body links #1522 + the spec, lists the stores/validators decision.

---

## Self-review (against the spec)

- **Spec 1.1 (service):** Tasks 1–4 ✔ (registry, CRUD, errors, jsonb, injection-safety, updated_at).
- **Spec 1.1 open items:** qa_gates reconciliation → Task 8 (read path stays in `qa_gates_db`); 5-vs-7 → Task 9; exact columns → resolved in the registry above. ✔
- **Spec 1.2 (routes):** Task 5 ✔ (thin, OAuth, lazy import, manifest bump 26→27, error mapping).
- **Spec 1.3 (CLI de-SQL):** Tasks 6–8 ✔ (shared helper; `run` verbs preserved; command names unchanged).
- **Spec 1.4 (tests):** service unit + route contract + CLI purity/output tests across Tasks 1–8 ✔.
- **Type consistency:** `SurfaceSpec`, `resolve_surface`, `UnknownSurfaceError`/`SurfaceValidationError`/`RowNotFoundError`, `list_rows`/`get_row`/`upsert_row`/`delete_row`, `run_service`/`render_rows` — names used identically across tasks. ✔
