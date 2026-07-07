# Writer First-Party Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ground the canonical_blog writer on scrubbed `claude_sessions` (first-party operator knowledge) instead of prior posts (the corpus-autophagy source), with posts capped at 2 and a reusable operator-info scrub protecting public content.

**Architecture:** A shared `services/rag_scrub.py` composes secret + private-repo + operator-identity redaction; operator-identity patterns live in a mirror-stripped overlay module loaded via a no-op-when-absent hook (mirrors `apply_operator_overrides`). The writer's snippet retrieval gets a decoupled source filter + per-source caps and applies the scrub fail-closed at the read boundary. Re-enable is a prod setting flip after verification.

**Tech Stack:** Python 3.13, pytest (`poetry run pytest`), asyncpg/pgvector, LangGraph (existing two_pass writer graph). No new dependencies.

## Global Constraints

- **OSS behavior must not change until opt-in.** Both new settings (`writer_rag_source_filter`, `writer_rag_source_caps`) default to `''` (the unset sentinel; app_settings values are never NULL). Empty = inherit current behavior.
- **Operator-identity literals must never ship to the public mirror.** Any new file carrying the operator name/path/host (the overlay module + its test) MUST be added to `_STRIP_FILES` in `scripts/ci/check_public_mirror_safety.py`. Public-shipping test files must NOT contain operator literals.
- **Scrub fails CLOSED at read.** If `scrub_rag_text` raises on a snippet, drop that snippet from grounding — never pass unscrubbed operator text to the writer.
- **Deterministic only.** No LLM calls anywhere in this plan.
- **Async-everywhere.** Never block the event loop in async paths.
- **TDD.** Test first, watch it fail, minimal implementation, watch it pass, commit.
- **Branch:** `claude/writer-session-grounding` (already created; spec committed).
- **Test command:** `cd src/cofounder_agent && poetry run pytest <path> -q`

---

## File Structure

- Create `src/cofounder_agent/services/operator_leak_patterns.py` — the operator-identity scrub patterns (STRIPPED from mirror).
- Create `src/cofounder_agent/tests/unit/services/test_operator_leak_patterns.py` — real-pattern matching + drift contract vs the leak guard (STRIPPED).
- Create `src/cofounder_agent/services/rag_scrub.py` — shared scrub library (secrets + private-repo + operator hook). SHIPS public.
- Create `src/cofounder_agent/tests/unit/services/test_rag_scrub.py` — mechanism tests, no operator literals. SHIPS public.
- Modify `scripts/ci/check_public_mirror_safety.py` — add the two new operator files to `_STRIP_FILES`.
- Modify `src/cofounder_agent/services/taps/claude_code_sessions.py` — route the tap's scrub through `scrub_rag_text`.
- Modify `src/cofounder_agent/modules/content/atoms/narrate_bundle.py` — use shared `scrub_private_repo_refs`.
- Modify `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` — source-filter resolution, per-source cap in `_select_snippets`, fail-closed read scrub in `_embed_and_fetch_snippets`, shared output scrub.
- Modify `src/cofounder_agent/services/settings_defaults.py` — two new default keys.
- Extend `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer*.py` — cap + source-filter tests.

---

## Task 1: Operator-identity scrub patterns (stripped overlay)

**Files:**

- Create: `src/cofounder_agent/services/operator_leak_patterns.py`
- Create/Test: `src/cofounder_agent/tests/unit/services/test_operator_leak_patterns.py`
- Modify: `scripts/ci/check_public_mirror_safety.py` (`_STRIP_FILES` tuple, after line ~214)

**Interfaces:**

- Produces: `OPERATOR_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...]` — `(compiled_regex, replacement_token)` pairs. Consumed by `rag_scrub._load_operator_leak_patterns` (Task 2).

- [ ] **Step 1: Write the failing test** (`test_operator_leak_patterns.py`). This file carries operator-shaped literals, so it is stripped from the mirror.

