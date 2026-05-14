# Module v1 — making the plugin substrate explicit

**Status:** Phases 1, 2, 3-lite, 4-lite shipped on 2026-05-13. Phases 3.5 / 4.5 / 5 deferred — see "What shipped" below.
**Date:** 2026-05-13 (spec) / 2026-05-13 (implementation)
**Author:** brainstormed with Matt 2026-05-13 16:48 UTC
**Tracker:** [Glad-Labs/poindexter#490](https://github.com/Glad-Labs/poindexter/issues/490) (umbrella)
**Supersedes (when implemented):** the informal "module = collection of contributions across 19 entry-point groups" pattern.

## What shipped 2026-05-13

End-to-end validated against a real second module in the operator-overlay
slot (a private business module — see [Visibility](#visibility) below for
how `visibility="private"` modules are filtered from the public mirror).

| Phase   | Status      | What landed                                                                                                                                                                                                                                                                                                                                                                          |
| ------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Phase 1 | ✅ Full     | `plugins/module.py` Protocol + `ModuleManifest` + `get_modules()` registry accessor with name + manifest validation + duplicate-drop + first-discovered-wins precedence. 5 unit tests pin the contract.                                                                                                                                                                              |
| Phase 2 | ✅ Full     | `services/module_migrations.py` runner + `module_schema_migrations` table (compound key on `module_name, migration_name`) + boot wiring in `utils/startup_manager._run_migrations`. 6 unit + 2 integration_db tests.                                                                                                                                                                 |
| Phase 3 | ⚠️ Lite     | `src/cofounder_agent/modules/content/` skeleton (`ContentModule` class) registered via `_SAMPLES`. The 21-stage tree, `content_router_service`, `multi_model_qa`, `content_validator`, and content prompts STAY in substrate — physical pipeline-code moves deferred to **Phase 3.5** until a 2nd module gives us a comparison point. Avoids refactoring for sample-size-1 symmetry. |
| Phase 4 | ⚠️ Lite     | Route auto-discovery wired in `utils/route_registration.register_all_routes` — iterates `get_modules()` after substrate routes mount, calls each module's `register_routes(app)`. Grafana dashboard registration, CLI subparser registration, brain-probe registration deferred to **Phase 4.5**.                                                                                    |
| Phase 5 | ⏭ Deferred | `visibility` flag drives `scripts/sync-to-github.sh` — currently the sync filter strips private modules via an explicit pattern list (works for n=1 module; will refactor when n≥3).                                                                                                                                                                                                 |

**Concrete second module shipped under this pattern:** a
`visibility="private"` business module in the operator overlay,
filtered from the public mirror by `scripts/sync-to-github.sh`. The
substrate landed by writing the second module against the
freshly-shipped scaffolding, not by retrofitting old content code.

**The cost-benefit takeaway from shipping all four phases in one day:** the
Module v1 scaffolding (Phases 1 + 2) is small and high-leverage (~250 LOC). The
"lite" approach to Phases 3 + 4 avoids ~100 file import-path churn by deferring
physical moves until a 3rd module concretely demands them. Total ratio:
~700 LOC of scaffolding + glue now supports a 1-day path to "add a new
business module" (`modules/<name>/` + `_SAMPLES` registration + migrations).

## Why

Poindexter's destination is a "cofounder OS run from phone-on-beach"
(`project_poindexter_as_business_os`). Content generation is module 1
of N — finance, legal, HR, customer-support, and revenue modules are
expected follow-ons (`project_llm_workforce_thesis`).

Today's architecture has accreted three pain points that block adding
module 2:

1. **Fuzzy module boundaries.** The content pipeline calls into
   `ai_content_generator.py` (1,331 LOC) which calls back into the
   stages tree; `brain/` has its own embeddings outside the main
   migration system (Glad-Labs/poindexter#328); the "Content Module"
   isn't a thing you can install or describe in one sentence.
2. **OSS / business mix is informal.** `scripts/sync-to-github.sh`
   filters by a hand-maintained path list (`web/public-site`,
   `web/storefront`, `mcp-server-gladlabs`, `marketing`, premium
   dashboards, `writing_samples`, `.shared-context`, `CLAUDE.md`).
   The line keeps moving; a new private-overlay file requires editing
   the sync filter, which is easy to forget.
3. **Pipeline orchestration drag.** A new module currently needs
   plumbing across 6+ surfaces — Prefect flow + Telegram command +
   MCP tool + Grafana panel + brain probe + CLI subcommand. Each
   surface has its own registration path. Adding a finance module
   feels like a five-day plumbing project.

The redesign explicitly does **not** reshuffle the substrate
(brain, Prefect, Langfuse, cost guard, Telegram bot, MCP server,
Grafana, audit_log, memory client). Those are working and load-
bearing. The redesign makes the **module** abstraction first-class
on top of that substrate.

## The 70% already built

An audit of `src/cofounder_agent/plugins/` on 2026-05-13 found:

- **Plugin registry exists** via setuptools `entry_points()` —
  `plugins/registry.py`, no custom registry code, same mechanism
  pytest / click / flask use. 19 entry-point groups defined:
  `taps`, `probes`, `jobs`, `stages`, `reviewers`, `adapters`,
  `providers`, `packs`, `llm_providers`, `topic_sources`,
  `image_providers`, `audio_gen_providers`, `video_providers`,
  `tts_providers`, `caption_providers`, `publish_adapters`.
- **Plugin Protocol interfaces** are formalised with
  `@runtime_checkable` Protocols in `plugins/{tap,stage,reviewer,
adapter,probe,job,llm_provider,pack,image_provider,
audio_gen_provider,video_provider,tts_provider,caption_provider,
media_compositor,publish_adapter,topic_source}.py`. Each has a
  typed `Result` / `Document` / `Completion` data class.
- **Sample plugins** at `plugins/samples/` —
  `database_probe.py`, `hello_tap.py`, `noop_job.py` — work as
  reference implementations.
- **Substrate services** are wired and load-bearing:
  `services/cost_guard.py`, `services/prompt_manager.py`
  (UnifiedPromptManager + Langfuse), `services/site_config.py`
  DI seam, `services/audit_log.py`, `services/memory_client.py`,
  the brain daemon (`brain/`).
- **Operator surface** is in place: a single Telegram bot
  (`poindexter-pipeline-bot` container, 24/7), the MCP server
  (25 tools), the FastAPI worker, the `poindexter` CLI.
- **Orchestration** is canonical: Prefect, as of the Stage 3
  cutover (2026-05-13, `docs/architecture/prefect-cutover.md`).
- **Declarative data plane** — 5 tables (`external_taps`,
  `retention_policies`, `webhook_endpoints`, `publishing_adapters`,
  `qa_gates`) feed 14 handlers across 5 surfaces.

What's missing is the **module-level** layer above these plugin
groups. A "Content Module" today is implicit — N stages + M
reviewers + K topic_sources + L probes + … scattered across the
registry. There is no manifest that says "these are its parts,
this is its version, these are its dependencies, this is whether
it's public or overlay-private."

## What changes — Module v1 in five components

### Component 1 — Module manifest + new entry-point group

Add a new entry-point group, `poindexter.modules`. Each module is a
Python package whose `pyproject.toml` declares **one** entry:

```toml
[project.entry-points."poindexter.modules"]
content = "poindexter_module_content:ContentModule"
```

The target resolves to a `Module` instance. `plugins/module.py`
defines the protocol:

```python
@runtime_checkable
class Module(Protocol):
    """A self-contained business function (content, finance, HR, ...).

    A Module BUNDLES the lower-level plugin contributions (stages,
    reviewers, probes, jobs, taps, adapters, providers, packs) plus
    the things the existing registry doesn't track (DB migrations,
    Grafana panels, HTTP routes, CLI commands).
    """

    name: str                       # canonical slug, e.g. "content"
    version: str                    # semver
    visibility: Literal["public", "private"]
    requires: list[str]             # ["substrate>=1.0", "module:memory"]

    def manifest(self) -> ModuleManifest: ...

    async def migrate(self, pool: asyncpg.Pool) -> None:
        """Apply this module's DB migrations. Idempotent."""

    def register_routes(self, app: FastAPI) -> None:
        """Mount this module's HTTP surface on the host app."""

    def register_cli(self, parser: CLISubparser) -> None:
        """Register `poindexter <module> <subcommand>` entries."""

    def register_dashboards(self, grafana: GrafanaProvisioner) -> None:
        """Contribute Grafana panels under a per-module folder."""

    def register_probes(self, brain: BrainProbeRegistry) -> None:
        """Brain probes specific to this module."""

    async def healthcheck(self, pool: asyncpg.Pool) -> ProbeResult:
        """Module-wide health (sums sub-probe results)."""
```

Importantly, the Module is _just a bundler_. The individual stages,
reviewers, probes, etc. continue to be discovered through their
existing entry-point groups. A Module's job is to be the unit of
install / version / dependency / OSS-visibility.

### Component 2 — Per-module migrations

Each module owns a `migrations/` subdirectory inside its package:

```
poindexter_module_content/
├── __init__.py
├── module.py            # ContentModule class
├── migrations/
│   ├── 20260514_120000_init.py
│   ├── 20260520_080000_add_dev_diary_template.py
│   └── ...
├── prompts/
├── flows/
├── routes/
├── dashboards/
└── probes/
```

`Module.migrate(pool)` walks the module's `migrations/` directory
using the same idempotent runner as `services/migrations/` today,
recording applied migrations in a `module_schema_migrations` table
keyed on `(module_name, migration_name)` so two modules can have a
migration named `init.py` without colliding.

Global migrations under `services/migrations/` continue to exist —
they apply to substrate tables (`app_settings`, `audit_log`,
`embeddings`, `schema_migrations`, …) that all modules share.

### Component 3 — Per-module Grafana dashboards

Each module places its dashboard JSON files under `dashboards/` in
its package. At boot, the substrate's `GrafanaProvisioner` walks
every loaded module, copies the dashboards into a per-module folder
in Grafana (e.g. `/folder/content/`, `/folder/finance/`), and
preserves a "Substrate" top-level folder for cross-module dashboards
(Mission Control, System Health).

The existing 7 dashboards under `infrastructure/grafana/` get split:
Pipeline + Auto-Publish Gate + QA Rails → `content/` folder;
Mission Control + Observability + System Health + Cost stays at the
substrate root.

### Component 4 — HTTP route auto-discovery

`Module.register_routes(app)` runs at lifespan startup. Each module's
routes mount under a per-module path prefix derived from
`Module.name`:

```python
# in poindexter_module_content.module
def register_routes(self, app: FastAPI) -> None:
    from .routes import tasks, approval, posts
    app.include_router(tasks.router, prefix="/api/content/tasks", tags=["content"])
    app.include_router(approval.router, prefix="/api/content/approval", tags=["content"])
    app.include_router(posts.router, prefix="/api/content/posts", tags=["content"])
```

The substrate's `main.py` no longer hardcodes route imports for
business modules. It iterates the module registry and calls
`register_routes` on each.

### Component 5 — `visibility` flag replaces the sync filter

Each module declares `visibility: Literal["public", "private"]` on
its manifest. The build process for the public mirror is now:

1. Walk `poindexter.modules` entry-points.
2. Include modules with `visibility="public"` in the OSS sync.
3. Exclude modules with `visibility="private"`.

`scripts/sync-to-github.sh` becomes a thin loop over the module
manifest rather than a hand-maintained path list. New private code
is private by default (a new module is created in a private
overlay package), and going public is a one-line manifest change
plus a code review for what's in the package.

## The OSS / business split made concrete

After Module v1:

- **`poindexter` (public OSS)** ships:
  - the substrate (brain, prefect, langfuse, cost_guard, prompt_manager,
    site_config, audit_log, memory_client, telegram-bot, MCP server, CLI)
  - the **`content` module** as the reference implementation:
    canonical_blog template, multi-model QA, programmatic validator,
    SDXL/Pexels image stage, publish to a self-hosted Next.js + DB.
  - the **`base` module** that provides shared utilities every
    module needs (admin settings UI, audit_log explorer, healthcheck
    aggregator). Could also be inlined into substrate; the spec
    treats it as a module for symmetry.

- **`glad-labs-stack` (private overlay)** ships:
  - the **`gladlabs-business`** module (renamed from the current
    `mcp-server-gladlabs/` + `web/storefront/` + marketing scripts):
    Lemon Squeezy customer lookup, premium-prompts seeder, Glad-Labs
    brand assets, R2 license-delivery API, the storefront Next.js app.
  - `web/public-site/` stays here too (Vercel deploys from this repo).
  - the Glad-Labs Postgres + Grafana + Telegram bot instances —
    deployment artifacts that depend on these modules but aren't
    distributed.

The line "is this poindexter-public or glad-labs-private?" becomes
"which module owns it?" — and `Module.visibility` answers in one
field.

## Migration path from today

The work fits in 5 child issues. Each is independently shippable
and provides observable value before the next one starts. Order
matters — components 1 and 2 unblock the others.

### Phase 1 — Module manifest + registry (~1 day)

- Define `plugins/module.py` with the `Module` Protocol and a
  `ModuleManifest` data class.
- Add the `poindexter.modules` entry-point group constant.
- Extend `plugins/registry.py` with `get_modules() -> list[Module]`.
- Pin behaviour with 5 unit tests (module discovery, manifest
  validation, dependency cycles).

No business behaviour changes yet — this is _scaffolding_.

### Phase 2 — Per-module migration runner (~1 day)

- New `services/module_migrations.py` runner. Same shape as
  `services/migrations/__init__.py` but keyed on
  `(module_name, migration_name)`.
- New `module_schema_migrations` table.
- Boot wiring: after substrate migrations apply, iterate registered
  modules and call `await module.migrate(pool)`.

### Phase 3 — Convert content/ into a Module (~1-2 days)

- Create `poindexter_module_content/` package (could be in-tree
  initially under `src/poindexter_module_content/` then split out
  to its own repo when the substrate stabilises).
- Move existing content-pipeline code: `services/content_router_service.py`,
  the stages tree, `multi_model_qa.py`, `content_validator.py`,
  the content-specific YAML prompts (the `qa.*` / `image.decision` /
  `topic.ranking` / `narrative.*` keys that landed via Lane A
  `Glad-Labs/poindexter#450`), the `canonical_blog` LangGraph
  template, the `content_generation` Prefect flow + deployment.
  `prompt_manager.py` (UnifiedPromptManager — the loader) stays in
  substrate; only the prompt files move.
- Move content-specific migrations from `services/migrations/` to
  `poindexter_module_content/migrations/`.
- Register the `ContentModule` via entry-point.
- All existing tests keep passing; the change is structural, not
  behavioural.

### Phase 4 — Per-module routes + dashboards (~1 day)

- Refactor `main.py` to iterate `get_modules()` and call
  `register_routes(app)` for each.
- Refactor `infrastructure/grafana/` into per-module folders.
- Add `Module.register_dashboards(provisioner)` to seed.
- Update Grafana provisioning workflow.

### Phase 5 — `visibility` + sync rewrite (~0.5 day)

- Add `visibility` field to manifest.
- Rewrite `scripts/sync-to-github.sh` (or replace with a Python
  script) to iterate modules and include only `visibility="public"`.
- Document the new sync contract in `docs/operations/oss-sync.md`.

Total: ~5 days of focused work. After Phase 5, **adding a finance
module is**: create `poindexter_module_finance/`, declare its
entry-point, write its `migrate()` / `register_routes()` /
`register_dashboards()` / `register_probes()`, ship.

## Non-goals

The spec deliberately does NOT include:

- **Module sandboxing / process isolation.** Modules run in the
  same Python process as today. If a future module needs isolation
  (third-party untrusted code), that's a separate spec.
- **Module hot-reload.** Modules are discovered at boot. Adding a
  module still requires a restart. Hot-reload doesn't compose with
  Python's import semantics.
- **A plugin marketplace.** Distribution is `pip install <package>`
  for now. A marketplace can come later if there's demand.
- **Cross-module RPC.** Modules continue to communicate through
  shared substrate (Postgres, audit_log, brain knowledge graph) —
  no new RPC layer.
- **Versioned migrations across modules.** A module that depends on
  another module's tables can declare `requires: ["module:content>=1.0"]`
  but the runner does not yet enforce migration ordering across
  modules. If module B needs a table from module A, ensure A is
  installed first. This may need revisiting once we have 3+ modules.

## Testing strategy

Each phase ships with tests:

- **Phase 1** — `tests/unit/plugins/test_module_registry.py`: 5
  cases covering discovery, manifest validation, missing fields,
  duplicate module names, dependency cycle detection.
- **Phase 2** — `tests/integration_db/test_module_migrations.py`:
  fresh `poindexter_test_<hex>` DB, register two test modules,
  verify both their migrations apply, verify
  `module_schema_migrations` rows land correctly, verify reruns
  are no-ops.
- **Phase 3** — `tests/integration_db/test_content_module_e2e.py`:
  with the content module registered, run one pipeline_tasks row
  end-to-end through the Prefect flow. Should produce the same
  awaiting_approval outcome as today's path. Pin the contract.
- **Phase 4** — `tests/unit/routes/test_module_route_discovery.py`:
  spin up a `TestClient` with two test modules registered, verify
  both modules' routes are mounted at their declared prefixes.
- **Phase 5** — `tests/unit/scripts/test_visibility_filter.py`:
  feed the sync filter a fake module registry with mixed visibility,
  verify only `visibility="public"` modules survive the filter.

The integration_db harness (fixed 2026-05-10 via
`fix(migrations): reconcile embeddings column drift on stripped
DB`, commit 4330e59f) gives us the per-test disposable Postgres
we need for Phase 2 + 3.

## Open questions / future work

- ~~**Naming.** Is `Module` the right word, or does it collide with
  Python's existing `module` semantics in confusing ways? Maybe
  `Workspace` or `Domain` or `Slice`? Defer until Phase 1 review.~~
  **Resolved 2026-05-13:** keep `Module`. Matt: "modules in reference
  to Python files etc. is very broad — we'll know the difference."
- **Sub-modules.** Should `gladlabs-business` further decompose into
  `gladlabs-storefront`, `gladlabs-licenses`, `gladlabs-premium`?
  Not now — get one private overlay module shipping first.
- **In-tree vs out-of-tree packages.** Phase 3 puts the content
  module package in-tree (`src/poindexter_module_content/`) for
  convenience. Once the contract is stable, extracting to its own
  repo + `pip install` is straightforward. Defer the split until
  there are 2 in-tree modules.

## Ground truth references

- `src/cofounder_agent/plugins/registry.py` — existing entry-point
  discovery, basis for Component 1.
- `src/cofounder_agent/plugins/{tap,stage,probe,job,...}.py` —
  existing plugin Protocols that modules will bundle.
- `src/cofounder_agent/services/migrations/__init__.py` — runner
  shape that Component 2 will mirror.
- `docs/architecture/prefect-cutover.md` — recent successful
  cutover pattern (Phase 0 → 5) to mirror for Module v1.
- `docs/architecture/langgraph-cutover.md` — same pattern.
- `scripts/sync-to-github.sh` — sync filter Component 5 replaces.
- `CLAUDE.md` — operator-facing description; will need an update
  when Phase 3 lands.
