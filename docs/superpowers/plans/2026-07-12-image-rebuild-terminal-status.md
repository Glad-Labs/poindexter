# image_rebuild Terminal Status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a successful `image_rebuild` run from stranding its own job row at `status='in_progress'` (which the stale-sweep re-runs in a loop), by adding a general-purpose, config-driven `atoms.set_task_status` terminal node that flips the job to `completed`.

**Architecture:** A new generic atom (`atoms.set_task_status`) does one guarded `pipeline_tasks.status` write, reading the target status from its graph_def node `config`. `image_rebuild`'s graph gets a `finalize` node using it (`target_status='completed'`). `completed` is added to `post_pipeline_actions._DECIDED_NON_REJECTED_STATUSES` so the post-pipeline success-path skips the utility job. Ships the graph_def re-seed trio (snapshot regen + baseline seed + re-seed migration) so both fresh installs and existing prod converge.

**Tech Stack:** Python 3 / asyncio, LangGraph graph_def pipelines, asyncpg, pytest (`pytest-asyncio`), the in-repo atom registry + `pipeline_architect` compiler.

## Global Constraints

- **Design source of truth:** `docs/superpowers/specs/2026-07-12-image-rebuild-terminal-status-design.md`. Every task traces to it.
- **Terminal status = `completed`** — already valid in the `pipeline_tasks_status_check` DB CHECK constraint; no constraint change.
- **`atoms.*` namespace** — the new atom's `ATOM_META.name` is `atoms.set_task_status` even though the file lives under `modules/content/atoms/` (mirrors `atoms.approval_gate`).
- **Config-seeded `requires` keys must be declared in `PipelineState`** — LangGraph silently drops undeclared state keys (#753), and `pipeline_architect._validate_graph_schema` raises at seed time if a `requires` key is undeclared. So `target_status` MUST be added to `PipelineState`.
- **graph_def re-seed trap (#2263):** changing the image_rebuild graph topology requires ALL THREE to move together, or prod halts / fresh installs diverge: (1) regenerate the fingerprint snapshot, (2) edit the baseline seed, (3) add a re-seed migration. Never do (1) alone.
- **Specs stay RAW (unstamped):** `IMAGE_REBUILD_GRAPH_DEF` nodes carry no `_contract_fp`; the boot self-heal (`ensure_active_graph_defs_stamped`) stamps them.
- **Running tests in this worktree:** this is a git worktree with **no venv of its own**. Run pytest through the **main checkout's** poetry environment and disable the repo's `--forked` addopts default, e.g. from `C:/Users/mattm/glad-labs-website/src/cofounder_agent`:
  `poetry run pytest <path> -o addopts="" -p no:cacheprovider -q`
  (See memory `reference_run_worktree_tests`. If `poetry run` can't find the env from the worktree, invoke the main checkout's venv python directly: `<main-venv-python> -m pytest <path> -o addopts="" -q`.)
- **All work on branch `claude/upbeat-khayyam-701aee`** (draft PR #2365 already open). Linear history, commit per task, Co-Authored-By trailer on every commit.

---

## File Structure

**Create:**

- `src/cofounder_agent/modules/content/atoms/set_task_status.py` — the generic status-mutation atom.
- `src/cofounder_agent/tests/unit/modules/content/atoms/test_set_task_status.py` — atom unit tests.
- `src/cofounder_agent/tests/unit/services/test_image_rebuild_terminal_contract.py` — the graph_def terminal-status contract test.
- `src/cofounder_agent/services/migrations/<generated-ts>_reseed_image_rebuild_finalize.py` — re-seed migration (generated via `scripts/new-migration.py`).

**Modify:**

- `src/cofounder_agent/services/template_runner.py` — add `target_status: str` to `PipelineState`.
- `src/cofounder_agent/services/post_pipeline_actions.py:82-84` — add `"completed"` to `_DECIDED_NON_REJECTED_STATUSES`.
- `src/cofounder_agent/services/image_rebuild_spec.py` — add the `finalize` node + edges; update the module docstring flow diagram.
- `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql:927` — add the `finalize` node + edges to the seeded `image_rebuild` graph_def.
- `src/cofounder_agent/tests/unit/services/graph_def_contract_fingerprints.json` — regenerated (adds `atoms.set_task_status`).
- `src/cofounder_agent/tests/unit/services/test_post_pipeline_actions.py` — add a `completed`-skips test.

---

## Task 1: `atoms.set_task_status` atom + `PipelineState.target_status`

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/set_task_status.py`
- Modify: `src/cofounder_agent/services/template_runner.py` (PipelineState, after line 681 `featured_source`)
- Test: `src/cofounder_agent/tests/unit/modules/content/atoms/test_set_task_status.py`

**Interfaces:**

- Produces: module `modules.content.atoms.set_task_status` exposing `ATOM_META: AtomMeta` (name `"atoms.set_task_status"`), `async def run(state: dict) -> dict`, and `_VALID_STATUSES: frozenset[str]`.
- Consumes: `plugins.atom.{AtomMeta, FieldSpec, RetryPolicy}`; `database_service.update_task_status_guarded(*, task_id, new_status, allowed_from, **fields) -> str | None` (returns previous status, or `None` when current status ∉ `allowed_from`).

- [ ] **Step 1: Write the failing atom tests**

Create `src/cofounder_agent/tests/unit/modules/content/atoms/test_set_task_status.py`:

```python
"""Unit tests for atoms.set_task_status — the generic status-mutation atom.

Models update_task_status_guarded's real semantics (returns the previous
status on success, None when the current status is not in allowed_from) —
mirrors tests/unit/services/atoms/test_content_evaluate_auto_publish_finalize.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _StatusTrackingDb:
    def __init__(self, status: str) -> None:
        self.status = status
        self.pool = MagicMock()
        self.guarded_calls: list[dict] = []

    async def update_task_status_guarded(
        self, *, task_id, new_status, allowed_from=("in_progress", "pending"), **fields
    ):
        self.guarded_calls.append(
            {
                "task_id": task_id,
                "new_status": new_status,
                "allowed_from": tuple(allowed_from),
                "fields": dict(fields),
            }
        )
        if self.status not in allowed_from:
            return None
        prev = self.status
        self.status = new_status
        return prev


@pytest.mark.asyncio
async def test_flips_in_progress_to_completed_with_percentage():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    state = {
        "task_id": "job-1",
        "target_status": "completed",
        "percentage": 100,
        "database_service": db,
    }

    out = await run(state)

    assert out == {}
    assert db.status == "completed"
    call = db.guarded_calls[-1]
    assert call["new_status"] == "completed"
    assert call["allowed_from"] == ("in_progress",)
    assert call["fields"] == {"percentage": 100}


@pytest.mark.asyncio
async def test_target_status_is_config_driven_not_hardcoded():
    """Proves the status is a parameter: a graph can finalize to any valid
    status, not just 'completed'."""
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    state = {"task_id": "job-2", "target_status": "published", "database_service": db}

    await run(state)

    assert db.status == "published"


@pytest.mark.asyncio
async def test_already_terminal_is_a_benign_noop():
    """Guard returns None (current status not in allowed_from) → no raise,
    status unchanged. Makes a re-run idempotent."""
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="completed")
    state = {"task_id": "job-3", "target_status": "completed", "database_service": db}

    out = await run(state)  # must not raise

    assert out == {}
    assert db.status == "completed"


@pytest.mark.asyncio
async def test_custom_allowed_from_from_config():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="awaiting_gate")
    state = {
        "task_id": "job-4",
        "target_status": "completed",
        "allowed_from": ["in_progress", "awaiting_gate"],
        "database_service": db,
    }

    await run(state)

    assert db.status == "completed"


@pytest.mark.asyncio
async def test_missing_target_status_fails_loud():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    with pytest.raises(RuntimeError, match="target_status"):
        await run({"task_id": "job-5", "database_service": db})


@pytest.mark.asyncio
async def test_invalid_target_status_fails_loud():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    with pytest.raises(RuntimeError, match="not a valid"):
        await run(
            {"task_id": "job-6", "target_status": "bogus", "database_service": db}
        )


@pytest.mark.asyncio
async def test_missing_guarded_method_is_nonfatal():
    """A degenerate db without the guarded method must not fail a completed
    graph — mirrors content.evaluate_auto_publish's terminal-node posture."""
    from modules.content.atoms.set_task_status import run

    out = await run(
        {"task_id": "job-7", "target_status": "completed", "database_service": object()}
    )

    assert out == {}


def test_valid_statuses_match_db_constraint():
    """Drift guard: the atom's _VALID_STATUSES must equal the DB
    pipeline_tasks_status_check CHECK constraint set."""
    import re
    from pathlib import Path

    import services
    from modules.content.atoms.set_task_status import _VALID_STATUSES

    schema = (
        Path(services.__file__).parent / "migrations" / "0000_baseline.schema.sql"
    ).read_text(encoding="utf-8")
    m = re.search(
        r"pipeline_tasks_status_check CHECK \(status IN \(([^)]*)\)\)", schema
    )
    assert m, "could not find pipeline_tasks_status_check in baseline schema"
    db_statuses = frozenset(s.strip().strip("'") for s in m.group(1).split(","))
    assert _VALID_STATUSES == db_statuses
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/atoms/test_set_task_status.py -o addopts="" -q`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'modules.content.atoms.set_task_status'`.

- [ ] **Step 3: Create the atom**

Create `src/cofounder_agent/modules/content/atoms/set_task_status.py`:

```python
"""atoms.set_task_status — general-purpose task-status mutation node.

A config-driven atom that transitions the RUNNING task's own
``pipeline_tasks.status`` to a status declared in its graph_def node ``config``
(``target_status``), via a guarded write. It is NOT a terminal-only finalizer —
it just sets a status, so it can sit anywhere in a graph; ``image_rebuild`` uses
it last (``target_status='completed'``) to give its otherwise-orphaned job row a
terminal state (mirrors how ``content.republish_post`` finalizes ``seo_refresh``).

Reads (from state; config values are seeded onto state by
``pipeline_architect.build_graph_from_spec``):
  - ``task_id``          (required) — the running task's own id.
  - ``target_status``    (required, from config) — the status to set.
  - ``allowed_from``     (optional, default ('in_progress',)) — guard whitelist.
  - ``percentage``       (optional, from config) — set atomically with the status.

Writes only the running task's own row; never touches any target draft.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

# Mirror of the pipeline_tasks_status_check CHECK constraint
# (services/migrations/0000_baseline.schema.sql). Kept honest by
# test_set_task_status.py::test_valid_statuses_match_db_constraint.
_VALID_STATUSES: frozenset[str] = frozenset(
    {
        "pending",
        "in_progress",
        "approved",
        "awaiting_approval",
        "awaiting_gate",
        "rejected",
        "rejected_retry",
        "rejected_final",
        "failed",
        "completed",
        "published",
        "cancelled",
        "dry_run",
        "superseded",
        "archived",
    }
)

_DEFAULT_ALLOWED_FROM = ("in_progress",)

ATOM_META = AtomMeta(
    name="atoms.set_task_status",
    type="atom",
    version="1.0.0",
    description=(
        "Transition the running task's pipeline_tasks.status to a config-declared "
        "target_status via a guarded write (allowed_from defaults to in_progress). "
        "General-purpose status-mutation node; image_rebuild uses it terminally to "
        "mark the rebuild job 'completed'."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="the running task's id"),
        FieldSpec(
            name="target_status",
            type="str",
            description="status to set (from node config)",
        ),
        FieldSpec(
            name="allowed_from",
            type="list",
            description="guard whitelist (default ('in_progress',))",
            required=False,
        ),
        FieldSpec(
            name="percentage",
            type="int",
            description="progress %, set atomically with status",
            required=False,
        ),
        FieldSpec(
            name="database_service",
            type="object",
            description="DB service",
            required=False,
        ),
    ),
    outputs=(),
    requires=("task_id", "target_status"),
    produces=(),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("db_write",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    task_id = state.get("task_id")
    target_status = state.get("target_status")
    if not task_id or not target_status:
        raise RuntimeError(
            "atoms.set_task_status: task_id + target_status are required "
            f"(task_id={task_id!r}, target_status={target_status!r}). "
            "Declare target_status in the node config."
        )
    if target_status not in _VALID_STATUSES:
        raise RuntimeError(
            f"atoms.set_task_status: target_status={target_status!r} is not a "
            f"valid pipeline_tasks status; must be one of {sorted(_VALID_STATUSES)}"
        )

    allowed_from = state.get("allowed_from") or _DEFAULT_ALLOWED_FROM
    if isinstance(allowed_from, str):
        allowed_from = (allowed_from,)
    allowed_from = tuple(allowed_from)

    database_service = state.get("database_service")
    guarded = getattr(database_service, "update_task_status_guarded", None)
    if guarded is None:
        logger.warning(
            "atoms.set_task_status: database_service has no "
            "update_task_status_guarded — cannot set status %r for task %s",
            target_status,
            task_id,
        )
        return {}

    fields: dict[str, Any] = {}
    percentage = state.get("percentage")
    if percentage is not None:
        fields["percentage"] = int(percentage)

    prev = await guarded(
        task_id=str(task_id),
        new_status=target_status,
        allowed_from=allowed_from,
        **fields,
    )
    if prev is None:
        logger.debug(
            "atoms.set_task_status: guarded no-op for task %s (current status "
            "not in allowed_from=%s; already terminal?)",
            task_id,
            allowed_from,
        )
    elif prev != target_status:
        logger.info(
            "atoms.set_task_status: task %s status %s -> %s",
            task_id,
            prev,
            target_status,
        )
    return {}


__all__ = ["ATOM_META", "run", "_VALID_STATUSES"]
```

- [ ] **Step 4: Add `target_status` to `PipelineState`**

In `src/cofounder_agent/services/template_runner.py`, the `image_rebuild` block currently ends at line 681. Replace:

```python
    allow_stock: bool            # operator opt-in to Pexels stock fallback slots
    featured_source: str         # content.rebuild_featured_image: image_gen | pexels | none
```

with:

```python
    allow_stock: bool            # operator opt-in to Pexels stock fallback slots
    featured_source: str         # content.rebuild_featured_image: image_gen | pexels | none

    # atoms.set_task_status config (generic status-mutation node; image_rebuild's
    # `finalize` node seeds target_status='completed'). Declared for the #753
    # schema gate — atoms.set_task_status.requires includes target_status, so an
    # undeclared key would raise ValueError at seed time.
    target_status: str           # atoms.set_task_status: status to transition the running task to
```

- [ ] **Step 5: Run the atom tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/atoms/test_set_task_status.py -o addopts="" -q`
Expected: PASS (8 tests).

- [ ] **Step 6: Confirm the atom discovers + doesn't break the fingerprint gate**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_graph_def_contract_freshness.py -o addopts="" -q`
Expected: PASS — the atom exists but no spec references it yet, so the snapshot needs no change.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/set_task_status.py \
        src/cofounder_agent/tests/unit/modules/content/atoms/test_set_task_status.py \
        src/cofounder_agent/services/template_runner.py
git commit -m "feat(pipeline): add atoms.set_task_status generic status-mutation node

Config-driven guarded pipeline_tasks.status write; target_status declared
in graph_def node config (added to PipelineState for the #753 schema gate).
Not terminal-only — placeable anywhere in a graph.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `post_pipeline_actions` recognizes `completed` as decided

**Files:**

- Modify: `src/cofounder_agent/services/post_pipeline_actions.py:82-84`
- Test: `src/cofounder_agent/tests/unit/services/test_post_pipeline_actions.py`

**Interfaces:**

- Consumes: `run_post_pipeline_actions(*, database_service, task_id, topic, result, site_config, settings_service=None)`; the terminal-status guard re-reads canonical `pipeline_tasks.status` via `pool.fetchrow("SELECT status, error_message ...")` and skips success-path side-effects when the status ∈ `_DECIDED_NON_REJECTED_STATUSES`.

- [ ] **Step 1: Write the failing test**

In `src/cofounder_agent/tests/unit/services/test_post_pipeline_actions.py`, add this test immediately after `test_already_published_task_skips_all_side_effects` (mirrors it with `status="completed"`):

```python
    @pytest.mark.asyncio
    async def test_already_completed_task_skips_all_side_effects(self):
        """A utility job finalized to 'completed' (e.g. image_rebuild) must not
        trigger the success-path webhook / auto-publish / awaiting-approval ping."""
        from services.post_pipeline_actions import run_post_pipeline_actions

        pool, _ = _make_pool(
            fetchrow_return={"status": "completed", "error_message": None},
        )
        db = _make_db_service(pool=pool)
        site = _make_site_config()
        settings = _make_settings_service(values={"min_curation_score": "70"})

        emit_mock = AsyncMock()
        notify_mock = AsyncMock()
        with patch(
            "services.post_pipeline_actions.emit_webhook_event", emit_mock,
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        ):
            await run_post_pipeline_actions(
                database_service=db,
                task_id="t-completed",
                topic="Image rebuild job",
                result=_result(score=0),
                site_config=site,
                settings_service=settings,
            )

        notify_mock.assert_not_awaited()
        events = [c.args[1] for c in emit_mock.call_args_list]
        assert "task.completed" not in events
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest "tests/unit/services/test_post_pipeline_actions.py::TestTerminalStatusGuard::test_already_completed_task_skips_all_side_effects" -o addopts="" -q`
(If the class name differs, run the whole file and locate the new test.) Expected: FAIL — `completed` is not yet in the decided set, so `notify_operator` fires (the awaiting-approval ping) → `notify_mock.assert_not_awaited()` raises.

- [ ] **Step 3: Add `completed` to the decided-status set**

In `src/cofounder_agent/services/post_pipeline_actions.py`, replace:

```python
_DECIDED_NON_REJECTED_STATUSES = frozenset(
    {"failed", "cancelled", "canceled", "published", "approved"}
)
```

with:

```python
_DECIDED_NON_REJECTED_STATUSES = frozenset(
    # 'completed' is the terminal a utility/rebuild graph sets on its own job row
    # (atoms.set_task_status). Recognizing it here keeps the success-path
    # side-effects (webhook / auto-publish / awaiting-approval ping) off a job
    # that has no post. Also covers seo_refresh's 'completed' rows.
    {"failed", "cancelled", "canceled", "published", "approved", "completed"}
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_post_pipeline_actions.py -o addopts="" -q`
Expected: PASS (the new test plus the existing suite).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/post_pipeline_actions.py \
        src/cofounder_agent/tests/unit/services/test_post_pipeline_actions.py
git commit -m "fix(pipeline): treat 'completed' as a decided terminal in post-pipeline guard

A utility job finalized to 'completed' no longer triggers the success-path
webhook / auto-publish / awaiting-approval ping. Also covers seo_refresh.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire `image_rebuild` + re-seed (snapshot + baseline + migration)

**Files:**

- Modify: `src/cofounder_agent/services/image_rebuild_spec.py`
- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql:927`
- Modify: `src/cofounder_agent/tests/unit/services/graph_def_contract_fingerprints.json` (regenerated)
- Create: `src/cofounder_agent/tests/unit/services/test_image_rebuild_terminal_contract.py`
- Create: `src/cofounder_agent/services/migrations/<generated-ts>_reseed_image_rebuild_finalize.py`

**Interfaces:**

- Consumes: `atoms.set_task_status` (Task 1); `IMAGE_REBUILD_GRAPH_DEF` (dict); `pipeline_architect._validate_graph_schema(spec)`; `post_pipeline_actions._DECIDED_NON_REJECTED_STATUSES` (Task 2).
- Produces: `IMAGE_REBUILD_GRAPH_DEF` with an 8th node `finalize` (`atoms.set_task_status`, config `{"target_status": "completed", "percentage": 100}`) as the terminal node.

- [ ] **Step 1: Write the failing terminal-contract test**

Create `src/cofounder_agent/tests/unit/services/test_image_rebuild_terminal_contract.py`:

```python
"""Contract: image_rebuild finalizes its job row to a decided-terminal status.

Regression guard for the in_progress-stranding loop — the graph's terminal node
must set a status that post_pipeline_actions treats as decided AND that the
stale-sweep won't re-claim. Also proves target_status is declared in
PipelineState (the #753 seed-time schema gate).
"""
from __future__ import annotations

import services.pipeline_architect as pa
from services.atom_registry import discover
from services.image_rebuild_spec import IMAGE_REBUILD_GRAPH_DEF
from services.post_pipeline_actions import _DECIDED_NON_REJECTED_STATUSES


def _node_by_id(spec: dict) -> dict[str, dict]:
    return {n["id"]: n for n in spec["nodes"]}


def test_terminal_node_is_set_task_status_to_END():
    edges = IMAGE_REBUILD_GRAPH_DEF["edges"]
    to_end = [e["from"] for e in edges if e["to"] == "END"]
    assert to_end == ["finalize"], f"expected single terminal 'finalize', got {to_end}"
    finalize = _node_by_id(IMAGE_REBUILD_GRAPH_DEF)["finalize"]
    assert finalize["atom"] == "atoms.set_task_status"


def test_terminal_target_status_is_decided_and_completed():
    finalize = _node_by_id(IMAGE_REBUILD_GRAPH_DEF)["finalize"]
    target = finalize["config"]["target_status"]
    assert target == "completed"
    # THE regression guard: the declared terminal must be one the post-pipeline
    # success-path guard recognizes, or the loop/side-effect bug returns.
    assert target in _DECIDED_NON_REJECTED_STATUSES


def test_spec_is_raw_unstamped():
    assert all(
        "_contract_fp" not in n for n in IMAGE_REBUILD_GRAPH_DEF["nodes"]
    ), "spec must be raw (unstamped) so the boot self-heal re-stamps it"


def test_target_status_declared_in_pipeline_state():
    """_validate_graph_schema raises if any atom requires/produces a key not in
    PipelineState. Passing here proves target_status was added to PipelineState."""
    discover()
    pa._validate_graph_schema(IMAGE_REBUILD_GRAPH_DEF)  # must not raise
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_rebuild_terminal_contract.py -o addopts="" -q`
Expected: FAIL — no `finalize` node yet (`test_terminal_node_is_set_task_status_to_END` and others fail on the current `persist → END`).

- [ ] **Step 3: Add the `finalize` node + edges to the spec**

In `src/cofounder_agent/services/image_rebuild_spec.py`, in `IMAGE_REBUILD_GRAPH_DEF`:

First, the nodes list — replace:

```python
        {"id": "persist", "atom": "content.persist_draft_images"},
    ],
```

with:

```python
        {"id": "persist", "atom": "content.persist_draft_images"},
        {
            "id": "finalize",
            "atom": "atoms.set_task_status",
            # The rebuild JOB row is otherwise orphaned at in_progress (the target
            # draft stays awaiting_approval, untouched by persist). 'completed' is
            # terminal, keeps the job out of the claim/stale-sweep/approval queries,
            # and post_pipeline_actions treats it as decided.
            "config": {"target_status": "completed", "percentage": 100},
        },
    ],
```

Then the edges list — replace:

```python
        {"from": "inject", "to": "persist"},
        {"from": "persist", "to": "END"},
    ],
```

with:

```python
        {"from": "inject", "to": "persist"},
        {"from": "persist", "to": "finalize"},
        {"from": "finalize", "to": "END"},
    ],