```python
"""Operator-identity scrub patterns — STRIPPED from the public mirror.

Carries operator-shaped literals to prove the patterns fire; must stay in
_STRIP_FILES (asserted by the drift test below).
"""
from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from services.operator_leak_patterns import OPERATOR_SCRUB_PATTERNS


def _scrub(text: str) -> str:
    for rx, repl in OPERATOR_SCRUB_PATTERNS:
        text = rx.sub(repl, text)
    return text


@pytest.mark.unit
class TestOperatorScrubPatterns:
    def test_full_name_incl_middle_initial(self):
        assert "Gladding" not in _scrub("by Matthew M. Gladding, founder")
        assert "Gladding" not in _scrub("Matthew Gladding")

    def test_informal_name(self):
        assert "Gladding" not in _scrub("thanks Matt Gladding")

    def test_windows_home_path(self):
        assert "mattm" not in _scrub(r"C:\Users\mattm\glad-labs-website")

    def test_claude_projects_encoding(self):
        assert "mattm" not in _scrub("~/.claude/projects/C--Users-mattm/memory/x.md")

    def test_tailnet_and_github_handle(self):
        assert "100.81.93.12" not in _scrub("ssh 100.81.93.12")
        assert "mattg-stack" not in _scrub("commit by mattg-stack")

    def test_negative_generic_prose_untouched(self):
        text = "The image of a lone GPU and a matte black case."
        assert _scrub(text) == text


def _load_leak_guard():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    script = repo_root / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location("_mirror_guard", script)
    assert spec is not None and spec.loader is not None
    guard = module_from_spec(spec)
    sys.modules[spec.name] = guard  # register so @dataclass annotation resolution works
    spec.loader.exec_module(guard)
    return guard


@pytest.mark.unit
def test_patterns_are_subset_of_leak_guard():
    """Drift guard: every operator scrub regex must also be a pattern the
    public-mirror leak guard enforces, so the two never diverge."""
    guard = _load_leak_guard()
    # LeakPattern's compiled-regex field is `.regex`; `.pattern` on the compiled
    # regex gives the source string.
    guard_sources = {lp.regex.pattern for lp in guard._LEAK_PATTERNS}
    for rx, _repl in OPERATOR_SCRUB_PATTERNS:
        assert rx.pattern in guard_sources, f"{rx.pattern!r} not in leak guard"


@pytest.mark.unit
def test_operator_files_are_stripped_from_mirror():
    """Fail loud if the overlay module or this test ever leaves _STRIP_FILES —
    either would ship the operator-name literal to the public mirror."""
    strip = set(_load_leak_guard()._STRIP_FILES)
    assert "src/cofounder_agent/services/operator_leak_patterns.py" in strip
    assert "src/cofounder_agent/tests/unit/services/test_operator_leak_patterns.py" in strip
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_operator_leak_patterns.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.operator_leak_patterns'`

- [ ] **Step 3: Create the module** (`services/operator_leak_patterns.py`). Regex sources copied verbatim from `check_public_mirror_safety.py::_LEAK_PATTERNS` (the operator-identity subset only — NOT the bare-number money literals, which false-positive in prose).

```python
"""Operator-identity scrub patterns — STRIPPED from the public mirror.

The regex SOURCES here are copied verbatim from the operator-identity subset of
``scripts/ci/check_public_mirror_safety.py::_LEAK_PATTERNS`` (name, home paths,
Tailnet host, GitHub handle). ``test_operator_leak_patterns`` pins them equal to
the guard so the two can't drift. This file carries the operator-name literal, so
it lives in the guard's ``_STRIP_FILES`` and never ships — ``rag_scrub`` imports it
via a no-op-when-absent hook (OSS installs get generic scrub only).
"""
from __future__ import annotations

import re

OPERATOR_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"100\.81\.93\.12"), "[operator-host]"),
    (re.compile(r"taild4f626\.ts\.net"), "[operator-host]"),
    (re.compile(r"\bnightrider\b"), "[operator-host]"),
    (re.compile(r"C:[\\/]+Users[\\/]+mattm"), "[operator-path]"),
    (re.compile(r"/c/Users/mattm"), "[operator-path]"),
    (re.compile(r"C--Users-mattm", re.IGNORECASE), "[operator-path]"),
    (re.compile(r"mattg-stack"), "[operator]"),
    (re.compile(r"matthew-gladding"), "[operator]"),
    (re.compile(r"[Mm]atthew (?:[A-Z]\.\s+)?[Gg]ladding"), "[operator]"),
    (re.compile(r"[Mm]att [Gg]ladding"), "[operator]"),
)
```

