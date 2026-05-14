# Extending Poindexter

**Last Updated:** 2026-04-23

How to add new capabilities to Poindexter without forking the monorepo
or touching 1,000-line files. Every extension point below corresponds
to a Protocol in `src/cofounder_agent/plugins/`; the plugin architecture
umbrella ([GH-64](https://github.com/Glad-Labs/poindexter/issues/64))
covers the long-range roadmap.

This guide is prescriptive. If you want design rationale, read
[`architecture/plugin-architecture.md`](../architecture/plugin-architecture).

---

## Quick picker

| I want to...                                              | Add a...     | Protocol                        | Example                                    |
| --------------------------------------------------------- | ------------ | ------------------------------- | ------------------------------------------ |
| Run a new step in the content pipeline                    | **Stage**    | `plugins/stage.py::Stage`       | `services/stages/writer_self_review.py`    |
| Score a draft against a new quality rule                  | **Reviewer** | `plugins/stage.py::Reviewer`    | `services/content_validator.py`            |
| Publish finished posts to a new social platform           | **Adapter**  | `plugins/stage.py::Adapter`     | `services/social_adapters/bluesky.py`      |
| Generate media (image / audio / video) from a new engine  | **Provider** | `plugins/stage.py::Provider`    | `services/providers/sdxl.py` (in-progress) |
| Ingest content ideas from a new source (API, file, queue) | **Tap**      | `plugins/tap.py::Tap`           | `services/topic_sources/hackernews.py`     |
| Run a background probe for health / business metrics      | **Probe**    | `plugins/probe.py::Probe`       | `brain/health_probes.py`                   |
| Schedule a recurring background task                      | **Job**      | `plugins/job.py::Job`           | `services/jobs/reload_site_config.py`      |
| Swap the LLM backend (Ollama → vLLM / OpenAI / Claude)    | **Provider** | `plugins/provider.py::Provider` | Phase J, tracked at GH-104                 |
| **Add an entire business function (finance, HR, ...)**    | **Module**   | `plugins/module.py::Module`     | `src/cofounder_agent/modules/content/`     |

**Capability plugins vs business modules.** Every row above the last is a
_capability plugin_ — a discrete piece (one tap, one provider, one stage) the
substrate consumes directly. The last row is a _business module_ — a
manifest+lifecycle bundle that composes several capability plugins into a
self-contained business function. Most extensions are capability plugins; you
only need a Module when you're adding a new business surface (finance,
customer support, ops/security, HR), not just a new step inside an existing
business surface. See [Module v1](../architecture/module-v1.md) for the full
contract; section 9 below walks through adding one.

Each column below describes the full "how" per extension type.

---

## 1. Adding a Stage

A **Stage** is a pipeline step that runs on a single content task.
Stages chain via `StageRunner`; order lives in the
`pipeline.stages.order` app_setting.

### 1a. Minimum viable Stage

Create `src/cofounder_agent/services/stages/my_stage.py`:

```python
from typing import Any
from plugins.stage import StageResult


class MyStage:
    name = "my_stage"
    description = "One-line description of what this stage does."
    # Optional: override default timeout (default 120s)
    timeout_seconds = 60
    # Optional: whether a failure should halt the chain (default True)
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        # Read from context. Common keys: task_id, topic, content, site_config.
        topic = context.get("topic", "")

        # Do the work. Any LLM/DB/IO calls go here.
        updated_content = do_something(topic)

        # Return. context_updates is shallow-merged into the shared context.
        return StageResult(
            ok=True,
            detail=f"stage ran for topic={topic!r}",
            context_updates={"my_stage_output": updated_content},
        )
```

### 1b. Register it

Add to `src/cofounder_agent/plugins/registry.py` — the stage must be
importable by name. The registry is the gateway; stages not registered
are invisible to StageRunner.

### 1c. Slot it into the pipeline order

```sql
-- Insert after cross_model_qa, before finalize_task
UPDATE app_settings
SET value = '["verify_task", "generate_content", "writer_self_review",
              "quality_evaluation", "url_validation", "replace_inline_images",
              "source_featured_image", "cross_model_qa", "my_stage",
              "generate_seo_metadata", "generate_media_scripts",
              "capture_training_data", "finalize_task"]'
WHERE key = 'pipeline.stages.order';
```

Or use the CLI:

```bash
poindexter settings set pipeline.stages.order '["verify_task", ...]'
```

No worker restart needed — the orchestrator reloads the stage order
each task.

### 1d. Stage-specific config

Each stage reads its config from `plugin.stage.<name>` in app_settings.
Standard fields honored by the runner:

- `enabled` (bool, default true) — runtime off-switch
- `timeout_seconds` (int) — per-invocation deadline
- `halts_on_failure` (bool) — whether to abort the chain on error

Custom fields (whatever your stage needs) live alongside. Example:

```json
{
  "enabled": true,
  "timeout_seconds": 90,
  "halts_on_failure": false,
  "my_stage_model": "ollama/qwen3:8b",
  "my_stage_max_tokens": 1024
}
```

### 1e. Test it

Every stage ships with a unit test that builds a fake context + config
and asserts the returned `StageResult`. Model the test on
`tests/unit/services/stages/test_*.py`. Test that `halts_on_failure`
and `timeout_seconds` are honored under the failure paths you care about.

---

## 2. Adding a Reviewer

A **Reviewer** produces a score (0-100) and a pass/fail judgment on a
draft. Reviewers run inside the `cross_model_qa` stage and contribute to
the weighted final score.

```python
from plugins.stage import ReviewerResult


class MyReviewer:
    name = "my_quality_check"
    description = "Checks for specific brand-voice violations."

    async def review(
        self,
        title: str,
        content: str,
        topic: str,
        context: dict,
    ) -> ReviewerResult:
        violations = my_rule_engine(content)
        if not violations:
            return ReviewerResult(
                reviewer="my_quality_check",
                approved=True,
                score=95,
                feedback="no violations found",
                provider="programmatic",
            )
        return ReviewerResult(
            reviewer="my_quality_check",
            approved=False,
            score=max(0, 100 - len(violations) * 10),
            feedback="; ".join(v.description for v in violations[:3]),
            provider="programmatic",
        )
```

### How your score is weighted

Weights live in `app_settings`:

- `qa_validator_weight` (default 0.4) — programmatic reviewers
- `qa_critic_weight` (default 0.6) — LLM critics
- `qa_gate_weight` (default 0, was 0.3) — binary pass/fail gates

Pick the right `provider` string. The aggregator maps provider → weight:

| Provider string                                                    | Weight source         |
| ------------------------------------------------------------------ | --------------------- |
| `programmatic`                                                     | `qa_validator_weight` |
| `anthropic`, `google`, `ollama`                                    | `qa_critic_weight`    |
| `consistency_gate`, `url_verifier`, `vision_gate`, `web_factcheck` | `qa_gate_weight`      |

If your reviewer answers a binary question (URL resolves / fact
verified / layout passes), use a gate provider and let `qa_gate_weight=0`
keep it as pure veto. If it produces a meaningful graded score, use
`programmatic`.

### Register

Add to `services/multi_model_qa.py::MultiModelQA.review()` in the
reviewer-assembly section. Future plugin-architecture work (Phase E)
will move this to an entry-point discovery.

---

## 3. Adding an Adapter (new publishing platform)

Adapters publish finished posts to external platforms. They sit after
the approval gate, in the `publish_post` flow rather than the pipeline
itself.

Existing adapters live in `services/social_adapters/`:

- `bluesky.py` — working
- `threads.py` — working
- `linkedin.py`, `reddit.py`, `youtube.py` — stubbed (`NotImplementedError`, tracked at GH-40)

### Minimum shape

```python
class MyAdapter:
    name = "my_platform"
    description = "Posts to my platform's API."

    async def publish(
        self,
        post: PostRecord,
        config: dict[str, Any],
    ) -> AdapterResult:
        # Transform the post for this platform's format
        payload = self._format(post)

        # Call the platform's API
        try:
            response = await self._http_post(config["api_url"], payload)
            return AdapterResult(
                ok=True,
                platform_url=response.get("url"),
                platform_post_id=response.get("id"),
            )
        except Exception as e:
            return AdapterResult(
                ok=False,
                error=str(e),
            )
```

### Config

Store credentials in app_settings with `is_secret=true` so they're
redacted from logs and lists:

```bash
poindexter settings set my_platform_api_token "xxx" --category secrets
poindexter settings set my_platform_enabled true
```

Register the adapter in `services/social_publisher.py` platform map.

---

## 4. Adding a new LLM Provider (Phase J)

Tracked at [GH-104](https://github.com/Glad-Labs/poindexter/issues/104).

The `LLMProvider` Protocol abstracts the inference backend. Today
there's effectively one implementation (Ollama); the Phase J refactor
exposes it as a registry so operators can choose between Ollama,
vLLM, llama.cpp server, OpenAI-compatible endpoints, and paid cloud
providers (Anthropic, Google, OpenRouter) via one app_setting.

Until Phase J ships, `pipeline_writer_model` accepts the `ollama/<name>`
prefix and routes to Ollama. Other provider prefixes will be valid once
the registry lands.

```python
# Future shape (Phase J):
class MyProvider:
    name = "myprovider"

    async def complete(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ProviderResponse:
        ...

    async def embed(self, text: str) -> list[float]:
        ...
```

---

## 5. Adding a Tap (new topic source)

A **Tap** pulls content ideas from an external source and emits them
as candidate topics. Taps run on a schedule (driven by topic discovery)
and their output feeds the content pipeline.

Existing taps live in `services/topic_sources/`:

- `hackernews.py` — HN top stories
- `devto.py` — Dev.to top posts by tag
- `knowledge.py` — internal brain knowledge

Each implements `.extract(pool, config) → list[DiscoveredTopic]`.

### Minimum shape

```python
from services.topic_sources.base import TopicSource, DiscoveredTopic


class MyTap:
    async def extract(
        self,
        pool,
        config: dict,
    ) -> list[DiscoveredTopic]:
        # Fetch from your source
        items = await fetch_from_api(config["api_url"])

        # Convert to DiscoveredTopic
        return [
            DiscoveredTopic(
                title=item["title"],
                source="my_tap",
                score=item.get("score", 50),
                category="technology",
                metadata={"url": item["url"]},
            )
            for item in items
        ]
```

Singer-protocol intake is tracked at
[GH-103](https://github.com/Glad-Labs/poindexter/issues/103) — that
lets you pull from 600+ off-the-shelf Singer taps without writing
any connector code.

---

## 6. Adding a Job (scheduled background task)

A **Job** is a recurring task (cron-like) that runs independently of
the content pipeline. Examples: reload app_settings cache, prune stale
embeddings, re-embed posts, scrape HackerNews.

Jobs live in `services/jobs/`.

```python
from typing import Any


class MyJob:
    name = "my_job"
    # apscheduler trigger: "interval" or "cron"
    trigger = "interval"
    # For interval: seconds. For cron: cron-style string.
    schedule = 300  # every 5 minutes

    async def run(self, *, site_config: Any = None, **kwargs) -> None:
        # Do the work. Anything long-running belongs in a Job.
        ...
```

Register in `plugins/scheduler.py`. The scheduler auto-picks up jobs
based on `trigger` + `schedule`.

---

## 7. Adding a Probe (health / business metric)

A **Probe** answers a question about current system state (health,
business metric, capacity). Probes run on the brain daemon side and
emit Prometheus metrics consumed by Grafana + Alertmanager.

```python
from prometheus_client import Gauge

MY_PROBE_GAUGE = Gauge("my_probe_value", "What this probe measures")


class MyProbe:
    name = "my_probe"
    interval_seconds = 60

    async def probe(self) -> None:
        value = await measure_something()
        MY_PROBE_GAUGE.set(value)
```

Register in `brain/probe_registry.py`.

---

## 8. Adding a HITL approval gate (#145)

A **HITL gate** is a configurable pause-and-wait point in any pipeline
where the worker stops and asks a human to approve / reject before
moving on. Gates are config-driven — the same `ApprovalGateStage`
ships with the worker; you "add a gate" by registering the Stage in
the chain with a new `gate_name` and an `artifact_fn`.

The single source of truth for the operator interface is
`services/approval_service.py`. The CLI is the canonical surface;
MCP and any future REST endpoints are thin wrappers.

### Wire the Stage into your chain

Wherever your pipeline registers Stages (declarative `qa_gates` table
for QA chains, or the legacy hard-coded list in
`content_router_service`), drop in `ApprovalGateStage` with config:

```python
from services.stages.approval_gate import ApprovalGateStage

approval_gate = ApprovalGateStage()

# When your runner walks the chain, call:
result = await approval_gate.execute(
    context=context,           # pipeline context dict
    config={
        "gate_name": "topic_decision",
        "artifact_fn": lambda ctx: {
            "topic": ctx.get("topic", ""),
            "rationale": ctx.get("topic_rationale", ""),
        },
        # optional: skip this gate when a flag is set globally.
        "skip_if_setting": "automated_test_mode",
        # optional: status to leave on the row while paused.
        "halt_status": "in_progress",
    },
)
```

The Stage:

1. Reads `pipeline_gate_<gate_name>` from `app_settings`. If unset or
   `off`, returns `StageResult(ok=True)` and is a no-op — adding the
   Stage to the chain doesn't accidentally start blocking until the
   operator opts in.
2. Calls `artifact_fn(context)` to build the JSON the operator will
   review.
3. Persists `awaiting_gate`, `gate_artifact`, `gate_paused_at` on the
   `content_tasks` row.
4. Fires a Discord + Telegram notification through the existing
   `_notify_alert` plumbing.
5. Returns `StageResult(ok=True, continue_workflow=False)` — the
   runner halts.

### Enable the gate

Default-off. Flip it on with the CLI:

```bash
poindexter gates set topic_decision on
poindexter gates list
```

### Operator workflow

When the gate trips, the operator sees a Telegram / Discord message
with the artifact summary and the exact CLI command to approve /
reject. Day-to-day flow:

```bash
# What's pending?
poindexter list-pending

# What does THIS task look like?
poindexter show-pending <task_id>

# Approve — clears the gate and re-queues the pipeline.
poindexter approve <task_id>
poindexter approve <task_id> --gate topic_decision --feedback "good angle"

# Reject — sets status=rejected (or the gate's custom reject status)
# and clears the gate.
poindexter reject <task_id> --reason "off-brand"
```

Every command takes `--json` for piping. MCP tools exposed by the
`gladlabs` server (`approve`, `reject`, `list_pending`, `show_pending`,
`gates_list`, `gates_set`) wrap the same service module so an
operator can drive everything from a Claude Code session too.

### Per-gate reject status

Default reject status is `rejected`. Override per gate by setting
`approval_gate_<gate_name>_reject_status` in `app_settings` — useful
when a gate's "reject" should be a soft `dismissed` (so retry logic
doesn't kick in) instead of a hard veto.

### Why default-off

A new gate ships inert so registering it in the chain doesn't break
existing pipelines. The operator opts in by flipping the
`pipeline_gate_<gate_name>` setting. Add a row to
`bootstrap_defaults` if you want the gate enabled out of the box for
fresh installs.

### Tests

Every new gate gets a unit test that drives `ApprovalGateStage` with
its specific `artifact_fn`. See
`tests/unit/test_approval_gate_stage.py` for the canonical patterns:
gate-disabled passthrough, skip_if_setting passthrough, enabled-halt

- artifact persistence.

---

## Anti-patterns — please don't

- **Don't import across stages.** Stages communicate through the
  context dict, never by importing each other. If stage B needs data
  from stage A, stage A writes it to context; stage B reads from context.
- **Don't bypass `site_config`.** Services should not call `os.getenv()`
  directly. Read config through `site_config.get()` so DB values win
  over env, and post-Phase-H dependency injection works.
- **Don't hardcode model names.** Writer / critic / research model
  identifiers live in `app_settings` (`pipeline_writer_model`, etc.).
  Even for experiments, use the DB.
- **Don't write secrets to stdout / audit log.** Use the `is_secret=true`
  flag on the setting row — `SiteConfig.get_secret()` redacts values
  from the in-memory cache and logs.
- **Don't skip tests.** Every Stage / Reviewer / Adapter / Tap / Job /
  Probe has a unit test. The repo's 5,000+ test suite is the moat
  against drift between what the docs promise and what the code does.

---

## Adding a database migration

Database migrations live in `src/cofounder_agent/services/migrations/`
and run on every worker startup. The naming convention changed in
Glad-Labs/poindexter#378 — **new migrations use a UTC timestamp
prefix** (`YYYYMMDD_HHMMSS_<slug>.py`) instead of the legacy 4-digit
integer prefix. Eliminates the parallel-PR collision class of bug.

Generate one with:

```bash
python scripts/new-migration.py "describe what the migration does"
```

Read [`migrations.md`](migrations.md) for the full convention,
runner mechanics, common patterns, and anti-patterns. The fresh-DB
verification walkthrough lives in [`fresh-db-setup.md`](fresh-db-setup.md).

---

## 9. Adding a Module (Module v1)

A _Module_ is the unit of install / version / OSS visibility for a whole
business function. The substrate (brain, Prefect, Langfuse, cost_guard,
prompt_manager, …) keeps running unchanged; a Module bundles the lower-level
plugin contributions (stages, reviewers, probes, jobs, taps, adapters,
providers, packs) + things the existing plugin registry doesn't track
(DB migrations, Grafana panels, HTTP routes, CLI subcommands) and gives them
a manifest.

**When to add a Module vs a single plugin:** a Module makes sense when you're
adding a new _business function_ with its own DB tables, jobs, HTTP routes,
and operator surface — finance, customer support, ops/security, HR. For one
new pipeline step, one new image provider, one new probe — just use the
capability-plugin patterns above. The reference Module is `content` at
[`src/cofounder_agent/modules/content/`](../../src/cofounder_agent/modules/content/).
Private operator-overlay Modules (`visibility="private"`) live only in
forks/operator overlays; the public mirror's sync filter strips them.

### Step 1 — Scaffold the package

```
src/cofounder_agent/modules/<name>/
├── __init__.py            # re-exports the Module class
├── <name>_module.py       # the Module class
├── migrations/            # per-module DB migrations (Phase 2 runner)
│   └── __init__.py
└── jobs/                  # optional: module-owned scheduled jobs
    └── __init__.py
```

Copy the layout of `modules/content/`. Public modules use
`visibility="public"`; operator-overlay modules use `visibility="private"`
(those get filtered out of the public OSS mirror — see the sync script).

### Step 2 — Define the Module class

```python
# modules/<name>/<name>_module.py
from pathlib import Path

from plugins.module import ModuleManifest


_MANIFEST = ModuleManifest(
    name="<name>",                  # ^[a-z][a-z0-9_]*$
    version="0.1.0",
    visibility="public",            # or "private" for operator overlay
    description="One-line human description.",
    requires=(),
)


class <Name>Module:
    @property
    def migrations_dir(self) -> Path:
        return Path(__file__).parent / "migrations"

    def manifest(self) -> ModuleManifest:
        return _MANIFEST

    async def migrate(self, pool: object) -> None:
        from services.module_migrations import run_module_migrations
        await run_module_migrations(pool, _MANIFEST.name, self.migrations_dir)

    # Phase 4 lifecycle hooks — leave as no-ops until you actually wire them.
    def register_routes(self, app: object) -> None:
        del app

    def register_cli(self, parser: object) -> None:
        del parser

    def register_dashboards(self, grafana: object) -> None:
        del grafana

    def register_probes(self, brain: object) -> None:
        del brain

    async def healthcheck(self, pool: object) -> object:
        del pool
        return None
```

### Step 3 — Register the Module in `_SAMPLES`

Add one line to `plugins/registry.py`'s `_SAMPLES` list:

```python
("modules", "modules.<name>", "<Name>Module"),
```

Verify discovery:

```bash
cd src/cofounder_agent && poetry run python -c "
from plugins.registry import get_modules, clear_registry_cache
clear_registry_cache()
print([m.manifest().name for m in get_modules()])
"
```

### Step 4 — Add a migration if your module owns DB tables

```bash
# from the repo root
python scripts/new-migration.py "create <name> tables"
# This drops the file in services/migrations/; MOVE it to
# src/cofounder_agent/modules/<name>/migrations/<file>.py.
```

The Phase 2 runner records each migration in `module_schema_migrations`
keyed on `(module_name, migration_name)` — so two modules can both have
an `init.py` migration without colliding.

### Step 5 — Add module-owned jobs (optional)

Module-owned scheduled jobs live at `modules/<name>/jobs/<job>.py`
implementing the `JobResult`-returning `run(pool, config)` contract.
Register in `_SAMPLES`:

```python
("jobs", "modules.<name>.jobs.<job_module>", "<JobClass>"),
```

The plugin scheduler picks them up at worker startup.

### Step 6 — Verify end-to-end

Restart the worker. The boot log should show:

```
PluginScheduler started with N jobs: [..., '<your_job>', ...]
module_migrations: <name> done — applied=X skipped=0 failed=0
module <name> — register_routes() complete
```

If you see all three, your Module is live.

### What's deferred

- **Phase 3.5** — physical pipeline-code moves into `modules/content/`
  (today substrate still owns the 21-stage tree). Wait for sample size ≥ 3.
- **Phase 4.5** — `register_dashboards` (Grafana folder per module),
  `register_cli` (subparser auto-discovery), `register_probes` (brain
  daemon integration). Today these are no-op stubs; wire them inline in
  your module's package until the substrate generalizes them.
- **Phase 5** — `visibility="private"` drives `scripts/sync-to-github.sh`
  automatically. Today private modules are stripped via explicit
  pattern list in the sync script.

---

## See also

- [Plugin architecture](../architecture/plugin-architecture) —
  full roadmap + rationale
- [Module v1 spec](../architecture/module-v1.md) — design rationale,
  shipped status, deferred phases
- [Services reference](../reference/services) — catalog of every
  service in the worker
- [Content pipeline](../architecture/content-pipeline) — how the
  Stage chain fits together
- [App settings reference](../reference/app-settings) — every
  DB-backed config key
- [Database migrations convention](migrations.md) — Glad-Labs/poindexter#378
- [Fresh DB setup walkthrough](fresh-db-setup.md) — verified end-to-end

<!-- DOC-SYNC 2026-04-25: stale references — brain/probe_registry.py (use brain/probe_interface.py), services/social_publisher.py, and threads.py adapter no longer exist. Extension-point docs may be out of date. -->
