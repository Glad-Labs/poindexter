# Module Visibility Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make removing a private module from the public mirror require _only_ deleting its directory — no substrate source patching — by switching in-tree module (and module-owned job) discovery to a filesystem scan.

**Architecture:** In-tree modules are discovered by scanning `modules/*/` for `<name>/<name>_module.py`; module-owned jobs by reading a `JOBS` list in `modules/<name>/jobs/__init__.py`. External modules keep using the existing `poindexter.modules` entry-point group (the additive seam toward the overlay-package endgame). Each module owns its CLI group via `register_cli`, so the CLI travels with the package. The mirror sync then strips a private module by deleting its directory, and the two hand-maintained substrate-patch pattern lists (in `sync-to-github.sh` and `check_public_mirror_safety.py`) are deleted.

**Tech Stack:** Python 3.12/3.13, `importlib.metadata` entry-points, `click` CLI, `pytest`, bash (`sync-to-github.sh`). Run tests from `src/cofounder_agent` via `poetry run pytest`.

**Spec:** `docs/architecture/2026-06-04-module-visibility-sync-design.md`

---

## File Structure

| File                                                                   | Responsibility                      | Change                                                                                                                                                                                                 |
| ---------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/cofounder_agent/plugins/registry.py`                              | Plugin/module discovery             | Add scan functions + `ModuleDiscoveryError`; source modules+jobs in `get_core_samples()` from the scan (leave `get_modules()` unchanged); remove the 3 hardcoded content/finance/job `_SAMPLES` tuples |
| `src/cofounder_agent/modules/finance/jobs/__init__.py`                 | Finance module-owned jobs           | Export `JOBS = [PollMercuryJob]` so the scan finds the job                                                                                                                                             |
| `src/cofounder_agent/modules/finance/cli.py`                           | Finance CLI group                   | **Created** by `git mv` from `poindexter/cli/finance.py`; fix one import to absolute                                                                                                                   |
| `src/cofounder_agent/modules/finance/finance_module.py`                | Finance module identity + lifecycle | Implement `register_cli()` to mount the finance group (no-op on `None`)                                                                                                                                |
| `src/cofounder_agent/poindexter/cli/app.py`                            | CLI root command group              | Remove direct finance import/registration; iterate `get_modules()` → `register_cli(main)`                                                                                                              |
| `src/cofounder_agent/pyproject.toml`                                   | Packaging / entry-points            | Drop in-tree `content` + `finance` from `poindexter.modules` (scan is authoritative); keep the group header for external modules                                                                       |
| `scripts/sync-to-github.sh`                                            | Public-mirror filter                | Delete the two substrate-patch loops; keep the `modules/finance/` path strip; drop the now-dead `cli/finance.py` strip                                                                                 |
| `scripts/ci/check_public_mirror_safety.py`                             | Leak-guard backstop                 | Delete the mirrored registry/CLI pattern arrays; drop `cli/finance.py` from the path mirror                                                                                                            |
| `src/cofounder_agent/tests/unit/plugins/test_module_discovery_scan.py` | Pins the scan contract              | **Created**                                                                                                                                                                                            |
| `src/cofounder_agent/tests/unit/plugins/test_cli_module_discovery.py`  | Pins CLI `register_cli` iteration   | **Created**                                                                                                                                                                                            |

---

## Task 1: Presence-based in-tree module + job discovery

**Files:**

- Modify: `src/cofounder_agent/plugins/registry.py` (add scan functions after line 83, before `_load_group`)
- Modify: `src/cofounder_agent/modules/finance/jobs/__init__.py` (currently empty)
- Test: `src/cofounder_agent/tests/unit/plugins/test_module_discovery_scan.py` (create)

- [ ] **Step 1: Export the finance job so the scan can find it**

Replace the empty `src/cofounder_agent/modules/finance/jobs/__init__.py` with:

```python
"""Finance module-owned scheduled jobs.

``JOBS`` is the presence-based discovery surface: ``plugins.registry``'s
in-tree module scan reads this list and registers each job into the
``jobs`` plugin bucket. Because the list lives inside ``modules/finance/``,
stripping the finance package from the public mirror removes the job with
zero substrate edits (Module v1 Phase 5).
"""

from __future__ import annotations

from .poll_mercury import PollMercuryJob

JOBS = [PollMercuryJob]