- [ ] **Step 4: Add both new operator files to `_STRIP_FILES`.** In `scripts/ci/check_public_mirror_safety.py`, inside the `_STRIP_FILES` tuple (near the other operator-tooling entries, ~line 204), add:

```python
    "src/cofounder_agent/services/operator_leak_patterns.py",
    "src/cofounder_agent/tests/unit/services/test_operator_leak_patterns.py",
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_operator_leak_patterns.py -q`
Expected: PASS (8 tests). If `test_patterns_are_subset_of_leak_guard` fails, a regex source diverged from the guard — copy the guard's source verbatim.

- [ ] **Step 6: Verify the strip entry holds** (guard doesn't flag its own new file)

Run: `python scripts/ci/check_public_mirror_safety.py`
Expected: exit 0, no new violations naming `operator_leak_patterns.py`.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/operator_leak_patterns.py \
        src/cofounder_agent/tests/unit/services/test_operator_leak_patterns.py \
        scripts/ci/check_public_mirror_safety.py
git commit -m "feat(rag-scrub): operator-identity scrub patterns (stripped overlay)"
```

---

## Task 2: Shared scrub library `rag_scrub.py`

**Files:**

- Create: `src/cofounder_agent/services/rag_scrub.py`
- Create/Test: `src/cofounder_agent/tests/unit/services/test_rag_scrub.py`

**Interfaces:**

- Consumes: `services.operator_leak_patterns.OPERATOR_SCRUB_PATTERNS` (Task 1) via a try/except import.
- Produces:
  - `SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...]`
  - `scrub_private_repo_refs(text: str) -> str`
  - `scrub_rag_text(text: str, *, extra_patterns: list[tuple[re.Pattern[str], str]] | None = None) -> str`

- [ ] **Step 1: Write the failing test** (`test_rag_scrub.py`). SHIPS public — no operator literals; the operator hook is exercised via monkeypatch.

```python
from __future__ import annotations

import re
import pytest

from services import rag_scrub


@pytest.mark.unit
class TestScrubRagText:
    def test_redacts_secrets(self):
        out = rag_scrub.scrub_rag_text("token sk-abcdefghijklmnopqrstuvwxyz012345 end")
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in out
        assert "[REDACTED" in out

    def test_rewrites_private_repo_mention(self):
        out = rag_scrub.scrub_rag_text("see Glad-Labs/glad-labs-stack for details")
        assert "glad-labs-stack" not in out
        assert "Glad-Labs/poindexter" in out

    def test_leaves_public_repo_alone(self):
        text = "the Glad-Labs/poindexter mirror"
        assert rag_scrub.scrub_rag_text(text) == text

    def test_applies_operator_hook_patterns(self, monkeypatch):
        # Inject a synthetic operator pattern — proves composition without
        # shipping a real operator literal in this public test.
        monkeypatch.setattr(
            rag_scrub, "_load_operator_leak_patterns",
            lambda: [(re.compile(r"ACME-SECRET-HOST"), "[operator-host]")],
        )
        out = rag_scrub.scrub_rag_text("deploy to ACME-SECRET-HOST now")
        assert "ACME-SECRET-HOST" not in out
        assert "[operator-host]" in out

    def test_oss_no_op_when_overlay_absent(self, monkeypatch):
        monkeypatch.setattr(rag_scrub, "_load_operator_leak_patterns", lambda: [])
        # Still scrubs secrets/repo, just no operator patterns.
        out = rag_scrub.scrub_rag_text("plain text with sk-" + "z" * 40)
        assert "[REDACTED" in out

    def test_extra_patterns_applied(self):
        extra = [(re.compile(r"myproj_token_[0-9]+"), "[REDACTED:custom]")]
        out = rag_scrub.scrub_rag_text("myproj_token_12345", extra_patterns=extra)
        assert "myproj_token_12345" not in out

    def test_empty_and_none_safe(self):
        assert rag_scrub.scrub_rag_text("") == ""
        assert rag_scrub.scrub_rag_text(None) == ""  # type: ignore[arg-type]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_rag_scrub.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.rag_scrub'`

