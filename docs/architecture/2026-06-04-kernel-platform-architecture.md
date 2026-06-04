# Kernel, Modules & the Architect — Platform Architecture

**Status:** north-star design — the compass all kernel/module/architect work points at.
Not a migration plan. A successor to and refinement of
[`business-os-endgame.md`](business-os-endgame.md) and
[`module-v1.md`](module-v1.md).
**Date:** 2026-06-04 (brainstormed with Matt).

---

## North star — the kitchen

Poindexter is an **autonomous business OS**: a strong, bulletproof **kernel** (the
platform) that a workforce of **modules** (departments) runs on, composed at
runtime by an **LLM architect**. The mental model is a professional kitchen:

| Kitchen               | System         | Role                                                                  |
| --------------------- | -------------- | --------------------------------------------------------------------- |
| **Oven**              | Kernel         | The appliance. Heat, timing, fridge, the order system. Knows no dish. |
| **Ingredients**       | Atoms / skills | Composable units, each self-describing (`requires` / `produces`).     |
| **Raw goods + tools** | Capabilities   | Flour, the mixer, the LLM-as-utility. Commodity, shared, swappable.   |
| **Stations**          | Modules        | Bakery, finance desk. Each stocks the pantry + owns its corner.       |
| **Chef**              | The architect  | Reads the pantry + the order, composes ingredients into a dish.       |
| **Front of house**    | Surfaces       | The window, the host stand, the phone — orders in, status out.        |

The crucial reframe: **the "core business" (content) and the "core system"
(the kernel) are not the same thing.** Content is the most important _module_,
but the thing kept bulletproof, isolated, and testable is the **kernel** — which
contains **zero domain logic**. Business-centrality is not architectural-centrality.

---

## The three layers

```
The architect (kernel)   composes a graph_def per intent from the catalog
   │
Modules (departments)    content, finance, support, compliance, HR, …
   │                     each: manifest + migrations + routes + jobs + ATOMS/SKILLS + glue
Capabilities (plugins)   llm / image / video / tts / taps / publishing — shared tools
   │
Kernel (the platform)    data · config · identity · LLM access · observability ·
                         module system · execution engine · scheduler · eventing ·
                         the architect · the surface runtimes
```

**The one-line divide rule.** If you'd need it whether or not "content" existed →
**kernel**. If it's a shared deterministic tool → **capability**. If it's a
department's knowledge or judgment → **module**.

---

## Governing principles

These are not section-specific; they shape every seam below. A design that
violates one of these is wrong, not merely suboptimal.

1. **Fail fast and loud.** Missing required config, an absent-but-expected
   dependency, a malformed contract → raise / `notify_operator()` / non-zero
   exit. No silent fallbacks, no fail-open defaults. The kernel never guesses on
   the module's behalf; a module never guesses on the kernel's. (The one
   sanctioned exception is _expected absence_ — a module legitimately not
   installed — which is **logged**, never silent.)
2. **DB-configurable by default.** Every tunable is an `app_settings` row read
   through `SiteConfig`, not a literal in code and not an env var. This includes
   the kernel's own knobs, every module's knobs, the architect's autonomy dials,
   and background-algorithm windows. The only thing on disk is `bootstrap.toml`
   (the DSN + machine secrets to bring the stack up before any row is reachable).
3. **Own the interface, rent the implementation.** The kernel owns the _seams_
   (Platform handle, module contract, catalog descriptor, surface channel
   contract, data schema); the implementations behind them (LangGraph, LiteLLM,
   the Telegram lib, Grafana) are swappable. Everything behind a seam is
   disposable.
4. **`OperatorScope` for tenancy.** All data access flows through one injected
   scope object, even with a single operator. Multi-tenancy later is an additive
   change at that one seam, not a retrofit.
5. **Earned autonomy.** Anything the system might one day do on its own (author a
   skill, stand up a module, raise a cap) is gated by a **runtime-tunable trust
   level (an `app_setting`) + track-record telemetry + reversible rails** — never
   a hard-coded `if`. Full autonomy is an asymptote, not a switch.
6. **CLI-first, MCP-parity.** Every operator action is a CLI verb with MCP
   parity, so a human and the architect drive the system through the _same_
   surfaces.
7. **PostgreSQL as the spinal cord.** Components communicate through shared DB
   state and events, not by importing each other.
8. **Built for LLM consumers.** Catalogs, descriptors, errors, and configs are
   shaped to be read by an LLM (the architect) first.

---

## The kernel (the oven)

Small, stable, heavily tested, **no domain logic**. It is the only thing every
module depends on. Contents:

- **Data plane** — Postgres pool and transactions, the migration runner (substrate and per-module migrations), the declarative data-plane tables.
- **Config & identity** — `app_settings` / `SiteConfig`, secrets, OAuth,
  `OperatorScope`.
- **LLM access** — `dispatch_complete` (the LiteLLM-backed router), `cost_guard`,
  cost-tier resolution, `prompt_manager`.