__all__ = ["JOBS", "PollMercuryJob"]
```

- [ ] **Step 2: Write the failing test**

Create `src/cofounder_agent/tests/unit/plugins/test_module_discovery_scan.py`:

```python
"""Unit tests for presence-based in-tree module discovery (Module v1 Phase 5).

The scan replaces the hardcoded content/finance ``_SAMPLES`` tuples. These
tests pin: (1) the real in-tree tree is discovered, (2) an absent module
directory simply isn't registered, (3) a listed-but-broken module raises
loud per feedback_no_silent_defaults.
"""

from __future__ import annotations

import pytest

from plugins import registry
from plugins.registry import (
    ModuleDiscoveryError,
    _scan_intree_modules,
)


def _names(modules):
    return sorted(m.manifest().name for m in modules)


def test_scan_discovers_real_intree_modules():
    """The real modules/ tree yields content + finance, and finance's
    poll_mercury job rides along via modules/finance/jobs/JOBS."""
    scanned = _scan_intree_modules()
    assert "content" in _names(scanned["modules"])
    assert "finance" in _names(scanned["modules"])
    job_names = {getattr(j, "name", None) for j in scanned["jobs"]}
    assert "poll_mercury" in job_names


def test_absent_module_directory_is_not_registered(monkeypatch):
    """Simulate the stripped public mirror: only `content` on disk.
    finance must vanish from both modules and jobs — no source edits."""
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["content"])
    scanned = _scan_intree_modules()
    assert _names(scanned["modules"]) == ["content"]
    job_names = {getattr(j, "name", None) for j in scanned["jobs"]}
    assert "poll_mercury" not in job_names


def test_present_but_broken_module_raises(monkeypatch):
    """A directory the scan lists but cannot import is a real bug and
    must fail loud (feedback_no_silent_defaults), not silently skip."""
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["bogus_broken"])
    with pytest.raises(ModuleDiscoveryError):
        _scan_intree_modules()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_discovery_scan.py -q`
Expected: FAIL — `ImportError: cannot import name 'ModuleDiscoveryError' from 'plugins.registry'`

- [ ] **Step 4: Add the scan implementation**

In `src/cofounder_agent/plugins/registry.py`, add `from pathlib import Path` and `import importlib` to the imports at the top (after `import re`), then insert this block immediately after the `ENTRY_POINT_GROUPS` dict (after line 83):

```python
class ModuleDiscoveryError(RuntimeError):
    """Raised when an in-tree module directory is present but fails to
    load or validate. In-tree modules are first-party code, so a broken
    one is a real bug that must fail loud (feedback_no_silent_defaults) —
    unlike entry-point (third-party) modules, which are dropped-and-warned
    by ``_validate_modules``."""


_INTREE_MODULES_IMPORT_ROOT = "modules"
"""Import root for in-tree business modules (``modules.content``,
``modules.finance``, …). The worker/CLI run with ``src/cofounder_agent``
on ``sys.path``, so this is a top-level package."""


def _intree_modules_path() -> Path:
    """Filesystem path of the in-tree ``modules`` package."""
    pkg = importlib.import_module(_INTREE_MODULES_IMPORT_ROOT)
    return Path(pkg.__path__[0])


def _intree_module_names() -> list[str]:
    """List in-tree module slugs — every ``modules/<name>/`` directory
    that contains a ``<name>_module.py`` and whose name is a valid module
    slug. This is the single presence signal: a name appears here iff its
    package directory is on disk. Tests monkeypatch this to simulate a
    stripped mirror.
    """
    base = _intree_modules_path()
    names: list[str] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(("_", ".")):
            continue
        if not _MODULE_NAME_RE.match(entry.name):
            continue
        if not (entry / f"{entry.name}_module.py").exists():
            continue
        names.append(entry.name)
    return names