- [ ] **Step 3: Create `services/rag_scrub.py`.** `SECRET_PATTERNS` copied verbatim from the tap's `_DEFAULT_SCRUB_PATTERNS`; `_PRIV` + `scrub_private_repo_refs` copied from `narrate_bundle` (the generalized `Glad-Labs/(?!poindexter)` form).

```python
"""Shared RAG scrub — redact secrets, private-repo refs, and operator identity
from any text before it reaches a writer prompt or the embeddings table.

Ships to the public mirror (generic mechanism). Operator-identity patterns load
from the stripped ``services.operator_leak_patterns`` overlay via a
no-op-when-absent hook (mirrors ``settings_defaults.apply_operator_overrides``),
so OSS installs get secret + private-repo scrub only.
"""
from __future__ import annotations

import re

# Secret formats — canonical home (was taps/claude_code_sessions._DEFAULT_SCRUB_PATTERNS).
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"enc:v1:[A-Za-z0-9+/=]{40,}"), "[REDACTED:enc]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED:sk-ant]"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "[REDACTED:sk]"),
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[REDACTED:ghp]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{50,}"), "[REDACTED:github_pat]"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "[REDACTED:aws]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-=]{10,}\.[A-Za-z0-9_\-=]{10,}\.[A-Za-z0-9_\-/+=]{20,}"), "[REDACTED:jwt]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED:slack]"),
)

# Private-repo refs — generalized Glad-Labs org form (excludes the public mirror).
_PRIV = r"Glad-Labs/(?!poindexter\b)[A-Za-z0-9._-]+"
_PRIVATE_REPO_PULL_INLINE = re.compile(r"\[([^]]+)\]\(https?://github\.com/" + _PRIV + r"/pull/(\d+)\)")
_PRIVATE_REPO_COMMIT_INLINE = re.compile(r"\[([^]]+)\]\(https?://github\.com/" + _PRIV + r"/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*\)")
_PRIVATE_REPO_PULL_AUTOLINK = re.compile(r"<https?://github\.com/" + _PRIV + r"/pull/(\d+)>")
_PRIVATE_REPO_COMMIT_AUTOLINK = re.compile(r"<https?://github\.com/" + _PRIV + r"/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*>")
_PRIVATE_REPO_PULL_BARE = re.compile(r"https?://github\.com/" + _PRIV + r"/pull/(\d+)")
_PRIVATE_REPO_COMMIT_BARE = re.compile(r"https?://github\.com/" + _PRIV + r"/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*")
_PRIVATE_REPO_MENTION = re.compile(r"\b" + _PRIV + r"\b")


def scrub_private_repo_refs(text: str) -> str:
    """Rewrite private Glad-Labs repo URLs/mentions to the public mirror."""
    if not text:
        return text
    text = _PRIVATE_REPO_PULL_INLINE.sub(r"\1 (PR #\2)", text)
    text = _PRIVATE_REPO_COMMIT_INLINE.sub(r"\1 (`\2`)", text)
    text = _PRIVATE_REPO_PULL_AUTOLINK.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_AUTOLINK.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_PULL_BARE.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_BARE.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_MENTION.sub("Glad-Labs/poindexter", text)
    return text


def _load_operator_leak_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Operator-identity patterns from the stripped overlay; [] on OSS installs.

    Mirrors ``settings_defaults.apply_operator_overrides`` — the module is absent
    on the public mirror, so this is a no-op there.
    """
    try:
        from services.operator_leak_patterns import OPERATOR_SCRUB_PATTERNS
    except ImportError:
        return []
    return list(OPERATOR_SCRUB_PATTERNS)


def scrub_rag_text(
    text: str,
    *,
    extra_patterns: list[tuple[re.Pattern[str], str]] | None = None,
) -> str:
    """Redact secrets + operator identity and rewrite private-repo refs."""
    if not text:
        return ""
    for rx, repl in SECRET_PATTERNS:
        text = rx.sub(repl, text)
    for rx, repl in extra_patterns or []:
        text = rx.sub(repl, text)
    for rx, repl in _load_operator_leak_patterns():
        text = rx.sub(repl, text)
    text = scrub_private_repo_refs(text)
    return text
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_rag_scrub.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/rag_scrub.py src/cofounder_agent/tests/unit/services/test_rag_scrub.py
git commit -m "feat(rag-scrub): shared scrub_rag_text (secrets + private-repo + operator hook)"
```

