# Transport surfaces are adapters; the service layer is the contract

**Status:** design v1 — 2026-06-10
**Decision:** The shared **service / module layer** (heading toward `modules/<x>/api.py`)
is the single contract for business logic. The HTTP API, the `poindexter` CLI,
and the MCP servers are **thin adapters** over it. No adapter contains business
logic or raw SQL — each delegates to a service function. "Go over HTTP" is
mandatory only across a **process / trust boundary**, never for local in-process
callers.
**Tracking:** [#1340](https://github.com/Glad-Labs/poindexter/issues/1340)
(epic) → #1341 / #1342 / #1343 / #1344.
**Aligns with:** [`kernel-platform-architecture.md`](2026-06-04-kernel-platform-architecture.md)
(surfaces = front of house), [`module-v1.md`](module-v1.md) (the
`modules/content/api.py` thin-adapter seam),
[`2026-05-28-site-config-di-migration.md`](2026-05-28-site-config-di-migration.md)
(constructor DI is what made services portable across entry points),
[`poindexter-as-engine.md`](poindexter-as-engine.md).

## The question

> "Should everything just go through the HTTP API for consistency?"

We have three ways into the same business logic — the HTTP API, the CLI, and the
MCP servers — and they each answer "how do I reach a service?" differently, with
reimplementations drifting apart. The tempting fix is "make everything go through
the API." This document records why that is the **wrong** target and what we do
instead.

## The answer: converge on the service layer, not the transport

**All-HTTP is impossible.** `setup`, `migrate`, and `auth` provisioning run
_before_ the API process, the schema, or the first OAuth client exist. You cannot
OAuth into an API to mint the first OAuth client. A subset of commands is
therefore permanently direct — so the API can never be the single chokepoint.

**All-HTTP is also the wrong kind of consistency.** The consistency we want is
_logical_ — one place each business rule, validation, and audit hook lives. You
get that by routing every caller through the same **service function**, not by
forcing a local, same-process caller through `HTTP → route → service` to reach
logic sitting in the same Python process. That detour buys a network round-trip,
a JSON serialization pass, and an OAuth handshake — and zero additional
correctness.

The real defect the 2026-06-10 audit found was never "some callers skip HTTP."
It was **duplicated logic**: e.g. the MCP `list_tasks` tool hand-rolls
`SELECT … FROM pipeline_tasks_view` while the CLI `tasks list` and the API both go
through `database_service.get_tasks_paginated`. Three copies of "list tasks" that
can drift. The fix is to make all three call one function — which needs no HTTP at
all for the in-process callers.

## The rule

1. **No business logic or SQL in an adapter.** Route handlers, CLI commands, and
   MCP tools all delegate to a service function. An adapter's job is transport:
   parse input, call the service, shape the response.
2. **One operation → one owning service function.** Kill reimplementations.
3. **The HTTP API is a _complete mirror_ of the service surface** — anything you
   can do via a direct call, you can also do over HTTP — so a remote / cloud
   coordinator / SaaS / forked-CLI consumer is never second-class. Local callers
   still call services in-process; the API exposes everything but is not the only
   door.
4. **Bootstrap exception** (permanent, documented): `setup`, `migrate`
   (`up`/`down`/`status`), and `auth` provisioning (`register-client`,
   `migrate-cli`/`-mcp`/`-mcp-gladlabs`/`-openclaw`/`-brain`/`-scripts`) stay
   direct. They run before the substrate exists; routing them through HTTP is not
   possible, and listing them keeps a future reader from "fixing" them.

## Why HTTP becomes mandatory at the boundary

Rule #3 is forward-looking. Today every consumer (API, CLI, MCP, Prefect tasks,
the brain) shares one Python import path and one local DB, so "import the service"
and "open the pool" work everywhere. The moment a consumer crosses a **process or
trust boundary** — a `DEPLOYMENT_MODE=coordinator` cloud host, a hosted/forkable
product where a customer's CLI or MCP hits _your_ instance, remote phone-driven
agents — "import the service" stops working and "open the DB pool from a remote
client" becomes a security hole.

So the HTTP API is the **boundary contract**: the network-reachable, OAuth-guarded
projection of the service surface. The auth asymmetry we have today (OAuth guards
HTTP writes; direct callers trust the local pool) is fine for a single-operator
local box and becomes the **enforcement seam** the day we go multi-tenant — at
which point the direct paths simply are not exposed to untrusted callers.

This is the same shape as the kitchen model in
[`kernel-platform-architecture.md`](2026-06-04-kernel-platform-architecture.md): surfaces are
"front of house," the service/module layer is the station that owns the work.
Surfaces take orders and report status; they do not cook.

## Current-state snapshot (2026-06-10 audit)

Point-in-time; the living worklist is in
[#1340](https://github.com/Glad-Labs/poindexter/issues/1340).

The CLI is already mostly correct — most commands call `services.*` directly via
constructor DI (the payoff of the SiteConfig DI migration). The drift is
concentrated in three places:

**A — Transport reimplementations** (adapter hand-rolls what a service owns):
MCP `list_tasks`, `get_budget`, `get_setting`/`set_setting`/`list_settings`, the
memory tools, `get_post_count`; CLI `settings list --include-inactive`. → #1342.

**B — API-side inline logic** (routes don't delegate, so logic isn't reusable —
this is what _forces_ category A): `cms_routes.py` posts/categories/tags/analytics
all run inline SQL with no posts service; gate-history `INSERT`s are inline and
duplicated across `approval_routes.py` and `task_publishing_routes.py`; operational
metrics and `go_live` are inline. → #1341 (posts service first; highest ROI).

**C — HTTP coverage gaps** (service-only ops with no route, blocking remote
parity): approval-gate admin, posts-approval, scheduling, topic-batch, media
approval, and the declarative data plane (taps/retention/webhooks/qa-gates/stores/
publishers/validators — which have no service at all yet). → #1343.

## Consequences

**Positive**

- One validation/audit path per operation; behavior can't drift between surfaces.
- Remote-ready: the day a cloud coordinator or hosted product ships, the API is
  already a full projection — no scramble to expose operator surfaces.
- Matches the `modules/content/api.py` destination already on the Module v1
  roadmap; this decision is the _why_ behind that seam.

**Cost**

- Extract `posts_service` (and pull gate-history / metrics / go-live into their
  services). Net new routes for the service-only surfaces.
- A CI adapter-purity guard so the rule doesn't re-rot (#1344).

**Non-goals**

- Not forcing local in-process callers onto HTTP.
- Not removing direct service calls — they _are_ the contract.
- Not a big-bang; each sub-issue lands independently.

## Worklist

| Issue                                                             | Work                                                                             |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| [#1340](https://github.com/Glad-Labs/poindexter/issues/1340) | Epic / tracker                                                                   |
| #1341                                                             | Extract `posts_service`; CMS routes delegate (Category B)                        |
| #1342                                                             | Converge MCP tools onto services; kill DIRECT-SQL reimplementations (Category A) |
| #1343                                                             | Mirror operator surfaces over HTTP (Category C)                                  |
| #1344                                                             | Bootstrap-exception docs + adapter-purity CI guard                               |