def _load_intree_module(name: str) -> Any:
    """Import ``modules.<name>.<name>_module`` and instantiate its Module
    class. The class is found via the module's ``__all__`` (both shipped
    modules declare it) with a fallback to the first class defined in the
    file that exposes a ``manifest`` method."""
    mod = importlib.import_module(
        f"{_INTREE_MODULES_IMPORT_ROOT}.{name}.{name}_module"
    )
    cls = None
    exported = getattr(mod, "__all__", None)
    if exported:
        cls = getattr(mod, exported[0], None)
    if cls is None or not hasattr(cls, "manifest"):
        for obj in vars(mod).values():
            if (
                isinstance(obj, type)
                and obj.__module__ == mod.__name__
                and hasattr(obj, "manifest")
            ):
                cls = obj
                break
    if cls is None:
        raise ModuleDiscoveryError(
            f"in-tree module {name!r}: no Module class with a manifest() "
            f"method found in {mod.__name__}"
        )
    return cls()


def _load_intree_module_jobs(name: str) -> list[Any]:
    """Return the module-owned jobs declared in
    ``modules/<name>/jobs/__init__.py`` as a ``JOBS`` list. A module
    without a ``jobs`` package contributes none."""
    try:
        jobs_pkg = importlib.import_module(
            f"{_INTREE_MODULES_IMPORT_ROOT}.{name}.jobs"
        )
    except ModuleNotFoundError:
        return []
    return [job_cls() for job_cls in getattr(jobs_pkg, "JOBS", [])]


def _scan_intree_modules() -> dict[str, list[Any]]:
    """Discover in-tree modules (and their owned jobs) by directory scan.

    Returns ``{"modules": [...], "jobs": [...]}``. A directory listed by
    ``_intree_module_names`` that fails to load raises
    ``ModuleDiscoveryError`` (present-but-broken = loud); an absent
    directory is simply never listed (expected-absent = silent, but the
    final discovered set is logged)."""
    modules_out: list[Any] = []
    jobs_out: list[Any] = []
    for name in _intree_module_names():
        try:
            modules_out.append(_load_intree_module(name))
            jobs_out.extend(_load_intree_module_jobs(name))
        except ModuleDiscoveryError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as loud domain error
            raise ModuleDiscoveryError(
                f"in-tree module {name!r} is present but failed to load: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    logger.info(
        "module discovery: in-tree modules=%s jobs=%s",
        [m.manifest().name for m in modules_out],
        [getattr(j, "name", "?") for j in jobs_out],
    )
    return {"modules": modules_out, "jobs": jobs_out}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_discovery_scan.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/plugins/registry.py \
        src/cofounder_agent/modules/finance/jobs/__init__.py \
        src/cofounder_agent/tests/unit/plugins/test_module_discovery_scan.py
git commit -m "feat(modules): presence-based in-tree module + job discovery (Phase 5)"
```

---

## Task 2: Source modules + jobs from the scan in get_core_samples()

**Files:**

- Modify: `src/cofounder_agent/plugins/registry.py` — `get_core_samples()` **only**. Do NOT change `get_modules()`.
- Test: `src/cofounder_agent/tests/unit/plugins/test_module_discovery_scan.py` (extend)

**Why `get_modules()` is left untouched (important):** `get_modules()` calls `_validate_modules(_merge_with_core_samples("modules", …))`, and `_merge_with_core_samples` reads `get_core_samples()["modules"]`. The existing `tests/unit/plugins/test_module_registry.py` tests patch **`_merge_with_core_samples` directly** to inject synthetic modules. So as long as `get_modules()` keeps routing through it, every one of those tests passes **unchanged** — we only change what `get_core_samples()` puts in the `modules`/`jobs` buckets. (Module-name precedence between an in-tree and an external module of the same name follows the existing merge + `_validate_modules` first-discovered-wins semantics; this is academic today since there are zero external `poindexter.modules` packages — revisit if one ever ships, per the design doc's open question.)

- [ ] **Step 1: Write the failing regression test**

Append to `src/cofounder_agent/tests/unit/plugins/test_module_discovery_scan.py`:

```python
def test_get_modules_uses_scan(monkeypatch):
    """get_modules() surfaces the scanned in-tree modules (via the
    unchanged _merge_with_core_samples → get_core_samples path)."""
    from plugins.registry import clear_registry_cache, get_modules

    clear_registry_cache()
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["content", "finance"])
    names = sorted(m.manifest().name for m in get_modules())
    assert names == ["content", "finance"]