```

- [ ] **Step 4: Update the module docstring flow diagram**

In the same file's module docstring, replace the line:

```
      → persist (content.persist_draft_images) — write content + featured back
                                                 to the TARGET draft, bump
                                                 regen_images_attempts, audit
```

with:

```
      → persist (content.persist_draft_images) — write content + featured back
                                                 to the TARGET draft, bump
                                                 regen_images_attempts, audit
      → finalize (atoms.set_task_status)       — flip THIS rebuild job row to
                                                 'completed' (the target draft
                                                 stays awaiting_approval); without
                                                 it the job strands in_progress and
                                                 the stale-sweep re-runs it
```

- [ ] **Step 5: Run the terminal-contract test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_rebuild_terminal_contract.py -o addopts="" -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Confirm the fingerprint gate now FAILS (expected — snapshot stale)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_graph_def_contract_freshness.py -o addopts="" -q`
Expected: FAIL — `atoms.set_task_status` is now referenced by `image_rebuild` but absent from the committed snapshot (`test_active_specs_match_committed_snapshot` / `test_committed_snapshot_has_no_stale_entries`). This is the #2263 guard doing its job.

- [ ] **Step 7: Regenerate the fingerprint snapshot**

Run: `cd src/cofounder_agent && REGEN_GRAPH_DEF_FP=1 poetry run pytest tests/unit/services/test_graph_def_contract_freshness.py::test__regenerate_snapshot -o addopts="" -q`
Then re-run the gate to confirm green:
Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_graph_def_contract_freshness.py -o addopts="" -q`
Expected: PASS. Confirm `git diff` shows `graph_def_contract_fingerprints.json` gained an `"atoms.set_task_status": "<12hex>"` entry and nothing else changed.

- [ ] **Step 8: Update the baseline seed (fresh installs)**

In `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql`, on the `image_rebuild` INSERT (line ~927), make two in-place edits to the graph_def JSON.

Edit A — edges. Replace:

```
{"to": "END", "from": "persist"}]
```

with:

```
{"to": "finalize", "from": "persist"}, {"to": "END", "from": "finalize"}]
```

Edit B — nodes. Replace:

```
{"id": "persist", "atom": "content.persist_draft_images"}]
```

with:

```
{"id": "persist", "atom": "content.persist_draft_images"}, {"id": "finalize", "atom": "atoms.set_task_status", "config": {"target_status": "completed", "percentage": 100}}]
```

(Both target substrings occur only in the `image_rebuild` row.)

- [ ] **Step 9: Generate the re-seed migration**

Run: `cd src/cofounder_agent && python ../../scripts/new-migration.py "reseed image_rebuild graph_def with finalize node"`
Note the generated path (e.g. `services/migrations/20260712_HHMMSS_reseed_image_rebuild_graph_def_with_finalize_node.py`). Replace its entire body with:

```python
"""Reseed the image_rebuild graph_def with the finalize (atoms.set_task_status) node.

image_rebuild previously ended at content.persist_draft_images -> END with no
node that finalizes the rebuild JOB row, so a successful run stranded it at
status='in_progress' and the stale-sweep re-ran it in a loop. The graph now ends
with a `finalize` node (atoms.set_task_status, target_status='completed').

Existing prod's stored pipeline_templates.graph_def row won't get the new node
from the baseline (baseline runs once), so UPDATE it here to the current spec —
RAW (unstamped); the boot self-heal (ensure_active_graph_defs_stamped) re-stamps
it same boot. Fresh installs get it from the updated baseline seed.

image_rebuild_spec is pure data (no heavy imports), so importing it in a
migration is safe for the dependency-light migrations-smoke env.

See docs/superpowers/specs/2026-07-12-image-rebuild-terminal-status-design.md.
"""

