# SiteConfig: per-module singleton → constructor DI (Application Container)

**Status:** design v2 — 2026-05-28 (v1 proposed ContextVar; superseded after the future-proofing review)
**Closes:** the latent bug class where every new entry point silently fails because it forgot to wire SiteConfig into N service modules. Most recent victim: `poindexter topics sweep --niche glad-labs` (2026-05-28).
**Aligns with:** Module v1 destination (each business module owns its dependencies as a composable unit) + future multi-tenant SaaS posture (per-request / per-tenant SiteConfig).

## Why DI, not ContextVar

Both options fix the bug class. The differentiator is what the codebase becomes downstream:

- **ContextVar** bakes in "one SiteConfig per process / per async-task" as an architectural assumption. Multi-tenant SaaS would have to set the var per-request (works, but implicit); Module v1's composability means modules read process-global state instead of owning their deps.
- **Pure DI** matches the Module v1 destination: `ContentModule(site_config, pool, ...)`, `SomeOtherModule(site_config, pool, third_party_client)`. A future multi-tenant world is just "construct different containers per tenant" — no architectural surgery.

Matt picked pure DI 2026-05-28 with the explicit framing of doing it "nice once" instead of doing it twice. The migration is ~30-40 focused hours; doing it now while the codebase is small avoids paying that bill later when there are 3-5 business modules instead of 2.

## The bug class (recap)

Current pattern (44 service modules):

```python
# services/some_service.py
from services.site_config import SiteConfig
site_config: SiteConfig = SiteConfig()  # empty default, module-level singleton

def set_site_config(sc: SiteConfig) -> None:
    global site_config
    site_config = sc
```

Worker process wiring: `di_wiring.wire_site_config_modules(loaded)` iterates 44 modules and calls `set_site_config(loaded)` on each. **Every new entry point (CLI subcommand, Prefect subprocess, script) has to remember to either call `wire_site_config_modules` or carry its own wiring.** Forget → modules silently use the empty default → `site_config.get(key, "")` returns empty string → downstream code branches on stale values without ever knowing.

## The destination

Two complementary primitives:

### 1. `SiteConfig` as a constructor dependency (no module state)

Every service that today reads `site_config.get(...)` becomes a class that takes `site_config: SiteConfig` in `__init__`:

```python
# services/some_service.py — after migration
from services.site_config import SiteConfig

class SomeService:
    def __init__(self, *, site_config: SiteConfig, pool: Any, **kwargs):
        self._site_config = site_config
        self._pool = pool

    async def do_thing(self):
        threshold = self._site_config.get_int("foo_threshold", 10)
        ...
```

No `site_config = SiteConfig()` module global. No `set_site_config()` setter. Reading site_config without constructing the class with one is a `TypeError` at the call site — caught in tests, CI, IDE.

### 2. `AppContainer` — composition root + wiring

A single dataclass-shaped container that holds every service the application needs, wired with the right dependencies. Constructed ONCE per entry point (worker lifespan, CLI command, Prefect subprocess, brain daemon, test fixture).

```python
# services/container.py
from dataclasses import dataclass, field
from typing import Any

from services.site_config import SiteConfig
from services.topic_batch_service import TopicBatchService
from services.internal_rag_source import InternalRagSource
from services.publish_service import PublishService
# ... etc

@dataclass
class AppContainer:
    """Composition root: every service the app needs, wired together."""
    site_config: SiteConfig
    pool: Any  # asyncpg.Pool

    # Lazily-constructed singletons — each property memoises after first call.
    @cached_property
    def topic_batch_service(self) -> TopicBatchService:
        return TopicBatchService(
            site_config=self.site_config,
            pool=self.pool,
            internal_rag_source=self.internal_rag_source,
        )

    @cached_property
    def internal_rag_source(self) -> InternalRagSource:
        return InternalRagSource(
            site_config=self.site_config,
            pool=self.pool,
        )

    # ... one cached_property per service
```

The container's role: be the SINGLE place where the wiring graph lives. Entry points construct the container; everything downstream just reaches into it.

```python
# main.py lifespan
async def startup():
    site_config = await load_site_config_from_db(pool)
    container = AppContainer(site_config=site_config, pool=pool)
    app.state.container = container
    # No 44-iteration set_site_config loop. No di_wiring module.

# A route handler
@router.get("/foo")
async def foo(container: AppContainer = Depends(get_container)):
    return await container.topic_batch_service.do_thing()

# A CLI command
@click.command()
def sweep(niche: str):
    async def _impl():
        pool = await asyncpg.create_pool(...)
        site_config = await load_site_config_from_db(pool)
        container = AppContainer(site_config=site_config, pool=pool)
        await container.topic_batch_service.run_sweep(...)
    asyncio.run(_impl())
```