- **Observability plumbing** — logging / metrics / tracing (Langfuse),
  `audit_log`, the brain daemon. (Views are split — see [Observability](#observability).)
- **Module system** — the registry (presence-based discovery + manifest +
  lifecycle hooks), per-module migrations, the capability-plugin registry.
- **Execution engine** — `template_runner` + `pipeline_architect` +
  `atom_registry`: the generic `graph_def` compiler / validator / runner.
- **Scheduler & eventing** — the job scheduler + the event bus over the spinal cord.
- **The architect** — the LLM orchestrator (see [The architect](#the-architect-the-chef)).
- **Surface runtimes** — the bot processes, the MCP server, the CLI root, the
  FastAPI app, the Grafana provisioner (see [Surfaces](#surfaces-front-of-house)).

---

## Capabilities (raw goods + shared tools)

The existing ~20 entry-point groups: `llm_providers`, `image_providers`,
`video_providers`, `tts_providers`, `audio_gen_providers`, `caption_providers`,
`topic_sources`, `publish_adapters`, `taps`, … Deterministic mechanism that
modules **compose**. Kernel-adjacent but pluggable and swappable — the purest
expression of "own the interface, rent the implementation."

---

## Modules (the stations / departments)

A module is a **manifested business function**: `manifest` + per-module
`migrations` + `routes` + `jobs` + **its composable units (atoms/skills)** +
business glue, discovered by the kernel's presence-based registry. Content is the
**flagship** module — architecturally a peer of finance/support/etc., even though
it is the business crown jewel; being a clean, _bounded_ module is what makes it
**more** bulletproof (contained blast radius, testable in isolation).

Modules are **hybrid by maturity**: a thin tier (manifest binds capabilities +
lists skills, mostly borrowed) and a graduated tier (custom services where a moat
emerges, like content's adversarial QA). Graduation is a fill-in change, never a
rewrite of the module's identity, routes, or data.

---

## The four seams

The layers are settled; the design _is_ the seams where they touch.

### Seam 1 — the Platform handle (kernel access)

A module reaches the kernel **only** through one injected `Platform` object — an
interface, never the kernel's internals. Modules import the `Platform` _type_,
not kernel modules. This delivers all three hard requirements at once:
**isolation** (a module touches the oven only through the handle),
**testability** (hand it a fake `Platform`), and **painless add/remove** (a
module's sole dependency is one stable interface). It is also exactly the
"thin-adapter" that retires the transitional substrate↔module import coupling.

**What's on the handle (minimal but complete):**

- `db` — pool + transaction scope (already `OperatorScope`-scoped)
- `dispatch` — LLM completion through the router (cost-guarded, tier-aware)
- `config` — read an `app_settings` value via `SiteConfig` (sync, cached)
- `secret` — read an encrypted secret (async, DB-hit)
- `log` / `metric` — structured logging + metric emit
- `audit` — a scoped `audit_log` writer
- `events` — emit / subscribe on the bus
- **No handle to other modules** — cross-department traffic goes through the
  expo line (Seam 3). This exclusion is load-bearing.

**Principle enforcement at this seam.** `config`/`secret` are the _only_ tunable
source — a module that hardcodes a literal is a bug (DB-configurable). A required
config that is missing makes the handle raise, not default (fail loud). The
make-or-break is keeping the handle **small but complete**: too thin and modules
reach around it (back to direct imports); too fat and it is the kernel
re-exported and the isolation is fiction.

### Seam 2 — the catalog (chef ↔ ingredients)

Every composable unit registers into **one shared catalog** with a uniform,
LLM-readable descriptor:

- `name` — unique slug (`qa.critic`, `finance.reconcile`)
- `description` — routing text the architect reads ("what + when")
- `requires` / `produces` — typed I/O, so the architect can tell two units fit
  and `build_graph_from_spec` can enforce it
- `cost_tier` + side-effects + idempotency — so the architect reasons about cost
  and safety

The architect's loop: **read catalog + order → emit a `graph_def` → validator
checks types/reachability → engine runs it.** Three of those four pieces exist
today (`AtomMeta` descriptors, the `graph_def` format, `build_graph_from_spec`).

**The one design move that matters now is uniformity:** every module exposes its
units through the same descriptor into the _one_ catalog. Today only content's
atoms self-describe; the module contract gains a single clause — _a department
registers its composable units the same way content does._ That uniformity is
the entire difference between "the architect can stitch across departments" and
"the architect can only see the bakery." A unit that does not self-describe is
**invisible** to the architect (fail-loud at the catalog boundary: a registered
unit missing required descriptor fields is rejected, not silently skipped). The
architect's _reasoning_ — planning, replanning, mid-pipeline failure recovery —
is explicitly **deferred** (YAGNI): design the catalog + `graph_def` seam now,
drop the brain in later, exactly like `OperatorScope`.

### Seam 3 — the expo line (inter-module communication)

Stations never call each other. They communicate through the spinal cord —
shared Postgres + `audit_log` + the brain's knowledge graph — or by
emit/consume on the Platform's **event bus** (e.g. content publishes a post →
emits `content.published` → a social module consumes it). No module imports
another; there is no cross-module RPC. This is _why_ the Platform handle has no
door to other modules.

### Seam 4 — hiring (self-management)

Self-management falls out for free if the seams above hold. A new department is:
**drop a package that implements the module contract (manifest + migrations +
registered atoms/surfaces), and the kernel's presence-based discovery picks it
up.** The _system_ doing it itself = the architect scaffolding that package
against the contract. The cleaner the contract, the more it can do unattended —
which is the whole reason to get the contract right now. Every self-dev
capability is gated by an **earned-autonomy** dial (an `app_setting`) + track
record + reversible rails; install/uninstall and spend increases stay
human-gated until a dial is earned.

---

## Observability

Splits the same way everything else does — kernel runtime + module contribution.

- **Plumbing → kernel.** Emit + collect + store: logging/metrics/tracing infra,
  Prometheus/Loki/Tempo/Langfuse, `audit_log`, the brain. Modules don't _run_
  any of it; they **emit** through the Platform handle (`log` / `metric` /
  `audit`). One stack, every module feeds it.
- **Views → split.** Cross-cutting boards (Mission Control, Cost, System Health)
  are kernel. Department boards (content's Pipeline/QA Rails, finance's balance
  trend) are **module-contributed**: the module ships its dashboard JSON and the
  kernel's Grafana provisioner mounts it under a per-module folder
  (`register_dashboards`). Per-module **brain probes** register the same way
  (`register_probes`); a probe whose required fields are missing is rejected
  (fail loud), and all probe thresholds/intervals are `app_settings` (DB-config).

---

## Surfaces (front of house)

The operator-facing channels — Grafana-as-window, Telegram/Discord, CLI, MCP —
are **front of house**: how orders come in and status goes out. Same split:

- **Channel runtime → kernel.** The bot processes, the MCP server, the CLI root
  group, the FastAPI app, the Grafana provisioner. The kernel runs them; the
  channel _implementations_ (Telegram lib, MCP SDK, Grafana) are rented and
  swappable.
- **Channel content → module.** A module registers its CLI subcommands, MCP
  tools, HTTP routes, dashboards, and probes — the contract hooks Module v1
  already carries (`register_routes` / `register_cli` / `register_dashboards` /
  `register_probes`; add `register_mcp_tools`).

A surface is therefore a **channel contract**, in two flavors: **control**
(CLI/MCP/Telegram/Discord — give orders + observe; also how the architect
_receives_ intents) and **observe** (Grafana — read-only window into the
kitchen).

Two properties fall out of modeling surfaces as uniform channels:

1. **A real GUI is just another channel.** When one is wanted, it registers
   against the same contract — a new front-of-house station, not a rewrite. "We
   don't have a UI yet" becomes "we haven't plugged in that channel yet."
2. **Human and architect share one steering wheel.** CLI-first / MCP-parity means
   the architect acts through the exact channels the operator does — anything
   doable from the CLI/MCP, the architect can do too.

---

## The architect (the chef)

A **kernel** service (cross-cutting; operates across all modules). Given the
catalog + an intent, it emits a `graph_def`, the validator checks it, the engine
runs it. It is **half-built already**: `atom_registry.to_catalog_text()`
(literally "so the architect can scan"), the DB-stored `graph_def`, and
`build_graph_from_spec`. What's missing is the LLM that turns catalog + intent
into a `graph_def` — and that is deliberately deferred. **Design the seam now,
defer the brain.** When it lands, it is governed by earned-autonomy dials
(propose-only → auto-compose) that are `app_settings`, with every composed run
traced and reversible.

---

## What this means for the current code

- **The architecture is already partly real.** The 2026-06-04 content-module
  migration pulled content's code into `modules/content/`, leaving the generic
  engine (`template_runner`, `pipeline_architect`, `prompt_manager`, `llm_text`,
  `atom_registry`) in substrate — i.e. the kernel/module split has _begun_.
  `module-v1.md` already defines the manifest + lifecycle hooks; the atom catalog
  - `graph_def` + validator are the architect's machinery.
- **The first buildable slice is Seam 1 — the `Platform` handle.** It doubles as
  the thin-adapter cleanup the migration left behind (the transitional
  `main.py`→`quality_service`, `task_routes`→`stages` imports become
  Platform-mediated). Get the handle small-but-complete, route modules through
  it, and the isolation/testability/pluggability goals are met.

## Seams to preserve in all new work

1. **`Platform` handle** — modules reach the kernel only through the injected
   interface; never import kernel internals.
2. **Catalog descriptor** — new composable units self-describe uniformly into the
   one catalog; non-conforming units are rejected loud.
3. **Expo line** — modules communicate through shared state + events, never by
   importing each other.
4. **`OperatorScope`** — all data access through the scope.
5. **Autonomy as an `app_setting`** — anything self-dev is a runtime-tunable
   trust dial + telemetry + reversible rails.
6. **CLI-first / MCP-parity** — every operator action is a CLI verb with MCP
   parity, drivable by human and architect alike.

A stable contract on these is the precondition for the system to eventually run
and extend itself — which is the actual deliverable, more than any one feature.