from __future__ import annotations

import json
import logging

from services.image_rebuild_spec import IMAGE_REBUILD_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Point the active image_rebuild row at the current (raw) graph_def."""
    graph_def_json = json.dumps(IMAGE_REBUILD_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $1::jsonb,
                   updated_at = now()
             WHERE slug = 'image_rebuild'
               AND active = true
            """,
            graph_def_json,
        )
    logger.info("Migration reseed_image_rebuild_finalize: applied (%s)", result)


async def down(pool) -> None:
    """No-op: the prior graph_def is recoverable from git history / baseline.

    Reverting to the pre-finalize graph would re-introduce the in_progress
    stranding bug, so we intentionally do not restore it.
    """
    logger.info("Migration reseed_image_rebuild_finalize: down is a no-op")
```

- [ ] **Step 10: Lint the migration**

Run: `cd src/cofounder_agent && python ../../scripts/ci/migrations_lint.py`
Expected: PASS (no collisions, runner interface present). If `migrations_lint.py` lives elsewhere, run the path named in CLAUDE.md's migrations section.

- [ ] **Step 11: Run the full set of touched suites**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_rebuild_terminal_contract.py tests/unit/services/test_graph_def_contract_freshness.py tests/unit/modules/content/atoms/test_set_task_status.py tests/unit/services/test_post_pipeline_actions.py tests/unit/services/migrations -o addopts="" -q`
Expected: PASS across all.