---

## Task 3: Route existing scrubs through `rag_scrub` (consolidate 3 copies)

**Files:**

- Modify: `src/cofounder_agent/services/taps/claude_code_sessions.py` (extract(), ~line 352)
- Modify: `src/cofounder_agent/modules/content/atoms/narrate_bundle.py` (lines 48-90 + its caller)
- Modify: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` (`_scrub_private_repo_refs` def lines 93-128 + caller)

**Interfaces:**

- Consumes: `rag_scrub.scrub_rag_text`, `rag_scrub.scrub_private_repo_refs` (Task 2).

- [ ] **Step 1: Point the tap at the shared scrub.** In `claude_code_sessions.py::extract`, replace the secret-only scrub with the full shared scrub. Change line ~352 from:

```python
                text = _scrub(text, scrub_patterns)
```

to:

```python
                from services.rag_scrub import scrub_rag_text
                text = scrub_rag_text(text, extra_patterns=scrub_patterns)
```

Keep `_compile_scrub_patterns` / `_scrub` / `_DEFAULT_SCRUB_PATTERNS` in the module (existing unit tests call them directly). `scrub_patterns` here already holds the caller's `extra_scrub_patterns` compiled with the built-in set; passing it as `extra_patterns` double-covers secrets harmlessly and adds operator scrub.

- [ ] **Step 2: Point `narrate_bundle` at the shared private-repo scrub.** In `narrate_bundle.py`, delete the local `_PRIV` + `_PRIVATE_REPO_*` regexes + `_scrub_private_repo_refs` def (lines ~38-90). Add an import alias near the top so the two existing call sites (lines 407, 708) keep working unchanged:

```python
from services.rag_scrub import scrub_private_repo_refs as _scrub_private_repo_refs
```

Then run `poetry run ruff check modules/content/atoms/narrate_bundle.py` and drop any now-unused `re` import only if ruff flags F401.

- [ ] **Step 3: Point `two_pass_writer` at the shared private-repo scrub.** In `two_pass_writer.py`, delete its local `_PRIV` + `_PRIVATE_REPO_*` regexes + `_scrub_private_repo_refs` def (lines ~86-128). Add the same import alias so the existing call site (line 1810) keeps working unchanged:

```python
from services.rag_scrub import scrub_private_repo_refs as _scrub_private_repo_refs
```

- [ ] **Step 4: Run the affected existing tests to verify no regression**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_claude_code_sessions_tap.py tests/unit/services/atoms/test_narrate_bundle.py -q`
Expected: PASS (secret-redaction + private-repo tests still green; the tap now also applies operator scrub).

- [ ] **Step 5: Run the two_pass writer tests**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/ -k two_pass -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/taps/claude_code_sessions.py \
        src/cofounder_agent/modules/content/atoms/narrate_bundle.py \
        src/cofounder_agent/modules/content/atoms/two_pass_writer.py
git commit -m "refactor(rag-scrub): route tap + narrate_bundle + two_pass through shared scrub"
```

---

## Task 4: Decoupled writer source filter

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` (`_resolve_snippet_source_filter`, lines ~329-353)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (add key near the `writer_rag_*` block)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer_source_filter.py` (new)

**Interfaces:**

- Produces: `_resolve_snippet_source_filter(site_config)` now reads `writer_rag_source_filter` first, then `rag_source_filter`, then `['posts']`.

- [ ] **Step 1: Write the failing test** (new file `test_two_pass_writer_source_filter.py`)

```python
from __future__ import annotations
import pytest
from modules.content.atoms.two_pass_writer import _resolve_snippet_source_filter


class _Cfg:
    def __init__(self, **kv): self._kv = kv
    def get(self, key, default=None): return self._kv.get(key, default)


