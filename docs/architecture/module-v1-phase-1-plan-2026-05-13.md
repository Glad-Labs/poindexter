# Module v1 — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Module` Protocol + `ModuleManifest` dataclass + a new `poindexter.modules` entry-point group to the existing plugin registry. No business behavior changes — this is the scaffolding that subsequent phases build on.

**Architecture:** Mirror the existing plugin Protocol pattern (`plugins/tap.py`, `plugins/stage.py`, etc.) — `@runtime_checkable Protocol` + a typed result dataclass + a registry function in `plugins/registry.py`. Modules are discovered via `importlib.metadata.entry_points()`, same mechanism as the existing 19 plugin types.

**Tech Stack:** Python 3.13, `typing.Protocol`, `dataclasses`, `importlib.metadata.entry_points`, pytest + pytest-asyncio.

**Spec reference:** `docs/architecture/module-v1.md` (commit `7cbd8b51`). Umbrella tracker: [Glad-Labs/poindexter#490](https://github.com/Glad-Labs/poindexter/issues/490).

**Scope:** This plan covers only Phase 1 of Module v1. Phases 2-5 (per-module migrations, ContentModule package, route/dashboard auto-discovery, visibility flag) get their own plans after Phase 1 lands.

---

## File Structure

This plan creates 1 new file, modifies 1 existing file, and adds 1 test file.

- **Create** `src/cofounder_agent/plugins/module.py` — the `Module` Protocol, `ModuleManifest` dataclass, and a small `Visibility` literal type. Mirrors the shape of `plugins/probe.py` (single-file Protocol + result class).
- **Modify** `src/cofounder_agent/plugins/registry.py` — add `MODULES` constant to `ENTRY_POINT_GROUPS`, add a `get_modules()` accessor, add `_validate_modules()` helper. Mirrors the existing `get_taps()` / `get_probes()` / etc. shape.
- **Create** `src/cofounder_agent/tests/unit/plugins/test_module_registry.py` — 5 unit tests pinning the contract.

The choice to put Module / ModuleManifest in a single `plugins/module.py` file follows the existing convention. Every other plugin Protocol lives in `plugins/<name>.py` as one file; staying consistent here keeps the plugin tree easy to scan.

---

## Task 1: Define the Module Protocol + ModuleManifest dataclass

**Files:**

- Create: `src/cofounder_agent/plugins/module.py`

- [ ] **Step 1: Read the existing plugin Protocol to confirm the convention**

Run: `cat src/cofounder_agent/plugins/probe.py`

Expected: a file with a result dataclass (`ProbeResult`) and a `@runtime_checkable` Protocol (`Probe`) with one `async def` method. The new `module.py` will mirror this shape.

- [ ] **Step 2: Create the file with the imports + module docstring**

Create `src/cofounder_agent/plugins/module.py` with:

```python
"""``Module`` Protocol — the unit of business-function composition.

A Module bundles the lower-level plugin contributions (stages, reviewers,
probes, jobs, taps, adapters, providers, packs) plus the things the
existing plugin registry doesn't yet track (DB migrations, Grafana
panels, HTTP routes, CLI subcommands).

See ``docs/architecture/module-v1.md`` for the design rationale + the
phased rollout this is Phase 1 of.

Discovery: modules are registered via the ``poindexter.modules`` entry-
point group, the same mechanism as every other plugin type in
``plugins/registry.py``. A ``pyproject.toml`` entry like::

    [project.entry-points."poindexter.modules"]
    content = "poindexter_module_content:ContentModule"

makes the module discoverable. The target resolves to a ``Module``
instance.

Phase 1 (this file) defines the Protocol + manifest dataclass only. The
lifecycle methods (``migrate``, ``register_routes``, etc.) are part of
the Protocol but no host code calls them yet — Phase 2-5 will wire each
call site in turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Visibility = Literal["public", "private"]
"""Whether a module ships in the OSS sync (`public`) or stays in the
glad-labs-stack private overlay (`private`)."""
```

- [ ] **Step 3: Add the `ModuleManifest` dataclass**

Append to `src/cofounder_agent/plugins/module.py`:

```python


@dataclass(frozen=True)
class ModuleManifest:
    """Static description of a module — what it is, who made it, what
    it depends on. Returned by ``Module.manifest()`` and consumed by
    the registry + the OSS sync filter.

    Frozen so a Module's identity is hashable + safe to log without
    fear of accidental mutation. Modules that need runtime state hold
    it elsewhere (on the Module instance itself, or in app_settings).
    """

    name: str
    """Canonical lowercase slug. Used as Grafana folder name, MCP
    namespace, DB-migration prefix, route prefix. Example: ``content``,
    ``finance``, ``gladlabs_business``. Must match
    ``^[a-z][a-z0-9_]*$`` — see ``_NAME_RE`` in
    ``plugins/registry.py``."""

    version: str
    """Semver. Used by future inter-module dependency resolution.
    Phase 1 stores it; Phase 2+ may enforce constraints."""

    visibility: Visibility
    """``public`` → ships in the Glad-Labs/poindexter OSS sync.
    ``private`` → glad-labs-stack overlay only. Default at Module
    instance level (subclasses pick); the registry does not enforce
    a default — every Module must declare it explicitly."""

    description: str = ""
    """One-line human-readable description for ``poindexter modules
    list`` and the eventual operator UI."""

    requires: tuple[str, ...] = field(default_factory=tuple)
    """Dependency specifiers, e.g. ``("substrate>=1.0",
    "module:memory>=0.3")``. Phase 1 stores them; resolution lands
    in a later phase if it proves load-bearing."""
```

- [ ] **Step 4: Add the `Module` Protocol**

Append to `src/cofounder_agent/plugins/module.py`:

```python


@runtime_checkable
class Module(Protocol):
    """A self-contained business function (content, finance, HR, ...).

    Implementations are typically classes that hold their manifest as
    a class attribute or build one in ``manifest()``. The Protocol's
    only hard requirements in Phase 1 are:

    - ``manifest()`` returns a valid ``ModuleManifest``
    - ``healthcheck(pool)`` returns something convertible to a
      ``plugins.probe.ProbeResult`` (Phase 1 returns ``None`` is OK)

    The other lifecycle methods (``migrate``, ``register_routes``,
    etc.) are declared here so the Protocol describes the WHOLE
    Module contract, but Phase 1 does not call them. Subsequent
    phases wire one call site each.
    """

    def manifest(self) -> ModuleManifest:
        """Return this module's manifest. Pure, no I/O."""
        ...

    async def migrate(self, pool: object) -> None:
        """Apply this module's DB migrations. Idempotent. Phase 2.

        ``pool`` is an ``asyncpg.Pool``; typed as ``object`` here to
        keep ``plugins/module.py`` free of an asyncpg import (the
        Protocol is meant to be cheap to import, even for tooling
        that doesn't have asyncpg installed). Implementations may
        narrow the type internally."""
        ...

    def register_routes(self, app: object) -> None:
        """Mount this module's HTTP routes on the host FastAPI app.
        Phase 4. ``app`` is typed as ``object`` for the same import-
        cheapness reason as ``migrate``."""
        ...

    def register_cli(self, parser: object) -> None:
        """Register ``poindexter <module> <subcommand>`` entries on
        the host CLI subparser. Phase 4."""
        ...

    def register_dashboards(self, grafana: object) -> None:
        """Contribute Grafana panels under the module's folder.
        Phase 4."""
        ...

    def register_probes(self, brain: object) -> None:
        """Register this module's brain probes for inclusion in the
        brain daemon's monitoring loop. Phase 4."""
        ...

    async def healthcheck(self, pool: object) -> object:
        """Return a ``plugins.probe.ProbeResult`` summarising this
        module's health. ``None`` is acceptable in Phase 1."""
        ...


__all__ = ["Module", "ModuleManifest", "Visibility"]
```

- [ ] **Step 5: Verify the file parses with the system Python (poetry's 3.13 venv may not be available)**

Run: `python -c "import ast; ast.parse(open('src/cofounder_agent/plugins/module.py').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 6: Verify the file passes ruff (matches the repo's linter)**

Run: `cd src/cofounder_agent && poetry run ruff check plugins/module.py`

Expected: `All checks passed!` (or no output, depending on ruff version).

If ruff reports the bare `object` parameter types are too loose, leave them as-is — they're deliberate to keep this Protocol importable from tooling without an asyncpg/fastapi dependency.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/plugins/module.py
git commit -m "feat(plugins): add Module Protocol + ModuleManifest dataclass (#490 phase 1)

Phase 1 of Module v1 — scaffolding only, no behavior change. The
Protocol declares the FULL lifecycle (manifest / migrate /
register_routes / register_cli / register_dashboards /
register_probes / healthcheck) but only manifest is exercised in
Phase 1. Subsequent phases will wire the rest one call site at a
time.

Mirrors the existing plugin Protocol convention (plugins/probe.py
shape). See docs/architecture/module-v1.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add MODULES to ENTRY_POINT_GROUPS + get_modules() accessor

**Files:**

- Modify: `src/cofounder_agent/plugins/registry.py`

- [ ] **Step 1: Read the existing registry shape**

Run: `grep -n 'ENTRY_POINT_GROUPS\|def get_taps\|def _cached' src/cofounder_agent/plugins/registry.py`

Expected output: a list of line numbers showing the existing pattern. The dict `ENTRY_POINT_GROUPS` declares 19 entries; each `get_<thing>` function is a one-liner calling `_cached("<key>")`.

- [ ] **Step 2: Add `modules` to `ENTRY_POINT_GROUPS`**

In `src/cofounder_agent/plugins/registry.py`, find the `ENTRY_POINT_GROUPS` dict (around line 50) and add a new entry. The dict currently ends with `"publish_adapters": "poindexter.publish_adapters",` — insert ABOVE the closing brace:

```python
ENTRY_POINT_GROUPS: dict[str, str] = {
    "taps": "poindexter.taps",
    "probes": "poindexter.probes",
    # ... existing entries unchanged ...
    "publish_adapters": "poindexter.publish_adapters",
    # Module v1 (Glad-Labs/poindexter#490) — bundles the lower-level
    # plugin contributions into installable, versioned business
    # functions. See docs/architecture/module-v1.md.
    "modules": "poindexter.modules",
}
```

Find the EXACT line via grep first to avoid drift:

Run: `grep -n '"publish_adapters"' src/cofounder_agent/plugins/registry.py`

Then edit at that location.

- [ ] **Step 3: Add a `_NAME_RE` constant for validating module names**

Find the top of `plugins/registry.py` (after the existing imports + `logger = ...`) and add:

```python
import re

_MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
"""Module names must be a lowercase slug — used as a Grafana folder
name, MCP namespace prefix, DB-migration prefix, and HTTP route
prefix. Same constraints as Python package names but stricter
(no uppercase, no dashes).
"""
```

If `import re` is already in the file, skip adding it again — find the existing import block and add `_MODULE_NAME_RE` below it.

Run: `grep -n '^import re\b\|^from re ' src/cofounder_agent/plugins/registry.py`

If empty, add the import.

- [ ] **Step 4: Add the `get_modules()` accessor + `_validate_modules()` helper**

Find the existing `get_publish_adapters()` function (the last `get_*` accessor in the file) and append after it:

```python


def _validate_modules(modules: list[Any]) -> list[Any]:
    """Drop modules whose manifest is malformed. Logs a warning per
    drop so a typo in a downstream package's manifest doesn't silently
    disappear from ``poindexter modules list``.

    Validation rules (Phase 1):
    - ``manifest()`` callable + returns a ``ModuleManifest``
    - ``manifest().name`` matches ``_MODULE_NAME_RE``
    - No two surviving modules share a name (first-discovered wins;
      collisions log a warning)
    """
    from plugins.module import ModuleManifest

    valid: list[Any] = []
    seen_names: set[str] = set()
    for mod in modules:
        manifest_fn = getattr(mod, "manifest", None)
        if not callable(manifest_fn):
            logger.warning(
                "plugins.registry: dropping module %r — no callable "
                "manifest() method",
                mod,
            )
            continue
        try:
            m = manifest_fn()
        except Exception as exc:
            logger.warning(
                "plugins.registry: dropping module %r — manifest() "
                "raised %s: %s",
                mod, type(exc).__name__, exc,
            )
            continue
        if not isinstance(m, ModuleManifest):
            logger.warning(
                "plugins.registry: dropping module %r — manifest() "
                "returned %s, expected ModuleManifest",
                mod, type(m).__name__,
            )
            continue
        if not _MODULE_NAME_RE.match(m.name):
            logger.warning(
                "plugins.registry: dropping module %r — manifest name "
                "%r does not match %s",
                mod, m.name, _MODULE_NAME_RE.pattern,
            )
            continue
        if m.name in seen_names:
            logger.warning(
                "plugins.registry: dropping duplicate module %r — "
                "name %r already registered (first-discovered wins)",
                mod, m.name,
            )
            continue
        seen_names.add(m.name)
        valid.append(mod)
    return valid


def get_modules() -> list[Any]:
    """Return all ``Module`` instances discovered via the
    ``poindexter.modules`` entry-point group, filtered to those with
    valid manifests.

    Results are cached for the process lifetime (same caching layer as
    every other ``get_*`` accessor here). Call ``clear_registry_cache``
    after a ``pip install`` if you need to re-discover.

    See ``docs/architecture/module-v1.md``.
    """
    return _validate_modules(list(_cached("modules")))
```

- [ ] **Step 5: Verify the file parses + imports cleanly**

Run: `cd src/cofounder_agent && poetry run python -c "from plugins.registry import get_modules, ENTRY_POINT_GROUPS; assert 'modules' in ENTRY_POINT_GROUPS; print('OK')"`

Expected: `OK`

- [ ] **Step 6: Verify ruff is happy**

Run: `cd src/cofounder_agent && poetry run ruff check plugins/registry.py`

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/plugins/registry.py
git commit -m "feat(plugins): get_modules() registry accessor + manifest validation (#490 phase 1)

Adds the 20th entry-point group ('poindexter.modules') alongside
the existing 19 plugin types. get_modules() returns Module
instances whose manifest passes validation:

- manifest() is callable + returns a ModuleManifest
- manifest.name matches ^[a-z][a-z0-9_]*\$
- no duplicate names (first-discovered wins; collisions log)

Invalid modules are DROPPED with a warning log, not raised — a
typo in a downstream package shouldn't crash the whole host.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Write the failing test file scaffold

**Files:**

- Create: `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`

- [ ] **Step 1: Verify the test directory exists**

Run: `ls src/cofounder_agent/tests/unit/plugins/`

If the directory doesn't exist:

Run: `mkdir -p src/cofounder_agent/tests/unit/plugins && touch src/cofounder_agent/tests/unit/plugins/__init__.py`

- [ ] **Step 2: Create the test file with imports + fixture**

Create `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`:

```python
"""Unit tests for the ``Module`` Protocol + ``get_modules()`` registry.

Phase 1 of Glad-Labs/poindexter#490 — Module v1. The tests pin the
contract; they do NOT exercise actual entry-point discovery (which
needs an installed package) — instead they patch
``plugins.registry._cached`` to return synthetic modules, which is
the same shape every other ``get_*`` test uses in this repo.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plugins.module import Module, ModuleManifest
from plugins.registry import get_modules


def _make_module(
    name: str = "test_mod",
    version: str = "1.0.0",
    visibility: str = "public",
    description: str = "",
    requires: tuple[str, ...] = (),
):
    """Minimal Module-shaped stub. Implements ``manifest()`` and the
    lifecycle methods as no-ops. Uses a plain class so each test can
    return different stubs to ``_cached``."""

    class _StubModule:
        def manifest(self) -> ModuleManifest:
            return ModuleManifest(
                name=name,
                version=version,
                visibility=visibility,  # type: ignore[arg-type]
                description=description,
                requires=requires,
            )

        async def migrate(self, pool: object) -> None:  # pragma: no cover
            pass

        def register_routes(self, app: object) -> None:  # pragma: no cover
            pass

        def register_cli(self, parser: object) -> None:  # pragma: no cover
            pass

        def register_dashboards(self, grafana: object) -> None:  # pragma: no cover
            pass

        def register_probes(self, brain: object) -> None:  # pragma: no cover
            pass

        async def healthcheck(self, pool: object) -> object:  # pragma: no cover
            return None

    return _StubModule()


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    """Every test starts with a fresh _cached() result. The registry
    caches via functools.cache, so we clear before AND after."""
    from plugins.registry import clear_registry_cache
    clear_registry_cache()
    yield
    clear_registry_cache()
```

- [ ] **Step 3: Run the empty file to confirm pytest collects it**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_registry.py -q`

Expected: `no tests ran` (or `collected 0 items`). Confirms the file is well-formed and importable.

If pytest reports `ImportError`, fix the imports before continuing — likely `plugins.registry.clear_registry_cache` doesn't exist yet, in which case run `grep -n 'clear_registry_cache' src/cofounder_agent/plugins/registry.py` to find the actual function name and adjust the import.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/tests/unit/plugins/test_module_registry.py \
        src/cofounder_agent/tests/unit/plugins/__init__.py
git commit -m "test(plugins): scaffold tests/unit/plugins/test_module_registry.py (#490 phase 1)

Stub helper + autouse cache-clear fixture for the 5 unit tests
that land in Tasks 4-8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Test — `get_modules()` returns an empty list when no modules are registered

**Files:**

- Modify: `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`

- [ ] **Step 1: Append the test**

Append to `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`:

```python


@pytest.mark.unit
def test_get_modules_empty_when_no_entry_points():
    """A fresh install with no module packages installed returns
    an empty list, not an error. Critical because this is the
    base case for the substrate-without-modules deployment."""
    with patch("plugins.registry._cached", return_value=()):
        result = get_modules()
    assert result == []
```

- [ ] **Step 2: Run the test, expect PASS**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_registry.py::test_get_modules_empty_when_no_entry_points -v`

Expected: `PASSED`. Should be green on the first run since `get_modules` is fully implemented in Task 2.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/plugins/test_module_registry.py
git commit -m "test(plugins): empty entry-points returns empty list (#490 phase 1)"
```

---

## Task 5: Test — `get_modules()` returns a valid module unchanged

**Files:**

- Modify: `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`

- [ ] **Step 1: Append the test**

```python


@pytest.mark.unit
def test_get_modules_returns_valid_module():
    """A module whose manifest passes every validation rule comes
    through as-is. Also checks isinstance(mod, Module) since the
    Protocol is runtime_checkable."""
    mod = _make_module(name="content", version="0.1.0", visibility="public")
    with patch("plugins.registry._cached", return_value=(mod,)):
        result = get_modules()
    assert len(result) == 1
    assert result[0] is mod
    # runtime_checkable Protocol — isinstance should succeed
    assert isinstance(mod, Module)
    # Manifest accessor returns what we built
    m = result[0].manifest()
    assert m.name == "content"
    assert m.version == "0.1.0"
    assert m.visibility == "public"
```

- [ ] **Step 2: Run the test, expect PASS**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_registry.py::test_get_modules_returns_valid_module -v`

Expected: `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/plugins/test_module_registry.py
git commit -m "test(plugins): valid module passes through unchanged (#490 phase 1)"
```

---

## Task 6: Test — manifest with invalid name pattern is dropped with a warning

**Files:**

- Modify: `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`

- [ ] **Step 1: Append the test**

```python


@pytest.mark.unit
def test_get_modules_drops_module_with_invalid_name(caplog):
    """A module whose name doesn't match ^[a-z][a-z0-9_]*$ is
    dropped with a warning, not raised. Caller experience:
    poindexter still boots; the bad module just doesn't appear
    in get_modules()."""
    bad_uppercase = _make_module(name="Content")  # uppercase rejected
    bad_dash = _make_module(name="my-module")  # dash rejected
    bad_leading_digit = _make_module(name="1content")  # leading digit rejected
    with patch(
        "plugins.registry._cached",
        return_value=(bad_uppercase, bad_dash, bad_leading_digit),
    ):
        with caplog.at_level("WARNING", logger="plugins.registry"):
            result = get_modules()
    assert result == []
    # One warning per dropped module
    warnings = [
        r for r in caplog.records
        if r.name == "plugins.registry" and r.levelname == "WARNING"
    ]
    assert len(warnings) == 3
    for w in warnings:
        assert "does not match" in w.message
```

- [ ] **Step 2: Run the test, expect PASS**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_registry.py::test_get_modules_drops_module_with_invalid_name -v`

Expected: `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/plugins/test_module_registry.py
git commit -m "test(plugins): invalid name pattern dropped with warning (#490 phase 1)"
```

---

## Task 7: Test — duplicate module names: first-discovered wins, second is dropped

**Files:**

- Modify: `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`

- [ ] **Step 1: Append the test**

```python


@pytest.mark.unit
def test_get_modules_drops_duplicate_names(caplog):
    """When two installed packages both register a module named
    e.g. 'content', the first one wins and the second is dropped
    with a warning. Deterministic precedence: the order
    entry_points() returns them in, which is itself a function of
    package install order + the platform's directory listing
    (entry_points() does NOT sort). This is documented as 'first-
    discovered wins' on purpose — any other policy needs a
    user-configurable precedence map and that's out of scope for
    Phase 1."""
    first = _make_module(name="content", version="1.0.0")
    second = _make_module(name="content", version="2.0.0")
    with patch("plugins.registry._cached", return_value=(first, second)):
        with caplog.at_level("WARNING", logger="plugins.registry"):
            result = get_modules()
    assert len(result) == 1
    assert result[0] is first
    assert result[0].manifest().version == "1.0.0"
    # One warning for the dropped duplicate
    dup_warnings = [
        r for r in caplog.records
        if r.name == "plugins.registry" and "duplicate module" in r.message
    ]
    assert len(dup_warnings) == 1
```

- [ ] **Step 2: Run the test, expect PASS**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_registry.py::test_get_modules_drops_duplicate_names -v`

Expected: `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/plugins/test_module_registry.py
git commit -m "test(plugins): duplicate module names — first wins, second logged (#490 phase 1)"
```

---

## Task 8: Test — module whose `manifest()` raises is dropped (not propagated)

**Files:**

- Modify: `src/cofounder_agent/tests/unit/plugins/test_module_registry.py`

- [ ] **Step 1: Append the test**

```python


@pytest.mark.unit
def test_get_modules_drops_module_whose_manifest_raises(caplog):
    """A third-party module package whose manifest() raises at
    discovery time must NOT crash the host — drop it with a warning
    and continue. The blast radius of one bad manifest is exactly
    one module."""

    class _ExplodingModule:
        def manifest(self) -> ModuleManifest:
            raise RuntimeError("kaboom — package corrupted")

        async def migrate(self, pool):  # pragma: no cover
            pass

        def register_routes(self, app):  # pragma: no cover
            pass

        def register_cli(self, parser):  # pragma: no cover
            pass

        def register_dashboards(self, grafana):  # pragma: no cover
            pass

        def register_probes(self, brain):  # pragma: no cover
            pass

        async def healthcheck(self, pool):  # pragma: no cover
            return None

    exploding = _ExplodingModule()
    healthy = _make_module(name="content")
    with patch("plugins.registry._cached", return_value=(exploding, healthy)):
        with caplog.at_level("WARNING", logger="plugins.registry"):
            result = get_modules()
    # Healthy module survives
    assert len(result) == 1
    assert result[0] is healthy
    # Exploding module dropped with the cause logged
    raise_warnings = [
        r for r in caplog.records
        if r.name == "plugins.registry"
        and "manifest() raised" in r.message
    ]
    assert len(raise_warnings) == 1
    assert "kaboom" in raise_warnings[0].message
```

- [ ] **Step 2: Run the test, expect PASS**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_registry.py::test_get_modules_drops_module_whose_manifest_raises -v`

Expected: `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/unit/plugins/test_module_registry.py
git commit -m "test(plugins): manifest() raising drops the module, not the host (#490 phase 1)"
```

---

## Task 9: Full file run — every test green together

**Files:**

- None (run-only)

- [ ] **Step 1: Run the entire module-registry test file**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_module_registry.py -v`

Expected: 5 tests collected, 5 PASSED, 0 failures. The exact 5 are:

- `test_get_modules_empty_when_no_entry_points`
- `test_get_modules_returns_valid_module`
- `test_get_modules_drops_module_with_invalid_name`
- `test_get_modules_drops_duplicate_names`
- `test_get_modules_drops_module_whose_manifest_raises`

If any fail, fix the underlying code (in `plugins/module.py` or `plugins/registry.py`) — never modify a test to make it pass without re-reading the contract it's pinning.

- [ ] **Step 2: Run the broader plugin unit-test suite to confirm no regression**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/ -q --tb=no`

Expected: all tests pass. If any pre-existing plugin tests fail, the change to `plugins/registry.py` may have a backward-compat issue — investigate before continuing.

- [ ] **Step 3: Confirm ruff is happy across the whole touched surface**

Run: `cd src/cofounder_agent && poetry run ruff check plugins/module.py plugins/registry.py tests/unit/plugins/test_module_registry.py`

Expected: `All checks passed!`.

---

## Task 10: Update the CLAUDE.md plugin-section pointer + close the Phase 1 box on the umbrella

**Files:**

- Modify: `CLAUDE.md`
- Modify: nothing on disk for the umbrella — checkbox-update via `gh issue edit`

- [ ] **Step 1: Find the right CLAUDE.md section**

Run: `grep -n 'plugin\|Plugin\|registry' CLAUDE.md`

Look for the "MCP Server" or "Plugin" section — there should be a list of cross-cutting infrastructure. If there isn't a clearly-right insertion point, add a one-line note under "Reference Documentation":

```markdown
- **Module v1 (Glad-Labs/poindexter#490):** [`docs/architecture/module-v1.md`](docs/architecture/module-v1.md) — Phase 1 (Module Protocol + get_modules() registry) shipped 2026-MM-DD. The 20th entry-point group joins the existing 19 in `plugins/registry.py`.
```

Replace `2026-MM-DD` with the actual date when committing.

- [ ] **Step 2: Verify the docs pointer is reachable + the markdown renders cleanly**

Run: `head -5 docs/architecture/module-v1.md`

Expected: the spec's title + status line.

- [ ] **Step 3: Tick the Phase 1 checkbox on umbrella poindexter#490**

Run:

```bash
gh issue view 490 --repo Glad-Labs/poindexter --json body --jq '.body' > /tmp/umbrella_body.md
# In an editor or via sed, change:
#   - [ ] Phase 1 — Module manifest + registry — ...
# to:
#   - [x] Phase 1 — Module manifest + registry — ... (shipped <commit-sha>)
sed -i 's|- \[ \] Phase 1 — Module manifest + registry|- [x] Phase 1 — Module manifest + registry|' /tmp/umbrella_body.md
gh issue edit 490 --repo Glad-Labs/poindexter --body-file /tmp/umbrella_body.md
```

If `sed` isn't available, edit the body manually via `gh issue edit 490 --repo Glad-Labs/poindexter` (opens $EDITOR).

- [ ] **Step 4: Commit CLAUDE.md update + push**

```bash
git add CLAUDE.md
git commit -m "docs(claude): point at Module v1 (#490 phase 1 shipped)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

- [ ] **Step 5: Final verification — Phase 1 acceptance**

Confirm each of these is true:

1. `plugins/module.py` exists, defines `Module`, `ModuleManifest`, `Visibility`.
2. `plugins/registry.py` has `"modules": "poindexter.modules"` in `ENTRY_POINT_GROUPS`.
3. `plugins/registry.py` exports `get_modules()` that drops invalid manifests with a warning.
4. `tests/unit/plugins/test_module_registry.py` — 5/5 tests green.
5. `CLAUDE.md` — points at the spec + notes Phase 1 shipped.
6. Umbrella poindexter#490 — Phase 1 checkbox is `[x]`.

Run: `cd src/cofounder_agent && poetry run python -c "from plugins.registry import get_modules; print('get_modules() returns:', get_modules())"`

Expected: `get_modules() returns: []` (no Module packages installed yet — Phase 1 is just the scaffolding).

---

## What's NOT in this plan (Phases 2-5)

Each of these gets its own plan after Phase 1 lands + Matt reviews how Phase 1 looks in practice:

- **Phase 2** — Per-module migration runner. New `services/module_migrations.py`. New `module_schema_migrations` table. Boot wiring in `main.py`.
- **Phase 3** — Convert `content/` pipeline code into a `ContentModule` package.
- **Phase 4** — `Module.register_routes(app)` + `Module.register_dashboards(grafana)` auto-discovery.
- **Phase 5** — `Visibility` flag integration with `scripts/sync-to-github.sh`.

Track at: [Glad-Labs/poindexter#490](https://github.com/Glad-Labs/poindexter/issues/490).