- [ ] **Step 12: Commit**

```bash
git add src/cofounder_agent/services/image_rebuild_spec.py \
        src/cofounder_agent/services/migrations/0000_baseline.seeds.sql \
        src/cofounder_agent/tests/unit/services/graph_def_contract_fingerprints.json \
        src/cofounder_agent/tests/unit/services/test_image_rebuild_terminal_contract.py \
        src/cofounder_agent/services/migrations/*_reseed_image_rebuild_*.py
git commit -m "fix(pipeline): image_rebuild finalizes its job row to 'completed'

Add a finalize node (atoms.set_task_status, target_status='completed') to the
image_rebuild graph_def so a successful rebuild no longer strands its job row at
in_progress (which the stale-sweep re-ran in a loop). Ships the #2263 re-seed
trio: snapshot regen + baseline seed + re-seed migration. The target draft is a
separate row and stays awaiting_approval.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Full verification + mark PR ready

**Files:** none (verification only).

- [ ] **Step 1: Run the broader backend suite for regressions**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services tests/unit/modules/content -o addopts="" -q`
Expected: PASS (no regressions from the PipelineState / post_pipeline / spec changes).

- [ ] **Step 2: Push and confirm CI**

```bash
git push origin claude/upbeat-khayyam-701aee
```

Watch the PR checks (test-backend, migrations-smoke, the two CodeQL analyzes, link-rot). Expected: green.