Forgetting to construct the container → `TypeError` when the entry point tries to call any service. Fail-loud at the entry point, not three layers deep.

## Strategy: leaf-first incremental migration

A big-bang PR converting all 44 services is the wrong shape: massive conflicts with anything in flight, hard to review, hard to roll back. Instead, migrate service-by-service in dependency order — leaves first.

### Definition: leaf service

A service is a "leaf" when no other un-converted service depends on it. Once a service is converted, callers that haven't been converted yet receive their `SomeService` instance via a temporary backcompat shim (see below).

### Backcompat shim period

During the migration, both patterns coexist. Each un-migrated module retains its `site_config` module-level singleton AND its `set_site_config` setter. Each migrated module is a class with constructor DI. The bridge:

- The container exposes every service it knows about.
- For each not-yet-migrated module, we keep the old `set_site_config(loaded)` call in `main.py`'s lifespan (and Prefect subprocess init, etc.) until the module is migrated.
- For each migrated module, the container constructs it once and any pre-migration caller that imports the module global gets a `RuntimeError` from `module.site_config` lookups that no longer exist (we delete the module global on migration day per-module).

This means the migration ships in slices — each PR migrates one service end-to-end (the service itself, all its direct callers, its tests). The codebase stays shippable every PR.

### Migration order

PR 1 establishes the pattern + container scaffold (no services migrated). PRs 2-N each migrate one service or one tight cluster. Order is rough — actual order picked by the agent dispatching the migration after a dependency scan.

Loose tiers (leaf-first):

**Tier 1 — pure leaves (likely first PRs)**:

- `redis_cache`, `r2_upload_service`, `telegram_config`, `decorators`, `ollama_client`
- These don't import other `services/*` modules at runtime; clean conversion.

**Tier 2 — one-hop**:

- `image_service`, `url_validator`, `url_scraper`, `web_research`, `seed_url_fetcher`
- Each depends on 1-2 tier-1 services. After tier 1 lands, these are leaves.

**Tier 3 — pipeline services**:

- `content_validator`, `quality_service`, `self_review`, `multi_model_qa`, `internal_rag_source`, `research_service`, `template_runner`, `publish_service`, `topic_batch_service`, `content_router_service`, `prompt_manager`, `ai_content_generator`, `social_poster`, `newsletter_service`, etc.
- These are the bulk of the migration (~25 services). Order within the tier picked by counting incoming edges.

**Tier 4 — entry points + cleanup**:

- `main.py` lifespan: replace `wire_site_config_modules(loaded)` with `container = AppContainer(site_config=loaded, pool=pool); app.state.container = container`.
- Prefect flow init: same swap.
- Brain daemon: same swap.
- CLI: every command constructs its container at the top of `_impl()`. The topics CLI gets the canary wire-up that proves the bug Matt hit is fixed.
- Delete `services/di_wiring.py` (no callers remain).
- Delete the `services/site_config.py` module-level singleton + the shared_context fallback in `services/integrations/shared_context.py`.
- Update `CLAUDE.md` config section.
- Update / retire `feedback_module_singleton_gotcha.md` memory.

### Test fixture pattern

Every test that currently does `set_site_config(test_cfg)` switches to constructing the service it's testing directly with a stub SiteConfig:

```python
# OLD
def test_thing():
    set_site_config(SiteConfig(initial_config={"foo": "bar"}))
    result = some_module.some_free_function(x, y)
    assert result == ...

# NEW
def test_thing():
    site_config = SiteConfig(initial_config={"foo": "bar"})
    svc = SomeService(site_config=site_config, pool=fake_pool)
    result = await svc.do_thing(x, y)
    assert result == ...
```

A pytest plugin / fixture in `tests/conftest.py` can provide a `default_container` fixture that hands tests a fully-wired container with stub dependencies — used by tests that want the wiring done for them.

### Per-PR shape

Each migration PR has the same shape (template the agent follows):

1. Convert the target service from free functions / module-global SiteConfig to a class with constructor DI.
2. Update the container to expose it via a `cached_property`.
3. Update every caller (other services, route handlers, CLI commands, stages, jobs) to reach the service via the container or to receive it as a constructor dep themselves if they're already migrated.
4. Delete the module-level `site_config: SiteConfig = SiteConfig()` + the `set_site_config(...)` setter from the migrated module.
5. Remove the migrated module's name from `di_wiring.WIRED_MODULES`.
6. Update tests for the migrated service + any tests for callers that change.
7. Run full pytest, fix breakage.
8. Update `CLAUDE.md` if the service appears in the load-bearing services table.