def test_stripped_tree_has_no_finance_anywhere(monkeypatch):
    """The leak-class regression: with finance absent on disk, neither
    get_modules() nor the jobs bucket reference finance — the exact
    consistency the old substrate line-patching guaranteed by hand."""
    from plugins.registry import clear_registry_cache, get_core_samples, get_modules

    clear_registry_cache()
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["content"])
    assert sorted(m.manifest().name for m in get_modules()) == ["content"]
    job_names = {getattr(j, "name", None) for j in get_core_samples()["jobs"]}
    assert "poll_mercury" not in job_names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_discovery_scan.py -q`
Expected: FAIL — `test_stripped_tree_has_no_finance_anywhere` fails because `get_core_samples()["jobs"]` still contains the hardcoded `poll_mercury` tuple, and `get_modules()` still surfaces `finance` from the hardcoded module tuple.

- [ ] **Step 3: Source modules + jobs from the scan in `get_core_samples()`**

In `get_core_samples()`, immediately after `samples: dict[str, list[Any]] = {k: [] for k in ENTRY_POINT_GROUPS}` (line 384), insert:

```python
    # In-tree business modules + their owned jobs are discovered by
    # directory scan (Module v1 Phase 5) — not hardcoded below — so a
    # stripped private module disappears from every bucket by deleting its
    # package directory alone.
    _scanned = _scan_intree_modules()
    samples["modules"].extend(_scanned["modules"])
    samples["jobs"].extend(_scanned["jobs"])
```

Then DELETE these three tuples (and their comments) from the `_SAMPLES` list (lines 388-398):

```python
        # Module v1 Phase 3-lite — ContentModule is the first concrete
        # business module. Lives in-tree at cofounder_agent.modules.content
        # while we prove the shape; extracts to its own top-level package
        # when 2+ modules give us a comparison point (see Phase 3.5).
        ("modules", "modules.content", "ContentModule"),
        # FinanceModule F1 (2026-05-13) — Mercury read-only banking
        # integration. visibility=private (Matt's operator overlay).
        ("modules", "modules.finance", "FinanceModule"),
        # FinanceModule F2 polling job — pulls accounts + transactions
        # from Mercury hourly. Gated by mercury_enabled in app_settings.
        ("jobs", "modules.finance.jobs.poll_mercury", "PollMercuryJob"),