- [ ] **Step 3: Real-run verification (per the spec's rollout section)**

After the worker image is rebuilt + restarted on Matt's PC (his authority), enqueue a rebuild against an `awaiting_approval` dev-niche draft:
`poindexter tasks rebuild-images <draft_task_id>` (with `POINDEXTER_API_URL=http://localhost:8002`).
Then confirm in the DB:

- the rebuild JOB row ends `status='completed'`, `percentage=100`;
- the DRAFT row stays `status='awaiting_approval'` with fresh images;
- no re-run occurs after `content_flow_stale_inprogress_minutes` elapses.

- [ ] **Step 4: Mark the draft PR ready for review**

```bash
gh pr ready 2365
```

---

## Self-Review

**1. Spec coverage** — spec §"Components": A (new atom) → Task 1; B (wire image_rebuild) → Task 3 steps 3-4; C (`_DECIDED_NON_REJECTED_STATUSES`) → Task 2; D (snapshot + baseline + migration) → Task 3 steps 7-9. §Design decision 1 (`completed`) → Task 3 config + Task 2. §Design decision 2 (parameterized node) → Task 1 atom. §Testing (atom / post_pipeline / graph_def contract) → Tasks 1, 2, 3. §Data flow (guarded finalize, stale-sweep leaves `completed`) → Task 1 atom + Task 3 contract test. §Rollout → Task 4. Covered.

**2. Placeholder scan** — the only intentional stand-in is the generated migration filename (`<generated-ts>`), resolved by `scripts/new-migration.py` in Task 3 Step 9; the body is fully specified. No TBDs.

**3. Type consistency** — `update_task_status_guarded(*, task_id, new_status, allowed_from, **fields)` used identically in the atom (Task 1 Step 3), the test double (Task 1 Step 1), and matches `services/tasks_db.py:908`. `ATOM_META.name="atoms.set_task_status"` used consistently in the atom, the spec node (Task 3), the baseline seed, and the snapshot. `target_status` / `allowed_from` / `percentage` state keys match between the atom, the node `config`, and `PipelineState`.
