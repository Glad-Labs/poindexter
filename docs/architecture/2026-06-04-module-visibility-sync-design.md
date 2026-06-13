# Module Visibility Sync — presence-based discovery for the public mirror

**Status:** ✅ **Shipped** — Module v1 **Phase 5** (presence-based discovery) is live: `plugins/registry.py` directory-scans `modules/*/` (`for entry in sorted(base.iterdir())`) and the private `finance` CLI group was relocated into the module (`modules/finance/cli.py`). This is the approved design (2026-06-04) it implemented.
**Date:** 2026-06-04 (brainstormed with Matt 2026-06-04)
**Tracker:** [Glad-Labs/poindexter#490](https://github.com/Glad-Labs/poindexter/issues/490) (Module v1 umbrella) — Phase 5 child.
**Supersedes:** the hand-maintained private-module strip + substrate line-patching in the public-mirror sync filter.
**Related:** [`module-v1.md`](module-v1.md) (the substrate this builds on), [`overview.md`](overview.md).

---

## Problem

A private business module (the `finance` overlay) is removed from the public
mirror today by a combination of **path strips and surgical source patching**.
That removal logic is brittle because a single private module's identity is
declared across **four** surfaces that are kept in sync by hand:

1. The module **package directory** (`modules/<name>/`).
2. An **entry-point** in the shared `pyproject.toml`
   (`[tool.poetry.plugins."poindexter.modules"]`).
3. An imperative **registry fallback** (`plugins/registry.py`, the core-samples
   list) that re-declares the module — and its jobs — by literal string.
4. A **CLI registration** line in the shared CLI bootstrap
   (`poindexter/cli/app.py`).

The mirror sync strips surface 1 and **line-patches** surfaces 3 and 4 with
literal-string filters. This has three concrete failure modes:

- **Drift.** The literal-string patch arrays are mirrored a _second_ time in the
  CI leak-guard (`scripts/ci/check_public_mirror_safety.py`). Two hand-maintained
  copies of the same pattern list have already drifted apart and caused repeated
  sync failures (documented in the sync script's own history).
- **Silent no-op.** If any patched line is reformatted, the literal-string filter
  stops matching and silently does nothing — yielding either a leaked private
  name or an internally inconsistent public tree.
- **An unstripped surface.** Surface 2 (the `pyproject.toml` entry-point line) is
  **not** stripped at all today, so the public mirror ships an entry-point
  pointing at a deleted package — a dangling registration that also leaks the
  private module's name.

The root cause is **scatter**: "which modules exist, and which are private" is
encoded in four places instead of one.

## Goal

Collapse private-module removal to **presence/absence of the module directory**.
After this change, the mirror sync strips a private module by deleting its
directory and nothing else — no source patching, no second pattern list.

## Non-goals

- **Relocating private modules out of the shared tree** (the "overlay package"
  endgame). That is a larger, separate move; this design deliberately leaves a
  clean seam for it (see [The C-seam](#the-c-seam)) but does not perform it.
- **Changing the cosmetic substitutions, CHANGELOG redaction, `docs.json`
  rewrite, premium-surface strips, or the leak-guard backstop.** Those are
  separate concerns that work today and stay as-is.
- **Touching the public `content` module's behaviour.** Only the private-module
  coupling is removed; `content` is discovered the same new way but is otherwise
  unchanged.

---

## The seam: presence-based discovery

A module's **presence in `modules/<name>/` becomes the only signal** the rest of
the system reads. Discovery flows two ways into the existing `get_modules()`
merge, split by where a module physically lives:

| Module location                                     | Discovery mechanism                                     | Why                                                                                                                   |
| --------------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| **In-tree** (`src/cofounder_agent/modules/<name>/`) | **Directory scan**                                      | Presence on disk is authoritative; removing the directory removes the module from every surface with no source edits. |
| **External / overlay** (a `pip install`ed package)  | **Entry-point group** (`poindexter.modules`, unchanged) | The mechanism a future private-overlay package or a third-party module uses.                                          |

`visibility` is read from each discovered module's `ModuleManifest` (the field
already exists on the `Module` Protocol) — there is no separate visibility list
to maintain.

This per-location split is the whole design. It replaces both the hardcoded
core-samples module tuples **and** the shared-`pyproject.toml` module
entry-points for in-tree modules, while leaving the entry-point group intact as
the external-module path.

---

## What changes (every surface, derived from the seam)

| Surface                                    | Today                                                                                               | After                                                                                                                  |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `plugins/registry.py`                      | Core-samples list hardcodes the private module + its job by literal string                          | Directory-scans `modules/*/`; **zero** module names hardcoded → nothing to patch                                       |
| `poindexter/cli/app.py`                    | Imports the private module's CLI group by name (`from .finance import …`)                           | Iterates `get_modules()` and calls each module's `register_cli()` → no module-specific line                            |
| Private module's CLI group                 | Lives in the shared CLI tree (must be stripped separately)                                          | **Relocated into the module** (`modules/<name>/cli.py`, exposed via `register_cli()`) → travels with the directory     |
| `pyproject.toml`                           | Lists in-tree modules as `poindexter.modules` entry-points (the private one is the unstripped leak) | In-tree modules dropped from entry-points (the scan is authoritative); the group remains declared for external modules |
| Public-mirror sync filter                  | Strips the directory **and** patches two substrate files via literal-string arrays                  | Strips the private module's directory + tests + operator doc **only**; both patch loops **deleted**                    |
| `scripts/ci/check_public_mirror_safety.py` | Re-declares the registry/CLI patch patterns (the second hand-maintained list)                       | Mirror arrays **deleted**; keeps the path-strip mirror + content-level leak patterns                                   |

**Net effect:** two hand-maintained pattern lists deleted, the unstripped
`pyproject.toml` leak closed, and the sync filter's module section reduced to
pure path-strips.

---

## Error handling — honoring "no silent defaults"

Directory-scan discovery must not convert "module missing" into a silent
default. The contract:

- **Directory present, but the module fails to import or its manifest is
  invalid → raise loud.** This is a real bug and must fail per the project's
  no-silent-defaults rule.
- **Directory absent → not registered, but logged.** A deployment legitimately
  ships fewer modules than another (the public mirror has no `finance`). Absence
  is expected and therefore _not_ an error — but it is **emitted in a startup
  log line**, never silently inferred.
- **Startup manifest log.** Boot emits the discovered module set
  (e.g. `module discovery: in-tree=[content], external=[]`) so "is this module
  loaded here?" is answerable from a log line rather than by inference.

The distinction that makes this safe: _present-but-broken_ is loud;
_expected-absent_ is logged. The public mirror's reduced module set is the
expected-absent case; a genuine import regression on the operator side is the
loud case.

---

## The C-seam

The reason for choosing directory-scan-vs-entry-point _as the discovery split_
(rather than a simpler tolerant-import hack) is to keep the overlay-package
endgame additive. After this design lands, relocating a private module to its
own `pip`-installable overlay package — the Module v1 long-term target — is:

1. Create the overlay package; declare its `poindexter.modules` entry-point.
2. Delete `modules/<name>/` from the shared tree.

Discovery already handles entry-point modules, so **registry, CLI, and the sync
filter need no further change.** The module simply moves from the scan path to
the entry-point path it already supports. Same contract, different fill-in.

---

## Testing strategy

Each piece ships with a test that pins the contract:

- **`test_module_discovery_scan.py`** — the scan finds in-tree modules; a
  present-but-broken module package raises; an absent directory is
  silently-OK-but-logged (assert the log line, assert no raise).
- **`test_visibility_filter.py`** — feed a mixed-visibility module set; assert
  only `visibility="public"` modules survive the mirror filter. (This is the
  Phase 5 test named in `module-v1.md`.)
- **`test_cli_module_discovery.py`** — `register_cli` iteration mounts a present
  module's CLI group and omits an absent one.
- **Leak-class regression test** — simulate the stripped tree (no private module
  directory) and assert that the registry, the CLI, and `get_modules()` are
  internally consistent with **zero** references to the private module anywhere.
  This is the direct regression test for the failure this design eliminates.

---

## Migration / rollout

1. Land the directory-scan discovery in `registry.py` alongside the existing
   entry-point path (both feed `get_modules()`; de-dup by `.name` as today).
   Drop in-tree modules from the `pyproject.toml` entry-points in the same change
   so the scan is unambiguously authoritative for them.
2. Relocate the private module's CLI group into the module; switch
   `cli/app.py` to `register_cli()` iteration.
3. Delete the sync filter's two substrate patch loops and the leak-guard's
   mirror arrays; keep the directory path-strip.
4. Run the full sync against a scratch mirror branch and assert a clean,
   consistent public tree with no private-module references (the leak-class
   regression test, run end-to-end).

Each step keeps existing tests green; the change is structural, not behavioural.

## Open questions

- **Discovery order of precedence.** When an in-tree module and an external
  entry-point module share a `name`, which wins? Proposed: entry-point wins
  (mirrors the existing plugin merge contract, where installed packages override
  core samples), but it should be asserted in a test and logged.
- **Should `content` also drop its `pyproject.toml` entry-point**, or keep it for
  symmetry with future external modules? Proposed: drop it (the scan is
  authoritative for everything in-tree); revisit only if a packaging consumer
  needs the static declaration.
