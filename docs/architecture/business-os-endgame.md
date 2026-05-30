# Business-OS Endgame — Module, Skill & Autonomy Architecture

**Status:** north-star design (the target all module/skill work points at).
**Scope:** the long-horizon architecture. Not a migration plan — a compass.

---

## North star

Poindexter is an **autonomous business OS**: a workforce of **modules
(departments)** that an **architect** composes per intent, run by a single
operator from a phone. Content is module 1 of N. Future departments —
customer support, finance, devops, compliance/legal, marketing, HR — are the
rest of the org chart.

The whole system is built so that, over time, it can **extend itself** —
author new skills and stand up new departments against a stable contract —
with progressively less human direction.

---

## The core model

Three layers, orthogonal, composed top-down:

```
Architect (LLM)        composes processes per intent across modules
   │
Modules (departments)  manifested business functions: content, support, …
   │                   each BINDS capabilities + BUNDLES skills + adds glue
Skills (procedures)    SKILL.md units — the "how-to" catalog, mostly borrowed
   │
Capabilities (plugins) llm / image / video / tts / taps / publishing — tools
```

- **Capabilities** are deterministic tools behind a plugin contract (the
  existing ~20 entry-point groups + the declarative data-plane). Mechanism.
- **Skills** are procedures (instruction text + optional bundled scripts) in
  the [agentskills.io](https://agentskills.io) `SKILL.md` format. Judgment.
  They tell an LLM _when_ (the `description`, used for routing) and _how_ (the
  body) to use capabilities.
- **Modules** are departments: a manifest that binds the capabilities it needs,
  bundles the skills it runs, and adds business glue. (Module v1 already
  defines the manifest + migrations + routes + dashboards + probes shape.)
- **The architect** reads the skill/module catalog and composes a process for
  a given intent, rather than running one hard-coded pipeline.

---

## Four governing decisions

### 1. Own your interfaces; rent your implementations

Keep full control at the **seams** — the skill format, the module/plugin
contract, the MCP + CLI surface, the data schema — and at the **moat** —
editorial DNA, adversarial QA, the business loop. **Borrow** everything behind
those seams: orchestration (LangGraph), LLM routing (LiteLLM), retrieval
(LlamaIndex), QA rails (DeepEval/Guardrails/Ragas), and commodity skills from
the open ecosystems.

Because the contract is owned, any rented implementation is swappable without a
rewrite. This single rule buys simplicity (don't build commodity), control (no
lock-in), extendability (new function = new skill/plugin behind the same seam),
and the eventual product surface (the seam _is_ what others build on).

**Corollary — everything behind a seam is disposable.** Implementations are
temporary by design, including the ones we hand-rolled. If something out there
does a job better, the right move is to adopt it and throw out the local
version — the owned interface is exactly what makes that cheap and safe. Don't
defend hand-rolled code on sunk-cost grounds; defend the _seam_, and let the
thing behind it be replaced whenever a better option appears. The goal is
ultimate flexibility: a stable contract with maximally swappable internals.

### 2. A module is hybrid by maturity

One module **identity**, two fill-ins behind it:

- **Thin tier (default):** a manifest that binds capabilities + lists skills
  (mostly borrowed) + minimal glue. Standing up a department is largely
  _declaring which skills it uses_. This tier is the one the system can
  eventually author itself.
- **Graduated tier:** custom services replace skills **only where a moat
  emerges**. Promotion is earned, not assumed.

Content already graduated organically (it grew adversarial QA + an
edit-distance publish gate, so it earned custom code). Support / finance /
devops / compliance start thin; most never graduate. The contract must let a
module's `skills[]` be progressively swapped for `services/` **without changing
its identity, routes, or data** — graduation is a fill-in change, not a rewrite.

### 3. Earned autonomy

Self-development is governed by **trust dials, not a fixed gate.** Every
self-dev capability (author a skill, stand up a thin module, graduate a module,
raise a spend cap, …) has a **DB-tunable autonomy level** that starts at
_propose-only_ and **auto-promotes when it accumulates a clean, measured track
record** — zero rollbacks, tests green, cost in bounds. Full autonomy is the
asymptote these dials approach over years; it is never a single switch.

This generalizes a pattern already proven in content: the auto-publish gate
that withholds automation until a capability logs N near-zero-edit runs. Every
self-dev capability needs three things, mirroring that gate:

1. an **autonomy level** (an app_setting — runtime-tunable, not hard-coded),
2. **track-record telemetry** to earn promotion (measured outcomes), and
3. **uniform rollback / kill-switch rails** so any autonomous action is
   reversible.

Customer-facing publishing and spend increases remain human-gated independent
of these dials.

### 4. Tenancy via an `OperatorScope` seam

Design the **scope seam** now; defer the multi-tenant implementation.

- **Now:** single operator per deployment — **instance-per-customer** is the
  de-facto model (self-host the OSS, or run a managed isolated deployment).
  No shared-DB tenant plumbing.
- **Seam:** all data access flows through one injected `OperatorScope` object
  (today seeded from per-deployment config + niche scoping). Running with one
  implicit operator costs nothing extra.
- **Later (only if justified):** shared-DB multi-tenancy becomes an _additive_
  change at that one seam (populate a real tenant id + row-scope there), not a
  codebase-wide retrofit.

Capture the cheap 80% (scoped-access discipline); skip the expensive 20% (real
tenant plumbing) until there's reason to build it.

---

## Two skill layers, one format

Skills come in two layers that **share the agentskills.io `SKILL.md` format**
but have different consumers and runtimes. Both live in the repo-root `skills/`
tree, namespaced by pack — `skills/<pack>/<skill>/SKILL.md` (the industry-standard
layout):

| Layer               | Pack example                | Consumed by                         | Purpose                                                                |
| ------------------- | --------------------------- | ----------------------------------- | ---------------------------------------------------------------------- |
| **Operator skills** | `skills/poindexter/<verb>/` | the operator agent (Claude)         | _Drive_ the business — wrap the CLI/MCP (approve-post, cost-report, …) |
| **Pipeline skills** | `skills/content/<skill>/`   | the worker's `UnifiedPromptManager` | _Procedure text_ a module's stages use (research, blog-generation, …)  |

**Pack = module.** The operator toolset is the `poindexter` pack; each business
module owns a pack of pipeline skills (`content`, later `finance`, `support`).
The prompt loader scans the whole tree and registers only prompt-bearing skills
(those declaring `metadata.prompts`); operator action skills lack that block and
are silently ignored — they're a different layer, not prompt text.

## How pipeline skills fit the existing prompt stack

Adopting `SKILL.md` **replaces only the default-text layer** of prompt
resolution; it removes nothing.

- **Storage + routing →** `prompts/*.yaml` migrate to `skills/<name>/SKILL.md`.
  Skills gain a `description` (the architect's routing menu) and a home for
  bundled scripts — a superset of what flat YAML keys offered.
- **Runtime override + versioning →** unchanged. The prompt manager still
  resolves _override-surface → on-disk default_; the override surface still
  wins. The skill body is just the default it falls back to.
- **Tracing / observability →** unchanged and orthogonal. Tracing hooks the
  dispatch layer (every LLM call), not prompt storage. Zero impact.

Mechanically: the prompt manager gains a skill loader alongside its YAML loader;
the resolution chain is untouched. A `SKILL.md`'s frontmatter declares the
prompt keys it provides; the resolver reads them exactly as before.

---

## Build-vs-borrow stance

**Content is the one moat department; almost every other is borrow-heavy.**

| Department         | Stance                                                                    |
| ------------------ | ------------------------------------------------------------------------- |
| Content            | **Build the moat** — voice DNA, adversarial QA, publish gate. Graduated.  |
| Customer support   | Borrow — triage / reply-drafting / helpdesk skills exist widely           |
| Finance            | Borrow — bookkeeping / reconciliation / reporting skills exist            |
| DevOps             | Borrow — deploy / monitor / incident / self-healing `doctor` skills exist |
| Compliance / legal | Borrow — contract-review / policy-QA / PII-scan skills exist              |

So the design target is **a contract good enough that most departments are
configuration (skill assembly), not code** — and self-authorable once the
relevant autonomy dial is earned.

Commodity to adopt rather than build: the agentskills.io `SKILL.md` format; a
self-healing `doctor`; an energy/cost eval harness; skill importers; and
specific permissive skills (web research, scrape, summarize, structured
extraction, TTS) from the open ecosystems. Vet each borrowed skill's license.

---

## Seams to preserve in all new work

Every change from here should keep these intact, because they are what make the
endgame reachable incrementally:

1. **Skill format** — new procedures land as `SKILL.md`, not inline prompts.
2. **Module manifest** — new departments declare capabilities + skills; custom
   code is a later, earned fill-in.
3. **`OperatorScope`** — data access goes through the scope, even with one
   operator.
4. **Autonomy as an app_setting** — anything the system might one day do on its
   own is gated by a runtime-tunable trust level + track-record telemetry +
   reversible rails, never a hard-coded `if`.
5. **CLI/MCP first** — operator actions are CLI verbs with MCP parity, so both
   a human and the architect can drive them.

A stable contract on these five is the precondition for the system to develop
itself — that is the actual deliverable, more than any single feature.
