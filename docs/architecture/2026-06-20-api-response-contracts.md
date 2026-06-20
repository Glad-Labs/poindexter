# API response contracts: one list envelope, one item shape, snake_case, typed, catalogued

**Status:** proposed — 2026-06-20
**Decision:** Every JSON API response follows one shape per category. A list
response is `{"items": [...], "total": N, "limit": N, "offset": N}`. A
single-resource response is the bare resource object. Field names are
`snake_case` everywhere — no camelCase, no Strapi `data.attributes` nesting.
Every `2xx` declares a typed Pydantic `response_model`; `response_model=dict[str, Any]`
is not acceptable on a new or touched route. The machine-readable catalog
(`/api/openapi.json`) is always reachable — behind auth in production, never
removed.
**Tracking:** [#745](https://github.com/Glad-Labs/poindexter/issues/745)
(this ADR is remediation step 1; the typed-schema sweep is steps 2–N).
**Aligns with:**
[`2026-06-10-transport-adapter-contract.md`](2026-06-10-transport-adapter-contract.md)
(the service layer is the contract — the wire shape is part of that contract,
and one shape means adapters don't each reinvent it) and the
`design-for-llm-consumers` principle (a coherent, typed, discoverable surface is
what makes the API legible to an LLM caller). The error-path counterpart,
[#624](https://github.com/Glad-Labs/poindexter/issues/624) (one `ErrorResponse`,
closed), did this for `4xx`/`5xx`; this is the success-path half.

## The problem (fresh inventory, 2026-06-20)

The 2026-06-09 audit found the success-path response contracts had drifted into
mutually incompatible shapes across sibling endpoints. Re-verified today against
`routes/` — the drift is real and has grown since the audit:

- **~107 HTTP operations** in `routes/` (the audit counted 83), of which
  **39 declare `response_model=dict[str, Any]`** (the audit counted 14) — the
  untyped escape hatch is spreading, not shrinking. Most operations declare no
  `2xx` schema at all.
- **Seven list-envelope shapes** still in use, e.g. `{tasks, total, offset, limit}`,
  `{items, total, page, per_page, pages}` (settings — `per_page` appears 11×),
  `{count, events, server_time}` (3×), `{episodes, count}` (podcast + video
  routes), and bare arrays.
- The Strapi-compat `{data, meta}` / `coverImage.data.attributes.url` camelCase
  leak the audit flagged in `cms_routes.py` is **gone** (already cleaned up) —
  so `snake_case` is currently uniform — this ADR's job there is to lock that
  in, not fix it.

A consumer (LLM or human) cannot write one pagination helper, one item
unwrapper, or one typed client against this surface. And in production
`main.py` set `openapi_url=None`, so the one artifact that would let a consumer
discover the surface was removed exactly where it matters most.

## Decision

1. **List envelope — `{"items", "total", "limit", "offset"}`.**
   - `items`: the page of results (always present, `[]` when empty — never
     `null`, never a bare top-level array).
   - `total`: total matching rows across all pages (the count the UI needs for
     "N results" / page math).
   - `limit` / `offset`: the pagination window echoed back.
   - `items` (not `tasks` / `episodes` / `events`) so one client unwraps every
     list. Offset/limit (not page/per_page) because it matches the DB query
     layer (`get_tasks_paginated` and friends already think in
     `LIMIT ... OFFSET`) and the task endpoints, so it composes without
     page↔offset translation.
   - **Reconciliation with the existing generic.** `schemas/database_response_models.py`
     already ships a `PaginatedResponse[T]` — but it is **page-based**
     (`{total, page, limit, items}`) and is the shape settings uses. The
     codebase is genuinely split (tasks/SQL = offset, settings/the generic =
     page); this ADR resolves the split toward offset. Step 2 retrofits
     `PaginatedResponse[T]` to `{items, total, limit, offset}` (or adds an
     offset-based `ListResponse[T]` and deprecates the page-based one),
     migrating settings in the same pass.

2. **Single resource — the bare object.** `GET /api/posts/{id}` returns the post
   object directly, no `{data: ...}` / `{post: ...}` wrapper. One unwrap rule:
   lists carry `items`, item endpoints carry the object.

3. **snake_case everywhere.** Response keys are `snake_case`; no camelCase, no
   nested `data.attributes`. (Currently true — locked in by the conformance test
   below.)

4. **Typed `2xx` on every touched route.** Each operation declares a Pydantic
   `response_model`. Lists use the offset-based generic from decision 1 (the
   reconciled `PaginatedResponse[T]` / `ListResponse[T]`,
   `items: list[T]; total: int; limit: int; offset: int`). `response_model=dict[str, Any]`
   is a smell, not a target — a route being edited for any reason gets a real
   model on the way through.

5. **The catalog is always reachable.** `/api/openapi.json` is served in every
   environment. In production it is **behind auth** (the full surface shouldn't
   be anonymously enumerable) rather than removed. `docs`/`redoc` UI stay
   disabled in production; the spec — what an API/LLM client consumes — does not.

## Migration strategy (backcompat-aware, incremental)

The contract changes are applied **route-by-route as routes are touched**, not
in one breaking big-bang — matching the issue's "mechanical sweep" framing and
[`backcompat-now-required`].

- **Public-site-consumed endpoints** (`cms` / `podcast` / `newsletter` / the
  video & podcast feeds) are a compatibility boundary: the deployed Next.js
  site reads their current shapes. Change these only with a shim (accept/emit
  both shapes for a release) or in lockstep with a public-site change in the
  same PR.
- **Operator-only endpoints** (consumed by our own CLI / MCP / operator
  console, all in-repo) migrate **in place**, updating those callers in the same
  PR. Single-operator surface, controlled consumers — no external deprecation
  window needed.
- New endpoints adopt the contract from day one.

## Conformance ratchet

A structural unit test (sibling to `test_operator_routers_require_auth.py`)
asserts the catalog stays reachable-but-authed in production, and — as the sweep
lands — that list endpoints declare a `ListResponse`-shaped `response_model`.
Like the auth test, it inspects route objects, so a regression fails CI rather
than shipping. The first slice (this PR) covers the catalog; the envelope
assertions grow with the sweep so the ratchet only tightens.

## Consequences

- **Step 1 (this PR):** this ADR + re-expose `/api/openapi.json` behind auth in
  production (`utils/openapi_auth.py`), with the structural test.
- **Steps 2–N:** the typed-schema sweep — reconcile the existing
  `PaginatedResponse[T]` to the offset shape (decision 1), then convert list
  endpoints to it route-by-route, retiring `response_model=dict[str, Any]` as
  each is touched, honoring the compatibility boundary above.
- A consumer gets one pagination helper, one unwrap rule, a typed client from
  the spec, and a discoverable (authed) catalog. The cost is the sweep's
  per-route edits — paid down incrementally, not all at once.