```

Leave `get_modules()` and every other `_SAMPLES` entry exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass (incl. the untouched registry tests)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_discovery_scan.py tests/unit/plugins/test_module_registry.py -q`
Expected: PASS (all). `test_module_registry.py` passes **without modification** because it patches `_merge_with_core_samples`, which `get_modules()` still calls. If any of those tests fail, STOP — it means `get_modules()` was changed; revert that change (this task must not touch `get_modules()`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/plugins/registry.py \
        src/cofounder_agent/tests/unit/plugins/test_module_discovery_scan.py
git commit -m "feat(modules): source in-tree modules+jobs from scan, drop hardcoded tuples"
```

---

## Task 3: Relocate the finance CLI into the module and iterate register_cli

**Files:**

- Move: `src/cofounder_agent/poindexter/cli/finance.py` → `src/cofounder_agent/modules/finance/cli.py`
- Modify: `src/cofounder_agent/modules/finance/cli.py` (one import line)
- Modify: `src/cofounder_agent/modules/finance/finance_module.py:81-85` (`register_cli`)
- Modify: `src/cofounder_agent/poindexter/cli/app.py:22,83` (remove) + add iteration loop
- Test: `src/cofounder_agent/tests/unit/plugins/test_cli_module_discovery.py` (create)

- [ ] **Step 1: Move the CLI file (preserves history; git mv stages it)**

```bash
git mv src/cofounder_agent/poindexter/cli/finance.py \
       src/cofounder_agent/modules/finance/cli.py
```

- [ ] **Step 2: Fix the relocated file's bootstrap import**

In `src/cofounder_agent/modules/finance/cli.py`, change the relative import (was line 28):

```python
from ._bootstrap import ensure_secret_key, resolve_dsn
```

to the absolute path (the bootstrap helper stays in the CLI package, shared by other commands):

```python
from poindexter.cli._bootstrap import ensure_secret_key, resolve_dsn
```

- [ ] **Step 3: Check for any other importer of the old CLI path and update it**

Run: `cd src/cofounder_agent && grep -rn "poindexter.cli.finance\|from .finance import finance_group\|cli\.finance import" --include=*.py .`
Expected: matches in `poindexter/cli/app.py` (fixed in Step 6) and possibly a test. For any test importing `from poindexter.cli.finance import finance_group`, change it to `from modules.finance.cli import finance_group`. If there are no other matches, proceed.

- [ ] **Step 4: Write the failing test**

Create `src/cofounder_agent/tests/unit/plugins/test_cli_module_discovery.py`:

```python
"""Modules register their own CLI groups via register_cli iteration
(Module v1 Phase 5) — so a stripped private module's CLI travels with
its package and needs no line in the shared CLI bootstrap."""

from __future__ import annotations

import click

from modules.finance.finance_module import FinanceModule
from modules.content.content_module import ContentModule


def _root_group() -> click.Group:
    @click.group()
    def root() -> None:
        pass

    return root


def test_finance_module_registers_its_cli_group():
    root = _root_group()
    FinanceModule().register_cli(root)
    assert "finance" in root.commands


def test_content_module_registers_no_cli_group():
    root = _root_group()
    ContentModule().register_cli(root)
    assert root.commands == {}


def test_register_cli_noops_on_none():
    """The worker lifespan passes None (no CLI host in the worker
    process) — register_cli must no-op, not raise."""
    FinanceModule().register_cli(None)  # must not raise
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_cli_module_discovery.py -q`
Expected: FAIL — `test_finance_module_registers_its_cli_group` fails because `register_cli` is currently a `del parser` no-op.

- [ ] **Step 6: Implement `FinanceModule.register_cli`**

In `src/cofounder_agent/modules/finance/finance_module.py`, replace the existing `register_cli` (lines 81-85):

```python
    def register_cli(self, parser: object) -> None:
        """Phase 4 — ``poindexter finance <subcommand>`` subparsers
        will register here. F1 wires the CLI inline via
        ``cli/finance_commands.py`` until Phase 4 generalizes this."""
        del parser
```

with:

```python
    def register_cli(self, parser: object) -> None:
        """Mount ``poindexter finance <subcommand>`` on the host CLI group.

        ``parser`` is the click root group. The worker lifespan invokes
        this with ``None`` (the worker process hosts no CLI), which is a
        no-op. A non-None host that isn't a click group fails loud per
        feedback_no_silent_defaults rather than silently dropping the
        finance commands.
        """
        if parser is None:
            return
        if not hasattr(parser, "add_command"):
            raise RuntimeError(
                "FinanceModule.register_cli: expected a click Group with "
                f".add_command, got {type(parser).__name__}"
            )
        from modules.finance.cli import finance_group

        parser.add_command(finance_group, name="finance")
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_cli_module_discovery.py -q`
Expected: PASS (3 passed)

- [ ] **Step 8: Wire the CLI bootstrap to iterate modules**

In `src/cofounder_agent/poindexter/cli/app.py`, DELETE the direct finance import (line 22):

```python
from .finance import finance_group
```

and DELETE the direct registration (line 83):

```python
main.add_command(finance_group, name="finance")
```

Then add, immediately before the `if __name__ == "__main__":` block (after line 120), the module-iteration loop:

```python
# Module-contributed CLI groups (Module v1 Phase 5). Each registered
# module mounts its own subcommands via register_cli, so a private
# module's CLI travels with its package — there is no module-specific
# line to strip from this shared bootstrap on the public mirror.
from plugins.registry import get_modules  # noqa: E402 — after static groups

for _module in get_modules():
    _module.register_cli(main)
```

- [ ] **Step 9: Verify the CLI still mounts finance and starts fast**

Run: `cd src/cofounder_agent && poetry run poindexter --help 2>&1 | grep -E "finance|^Commands" | head`
Expected: the help output lists `finance` among the commands (proves the iteration mounts it). If `poindexter` isn't on PATH, use `poetry run python -m poindexter.cli.app --help`.

- [ ] **Step 10: Commit**

```bash
git add src/cofounder_agent/modules/finance/cli.py \
        src/cofounder_agent/modules/finance/finance_module.py \
        src/cofounder_agent/poindexter/cli/app.py \
        src/cofounder_agent/tests/unit/plugins/test_cli_module_discovery.py
git commit -m "feat(modules): finance owns its CLI; bootstrap iterates register_cli (Phase 5)"
```

---

## Task 4: Drop in-tree modules from pyproject entry-points

**Files:**

- Modify: `src/cofounder_agent/pyproject.toml:369-379`

- [ ] **Step 1: Confirm nothing reads the module entry-points directly**

Run: `cd src/cofounder_agent && grep -rn "poindexter.modules\|entry-points.*modules" --include=*.py . | grep -v test_`
Expected: discovery flows through `plugins/registry.py` only (`ENTRY_POINT_GROUPS["modules"]` + `_cached`). No code parses the `poindexter.modules` entry-points outside the registry. If anything else does, stop and reconsider — otherwise proceed.

- [ ] **Step 2: Remove the content + finance entries, keep the group header**

In `src/cofounder_agent/pyproject.toml`, replace the block (lines 377-379):

```toml
[tool.poetry.plugins."poindexter.modules"]
content = "cofounder_agent.modules.content.content_module:ContentModule"
finance = "cofounder_agent.modules.finance.finance_module:FinanceModule"
```

with the group header documenting the seam (no in-tree entries — the scan is authoritative; external/overlay modules register here):

```toml
# In-tree modules (content, finance, …) are discovered by directory scan
# in plugins/registry.py (Module v1 Phase 5) — NOT listed here, so a
# private module never leaks its name through the shared pyproject. This
# group stays declared for EXTERNAL/overlay modules installed via pip,
# which is the discovery path the overlay-package endgame uses.
[tool.poetry.plugins."poindexter.modules"]
```

- [ ] **Step 3: Verify discovery is unaffected**

Run: `cd src/cofounder_agent && poetry run python -c "from plugins.registry import get_modules; print(sorted(m.manifest().name for m in get_modules()))"`
Expected: `['content', 'finance']` (both still discovered — via the scan, not the entry-points).

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/pyproject.toml
git commit -m "chore(modules): drop in-tree modules from pyproject entry-points (scan is authoritative)"
```

---

## Task 5: Simplify the sync filter and leak guard

**Files:**

- Modify: `scripts/sync-to-github.sh` (the "Module v1 private business modules" block)
- Modify: `scripts/ci/check_public_mirror_safety.py` (the mirrored pattern arrays + path mirror)

- [ ] **Step 1: Delete the substrate-patch loops in the sync script**

In `scripts/sync-to-github.sh`, in the `=== Module v1 private business modules ===` block:

- KEEP these path strips:
  ```bash
  git rm -r --cached --quiet src/cofounder_agent/modules/finance/ 2>/dev/null || true
  git rm -r --cached --quiet src/cofounder_agent/tests/unit/modules/finance/ 2>/dev/null || true
  git rm --cached --quiet docs/operations/finance-module-operator.md 2>/dev/null || true
  ```
- DELETE the now-dead CLI strip (the file was relocated into `modules/finance/` in Task 3 and travels with the stripped directory):
  ```bash
  git rm --cached --quiet src/cofounder_agent/poindexter/cli/finance.py 2>/dev/null || true
  ```
- DELETE the entire `PRIVATE_MODULE_REGISTRY_PATTERNS=(...)` array, the `PRIVATE_MODULE_CLI_PATTERNS=(...)` array, and both `for pat in ...; do ... grep -v -F ... done` loops that patch `plugins/registry.py` and `poindexter/cli/app.py`. Replace that whole section with a comment:

  ```bash
  # Module v1 Phase 5 (2026-06-04): private modules are stripped by
  # deleting their package directory alone. In-tree module + job + CLI
  # discovery is presence-based (plugins/registry.py directory scan;
  # modules/<name>/jobs/JOBS; module.register_cli), so there is nothing
  # to patch in the substrate. See docs/architecture/2026-06-04-module-visibility-sync-design.md.
  ```

- [ ] **Step 2: Delete the mirrored pattern arrays in the leak guard**

In `scripts/ci/check_public_mirror_safety.py`:

- DELETE the `PRIVATE_MODULE_REGISTRY_PATTERNS` and `PRIVATE_MODULE_CLI_PATTERNS` definitions (the arrays mirroring the bash patterns, ~lines 214-227) **and any code that consumes them** (the check that asserts these patterns are absent from `registry.py` / `app.py`). Run `grep -n "PRIVATE_MODULE" scripts/ci/check_public_mirror_safety.py` first to find every reference, and remove all of them.
- In the WOULD-SHIP path mirror (the list around lines 104-128 that mirrors `sync-to-github.sh` strips), DELETE the line `"src/cofounder_agent/poindexter/cli/finance.py",` (the file no longer exists). KEEP `"src/cofounder_agent/modules/finance/",` and `"src/cofounder_agent/tests/unit/modules/finance/",`.
- KEEP all content-level leak patterns (`mercury_`, etc.) untouched — they are the backstop that still matters.

- [ ] **Step 3: Run the leak guard against the working tree**

Run: `cd src/cofounder_agent/.. && python3 scripts/ci/check_public_mirror_safety.py; echo "exit=$?"`
Expected: it runs without a Python error (NameError/ImportError from a dangling `PRIVATE_MODULE_*` reference would mean Step 2 missed a consumer). A non-zero "leak found" exit is acceptable here only if it flags a _real_ pre-existing issue unrelated to this change; a clean `exit=0` is expected on the current tree.

- [ ] **Step 4: Commit**

```bash
git add scripts/sync-to-github.sh scripts/ci/check_public_mirror_safety.py
git commit -m "chore(sync): presence-based private-module strip; delete substrate patch lists (Phase 5)"
```

---

## Task 6: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the module + registry + CLI + finance test suites**

Run:

```bash
cd src/cofounder_agent && poetry run pytest \
  tests/unit/plugins/test_module_discovery_scan.py \
  tests/unit/plugins/test_cli_module_discovery.py \
  tests/unit/plugins/test_module_registry.py \
  tests/unit/modules/finance/ -q
```

Expected: all pass. Fix any finance test that imported the old CLI path (`poindexter.cli.finance` → `modules.finance.cli`).

- [ ] **Step 2: Prove the substrate has zero finance coupling**

This is the core end-to-end claim: deleting `modules/finance/` is sufficient because no shared substrate file names finance. Verify directly on the substrate files (no scratch branch, no `git rm` needed — the substrate is finance-free by construction after Tasks 1–4):

```bash
cd src/cofounder_agent/..
echo "=== finance/mercury refs in shared substrate (expect ZERO) ==="
grep -niE "finance|mercury" \
  src/cofounder_agent/plugins/registry.py \
  src/cofounder_agent/poindexter/cli/app.py \
  src/cofounder_agent/pyproject.toml \
  || echo "  none — substrate is clean"
```

Expected: `none — substrate is clean`. Any hit here means a coupling survived and the leak class isn't closed — fix before proceeding.

- [ ] **Step 2b (optional, fuller check): run the leak guard on the working tree**

Run: `python3 scripts/ci/check_public_mirror_safety.py; echo "exit=$?"`
Expected: runs without a Python error and exits 0 (or flags only real pre-existing issues unrelated to this change).

- [ ] **Step 3: Confirm discovery is intact on the full tree**

Run: `cd src/cofounder_agent && poetry run python -c "from plugins.registry import get_modules, get_core_samples; print('modules:', sorted(m.manifest().name for m in get_modules())); print('finance job present:', 'poll_mercury' in {getattr(j,'name',None) for j in get_core_samples()['jobs']})"`
Expected: `modules: ['content', 'finance']` and `finance job present: True`.

- [ ] **Step 4: Run the broader plugins + boot-path test tier as a regression sweep**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/ tests/unit/utils/ -q`
Expected: green (catches any caller of `get_modules`/`get_core_samples`/route registration that the rewire affected). Triage any failure before declaring done.

---

## Notes for the implementer

- **Why the scan is light enough for the CLI:** `get_modules()` now calls `_scan_intree_modules()` directly (not the heavy `get_core_samples()` with its torch/SDXL provider imports). The scan only imports `modules/<name>/<name>_module.py` + `modules/<name>/jobs`, all lightweight. The CLI's new iteration loop therefore does not slow `poindexter --help`. Verify with Step 9 of Task 3.
- **`feedback_no_silent_defaults` is honored two ways:** in-tree present-but-broken raises `ModuleDiscoveryError`; `register_cli`/`register_routes`/`register_probes` on a non-None, wrong-shaped host raise `RuntimeError`. Only _expected absence_ (a module directory not on disk) is silent — and even that is logged.
- **Do not push during this work.** A push to `origin` triggers the live mirror sync. All verification is local (Task 6 uses a throwaway branch). The first real sync should be a deliberate, observed run after this branch merges.