@pytest.mark.unit
class TestResolveSnippetSourceFilter:
    def test_prefers_writer_setting(self):
        cfg = _Cfg(writer_rag_source_filter="claude_sessions,posts", rag_source_filter="posts")
        assert _resolve_snippet_source_filter(cfg) == ["claude_sessions", "posts"]

    def test_falls_back_to_general_when_writer_empty(self):
        cfg = _Cfg(writer_rag_source_filter="", rag_source_filter="posts")
        assert _resolve_snippet_source_filter(cfg) == ["posts"]

    def test_falls_back_to_posts_when_both_empty(self):
        cfg = _Cfg(writer_rag_source_filter="", rag_source_filter="")
        assert _resolve_snippet_source_filter(cfg) == ["posts"]

    def test_none_site_config_defaults_to_posts(self):
        assert _resolve_snippet_source_filter(None) == ["posts"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer_source_filter.py -q`
Expected: FAIL on `test_prefers_writer_setting` (currently reads `rag_source_filter` only).

- [ ] **Step 3: Update `_resolve_snippet_source_filter`.** Replace the body's setting read:

```python
def _resolve_snippet_source_filter(site_config: Any = None) -> list[str]:
    """Resolve the writer's snippet source allowlist.

    Reads ``writer_rag_source_filter`` first (decoupled from the general
    ``rag_source_filter`` other consumers use), falling back to
    ``rag_source_filter`` then the built-in ``posts`` allowlist. The writer
    NEVER queries unfiltered.
    """
    if site_config is None:
        return list(_DEFAULT_SNIPPET_SOURCE_FILTER)
    try:
        csv = (site_config.get("writer_rag_source_filter", "") or "").strip()
        if not csv:
            csv = (site_config.get("rag_source_filter", "") or "").strip()
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        return list(_DEFAULT_SNIPPET_SOURCE_FILTER)
    parsed = [s.strip() for s in csv.split(",") if s.strip()]
    return parsed or list(_DEFAULT_SNIPPET_SOURCE_FILTER)
```

- [ ] **Step 4: Add the default.** In `settings_defaults.py` `DEFAULTS`, near the other `writer_rag_*` keys:

```python
    # Writer-only snippet source allowlist. Empty = inherit rag_source_filter
    # (which other RAG consumers read). Set to e.g. 'claude_sessions,posts' to
    # ground the writer on first-party sessions without changing internal-link
    # retrieval. The writer never queries unfiltered.
    'writer_rag_source_filter': '',
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer_source_filter.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/two_pass_writer.py \
        src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer_source_filter.py
git commit -m "feat(writer): decoupled writer_rag_source_filter (inherits rag_source_filter)"
```

---

## Task 5: Per-source cap + fail-closed read scrub

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/two_pass_writer.py` (`_select_snippets` lines ~403-449; `_embed_and_fetch_snippets` lines ~454-520; add `_parse_source_caps`)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (add `writer_rag_source_caps`)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer_caps.py` (new)

**Interfaces:**

- Consumes: `rag_scrub.scrub_rag_text` (Task 2).
- Produces: `_select_snippets(candidates, *, k, dedup_ceiling, mmr_lambda, source_caps=None)` — new `source_caps: dict[str, int] | None` kwarg; `_parse_source_caps(site_config) -> dict[str, int]`.

- [ ] **Step 1: Write the failing test** (new file `test_two_pass_writer_caps.py`)

```python
from __future__ import annotations
import pytest
from modules.content.atoms.two_pass_writer import _select_snippets, _parse_source_caps


def _c(source, ref, relevance):
    # vec identical so MMR diversity term is 0 → selection is pure relevance order,
    # making the cap effect deterministic to assert.
    return {"source": source, "ref": ref, "snippet": f"s{ref}", "relevance": relevance, "vec": [1.0, 0.0]}


class _Cfg:
    def __init__(self, **kv): self._kv = kv
    def get(self, key, default=None): return self._kv.get(key, default)


@pytest.mark.unit
class TestSourceCaps:
    def test_posts_capped_sessions_fill(self):
        cands = (
            [_c("posts", i, 0.99 - i * 0.001) for i in range(10)]
            + [_c("claude_sessions", 100 + i, 0.80 - i * 0.001) for i in range(10)]
        )
        out = _select_snippets(cands, k=6, dedup_ceiling=1.0, mmr_lambda=1.0, source_caps={"posts": 2})
        srcs = [s["source"] for s in out]
        assert srcs.count("posts") == 2
        assert srcs.count("claude_sessions") == 4
        assert len(out) == 6

    def test_no_caps_is_unchanged(self):
        cands = [_c("posts", i, 0.9 - i * 0.01) for i in range(5)]
        out = _select_snippets(cands, k=3, dedup_ceiling=1.0, mmr_lambda=1.0, source_caps=None)
        assert len(out) == 3

    def test_cap_larger_than_supply_is_noop(self):
        cands = [_c("posts", i, 0.9 - i * 0.01) for i in range(2)]
        out = _select_snippets(cands, k=5, dedup_ceiling=1.0, mmr_lambda=1.0, source_caps={"posts": 10})
        assert len(out) == 2


@pytest.mark.unit
class TestParseSourceCaps:
    def test_parses_csv(self):
        assert _parse_source_caps(_Cfg(writer_rag_source_caps="posts:2,foo:5")) == {"posts": 2, "foo": 5}

    def test_empty_is_no_caps(self):
        assert _parse_source_caps(_Cfg(writer_rag_source_caps="")) == {}

    def test_malformed_entries_skipped(self):
        assert _parse_source_caps(_Cfg(writer_rag_source_caps="posts:2,junk,bad:x")) == {"posts": 2}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer_caps.py -q`
Expected: FAIL — `_parse_source_caps` undefined + `_select_snippets` has no `source_caps` kwarg.

- [ ] **Step 3: Add `_parse_source_caps` and the cap to `_select_snippets`.** Add the helper near `_select_snippets`:

```python
def _parse_source_caps(site_config: Any = None) -> dict[str, int]:
    """Parse ``writer_rag_source_caps`` (CSV ``source:N``) → {source: cap}.

    Empty/malformed entries are skipped (no cap). Deterministic, DB-tunable.
    """
    if site_config is None:
        return {}
    try:
        csv = (site_config.get("writer_rag_source_caps", "") or "").strip()
    except Exception:  # noqa: BLE001 — defensive against stubbed site_config
        return {}
    caps: dict[str, int] = {}
    for part in csv.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        src, _, num = part.partition(":")
        src = src.strip()
        try:
            caps[src] = int(num.strip())
        except ValueError:
            continue
    return caps
```

Then extend `_select_snippets` to honor caps during MMR selection (replace the signature + selection loop):

```python
def _select_snippets(
    candidates: list[dict],
    *,
    k: int,
    dedup_ceiling: float,
    mmr_lambda: float,
    source_caps: dict[str, int] | None = None,
) -> list[dict]:
    """Pick up to ``k`` diverse grounding snippets, honoring per-source caps.

    ``source_caps`` (e.g. ``{"posts": 2}``) bounds how many snippets any one
    ``source`` may contribute, so the writer's first-party (session) grounding
    isn't crowded out by — or crowding out — a single source. A capped-out
    source becomes ineligible for the rest of the MMR loop.
    """
    source_caps = source_caps or {}
    if not candidates:
        return []
    pool = [c for c in candidates if c.get("relevance", 0.0) < dedup_ceiling]
    if not pool:  # fail-open — the ceiling never zeroes grounding
        pool = list(candidates)

    selected: list[dict] = []
    per_source: dict[str, int] = {}
    remaining = list(pool)
    while remaining and len(selected) < k:
        best_i, best_score = None, None
        for i, cand in enumerate(remaining):
            src = cand.get("source")
            cap = source_caps.get(src)
            if cap is not None and per_source.get(src, 0) >= cap:
                continue  # this source is at its cap — ineligible
            diversity_penalty = (
                max(_cosine(cand.get("vec", []), s.get("vec", [])) for s in selected)
                if selected else 0.0
            )
            score = mmr_lambda * cand.get("relevance", 0.0) - (
                1.0 - mmr_lambda
            ) * diversity_penalty
            if best_score is None or score > best_score:
                best_score, best_i = score, i
        if best_i is None:
            break  # every remaining candidate is capped-out
        chosen = remaining.pop(best_i)
        selected.append(chosen)
        per_source[chosen.get("source")] = per_source.get(chosen.get("source"), 0) + 1
    return selected
```

- [ ] **Step 4: Wire caps + fail-closed read scrub into `_embed_and_fetch_snippets`.** In that function, after resolving the other knobs add `source_caps = _parse_source_caps(site_config)`, pass it to `_select_snippets`, and replace the final `snippets = [...]` comprehension with a fail-closed scrub loop:

```python
    source_caps = _parse_source_caps(site_config)
    # ... (existing candidate fetch unchanged) ...
    selected = _select_snippets(
        candidates,
        k=snippet_limit,
        dedup_ceiling=dedup_ceiling,
        mmr_lambda=mmr_lambda,
        source_caps=source_caps,
    )
    # Read backstop: scrub each snippet before it enters the writer prompt.
    # FAIL CLOSED — a scrub error drops the snippet rather than passing
    # unscrubbed operator text to a public writer.
    from services.rag_scrub import scrub_rag_text
    snippets: list[dict[str, Any]] = []
    for c in selected:
        try:
            clean = scrub_rag_text(c["snippet"] or "")
        except Exception as exc:  # noqa: BLE001 — never ground on unscrubbed text
            logger.warning(
                "[two_pass] snippet scrub failed (%s) — dropping ref %s",
                exc, c.get("ref"),
            )
            continue
        snippets.append({"source": c["source"], "ref": c["ref"], "snippet": clean})
    return {**state, "snippets": snippets, "revision_loops": 0,
            "external_lookups": [], "loop_capped": False}
```

- [ ] **Step 5: Add the default.** In `settings_defaults.py` `DEFAULTS`, next to `writer_rag_source_filter`:

```python
    # Per-source snippet caps for the writer, CSV 'source:N'. Empty = no caps.
    # Set 'posts:2' alongside writer_rag_source_filter='claude_sessions,posts' so
    # prior posts can't crowd out (or re-form the echo loop from) first-party
    # session grounding.
    'writer_rag_source_caps': '',
```

- [ ] **Step 6: Run the new + existing two_pass tests**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_two_pass_writer_caps.py tests/unit/services/atoms/ -k two_pass -q`
Expected: PASS (new cap/parse tests + existing two_pass tests green).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/two_pass_writer.py \
        src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/tests/unit/services/atoms/test_two_pass_writer_caps.py
git commit -m "feat(writer): per-source caps + fail-closed read scrub on grounding snippets"
```

---

## Task 6: Verify + re-enable on prod (operator action — no code)

**Files:** none (runtime configuration + verification).

This task is a runbook, not a commit. Run it only after Tasks 1-5 are merged and the worker image is rebuilt so the scrub is live on the write + read paths.

- [ ] **Step 1: Full backend test sweep** (catch cross-cutting regressions)

Run: `cd src/cofounder_agent && poetry run pytest tests/unit -q`
Expected: green (matches the pre-change nightly baseline).

- [ ] **Step 2: Verify the scrub on a real session sample** (no operator token survives). Reuse the in-container pattern from the calibration work — pull one recent `claude_sessions` `text_preview`, run it through `scrub_rag_text`, and grep the output for the operator name, `C--Users-mattm`, the Tailnet IP, and `glad-labs-stack`. Expected: none present.

- [ ] **Step 3: Re-embed sessions through the new write-path scrub.** The tap uniques on `content_hash`, so bump/re-run the `claude_code_sessions` tap (or wait for its 2h interval) so stored session vectors are operator-scrubbed at rest, not just at read.

- [ ] **Step 4: Flip the writer source + cap on prod** (via the MCP `set_setting` / `poindexter settings set`):

```
writer_rag_source_filter = claude_sessions,posts
writer_rag_source_caps   = posts:2
```

- [ ] **Step 5: Watch the next few drafts.** Confirm: (a) drafts read as session-grounded ("what we did / why"), (b) `opening_originality` advisory flags trend down, (c) QA pass rate holds, and (d) — the risk the token scrub can't cover — spot-check each of the first N drafts for operator _substance_ leak (internal systems, un-shipped work). If substance leaks, tighten `writer_rag_source_caps` or pause by reverting `writer_rag_source_filter` to `''`.

---

## Follow-ups (tracked, not in this plan)

- LLM distillation of sessions into insight nuggets (if the deterministic filter proves too noisy).
- `memory` source and/or niche-conditional grounding (glad-labs niche only).
- Widen the writer to rent `rag_engine` (hybrid + cross-encoder rerank) — the retrieval-path divergence.
- Strategy: narrow niches toward AI/ML + first-party/build-in-public content.