## Migration phases (PR plan)

1. **PR 1 — pattern + container scaffold** (~150 LOC, 1-2 hr)
   - Add `services/container.py` with `AppContainer` dataclass + an initial scaffolding that exposes ZERO services (every method raises `NotImplementedError` until its service is migrated, OR the property is added as services migrate).
   - Add a helper `services.bootstrap.build_container(pool)` that loads SiteConfig from the DB and constructs the container.
   - Unit tests that the container constructs cleanly and that `get_site_config()`-style "did you wire it?" errors are loud at the right boundary.
   - No services migrated yet — old pattern still operative everywhere.

2. **PR 2 — entry-point wire-up** (~5 files, 1-2 hr)
   - `main.py` lifespan: also build the container alongside the old `wire_site_config_modules(loaded)` call. Both wirings coexist during the migration; new code reaches into the container; old code keeps using the per-module singleton.
   - Prefect subprocess init: same.
   - Brain daemon init: same.
   - CLI: build container in `_impl()` of each command; topics sweep gets the canary wire-up (proves the bug Matt hit is fixed for any service that's already in the container).
   - Pytest: add `default_container` fixture in conftest.

3. **PR 3-N — service migration** (1 service per PR, ~25-40 PRs, mostly mechanical)
   - Each PR follows the per-PR shape above.
   - Dispatch as separate agents in worktrees, in parallel where dependencies allow (cap at 3 concurrent per `feedback_max_3_agents`).
   - Order: tier 1 → tier 2 → tier 3 (see above).

4. **Final PR — cleanup**
   - Delete `services/di_wiring.py`.
   - Delete the `services/site_config.py` module-level instance + the integrations/shared_context fallback.
   - Update `CLAUDE.md` (replace the "Singleton deleted 2026-05-09 ... per-module set_site_config" language with "constructor DI via `AppContainer`").
   - Retire `feedback_module_singleton_gotcha.md` memory (or update to "obsolete — replaced by constructor DI 2026-MM-DD").
   - Update the "Configuration" key principle in CLAUDE.md.

## What this fixes

- Forgetting to wire SiteConfig in a new entry point → `TypeError` when constructing the service, OR `AttributeError` if the entry point tries to use a service without constructing the container.
- Tests have zero shared state — each constructs the services it needs with the stubs it wants.
- Multi-tenant future is "construct different containers per tenant request" — clean fit.
- Module v1 future: each `Module` becomes its own container subgraph; `AppContainer` composes them.
- The 44-name `WIRED_MODULES` tuple goes away entirely.

## What this doesn't fix

- `SiteConfig.get_secret(key)` stays async + per-call DB read — unchanged.
- Services that contain heavy free-function pure utilities (`utils/*.py`) that don't read settings stay free functions. Only the settings-reading paths become classes.
- Free functions inside services modules that don't read site_config can stay free functions OR become @staticmethod / @classmethod on the service class. Agent picks per-case based on logical grouping.

## Risks

- **Volume** — ~25-40 PRs is a lot. Mitigated by mechanical-ness of each PR + parallel agents. Worst case the migration takes 2-3 calendar days of agent work.
- **Conflicts with in-flight Phase 1 lab work** — the lab harness PRs (#699, #702, +3 more) touch many of the same files. Sequencing: finish Phase 1 first (~3 more PRs), then start the DI migration. The design doc itself can land now; the implementation PRs wait for Phase 1 to clear.
- **Container surface ergonomics** — if `AppContainer` ends up with 44 cached properties, that's a fine starting point; refactor into sub-containers (per-Module) when there are 3+ business modules and it actually helps.
- **`site_config` mutation through the in-process refresh job** — the existing scheduled `reload_site_config` job mutates the loaded SiteConfig's `_config` dict in place. Container holds the SAME instance, so mutations propagate. Verify with a test.

## Why NOT ContextVar (the alternative considered)

ContextVar would have fixed the bug class in ~50 file touches (vs ~200) but kept "process-global SiteConfig" as an implicit assumption. The Module v1 destination wants each module to own its dependencies; ContextVar fights that. Pure DI matches the destination shape, even though the migration is 4-5x bigger.

The choice was made 2026-05-28 with the explicit framing of doing it nice once instead of twice.
