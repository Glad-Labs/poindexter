# Issue Auto-Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-triage new GitHub issues across both repos by backfilling only the labels their _content_ justifies, and surfacing (never defaulting) the judgment axes for operator approval.

**Architecture:** Three independently-shippable increments. (1) A deterministic conventional-commit prefix → `type` label, shared by a `on: issues.opened` GitHub Action and the weekly sweep. (2) The findings filer stamps `kind`-derived labels on the issues it opens (+ the missing `finding` label gets created). (3) A weekly scheduled Claude routine applies the reasoned `area` label where it can cite a reason and posts `priority`/`milestone` _proposals_ to Discord for one-tap approval. The governing rule everywhere is **cite-or-surface**: set a field only with a content-derived reason; otherwise leave it bare so it stays in the triage queue.

**Tech Stack:** Python 3.13 (stdlib `re`, `asyncio`, `asyncpg`), pytest (asyncio), GitHub Actions (YAML), `gh` CLI, `app_settings` (DB config), the `gladlabs` MCP `discord_post`.

---

## Context & invariants (read before any task)

**The principle (do not violate):** A defaulted label launders "untriaged" into "looks-triaged." A _missing_ label is the forcing function that drags an issue back for a real decision. So: a label may be applied only when the issue's own content justifies it (a CC prefix, a finding `kind`, a cited keyword). No content basis → leave it bare. Never move a triage decision to a point where no content signal exists yet, and never auto-default.

**Axis ownership (why each piece lives where it does):**

- `type` ← conventional-commit title prefix (`feat(`→feature). Deterministic from text the author already wrote → safe in a zero-LLM Action.
- `area` ← reasoned, not regex. A keyword like "dashboard" in a backend issue would mislabel, so area is the _reasoning_ agent's job (Increment 3), applied only when confidently cited.
- `priority` + `milestone` ← genuine roadmap judgment. Always _surfaced_ as a proposal, never auto-applied. Milestones differ per repo (see below), so they are read per-repo.

**Repo facts (verified 2026-06-02):**

- Issues are content-routed at filing time: OSS → `Glad-Labs/poindexter` (public), business/internal → `Glad-Labs/glad-labs-stack` (private). The triager operates _within_ whichever repo an issue already lives in and **never transfers** issues between repos (that's a human/file-time judgment; a suspected mis-file is a surface, not an action).
- Both repos share the identical label taxonomy (P0–P3, `bug`/`feature`/`enhancement`/`improvement`/`chore`/`security`/`tech-debt`/`documentation`/`question`, `backend`/`frontend`/`testing`/`infra`/`monitoring`/`pipeline`/`monetization`). So one label map is valid in both.
- Milestones DIVERGE: poindexter has Phase 0/1/2, Distribution, Backlog; glad-labs-stack has only "Phase 3 — Monetize". Read each repo's `gh api repos/<repo>/milestones` live; never hardcode.
- The sync filter (`scripts/sync-to-github.sh`) strips specific files but **not** `.github/workflows/*`, so a new workflow committed to glad-labs-stack rides the force-push mirror into poindexter and runs in both. `docs/superpowers/` IS stripped, so this plan stays private.

**Latent bug this plan also fixes:** `findings_alert_router._dispatch_github_issue` calls `gh issue create … --label finding` (findings_alert_router.py:236), but the `finding` label does **not exist** in `glad-labs-stack`. `gh` fails the entire create when a label is unknown — so even after #546 authenticates `gh` in the worker, `github_issue` delivery still fails. Increment 2 creates the label and is a hard prerequisite for #546 actually working.

## File structure

| File                                                                              | Responsibility                                                                                                                                                                                   | Increment |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------- |
| `src/cofounder_agent/services/triage/__init__.py`                                 | package marker                                                                                                                                                                                   | 1         |
| `src/cofounder_agent/services/triage/derive_labels.py`                            | pure: title → `type` label (cite-or-None). Self-contained (only `re`) so it runs as a bare script for the Action AND imports as `services.triage.derive_labels` for tests/sweep. CLI: `--title`. | 1         |
| `src/cofounder_agent/tests/unit/services/triage/test_derive_labels.py`            | unit tests for `derive_type`                                                                                                                                                                     | 1         |
| `.github/workflows/triage-on-open.yml`                                            | `on: issues.opened` → run derive CLI → `gh issue edit --add-label`. Runs in both repos.                                                                                                          | 1         |
| `src/cofounder_agent/services/settings_defaults.py:~447`                          | add `findings.<kind>.labels` seeds                                                                                                                                                               | 2         |
| `src/cofounder_agent/services/jobs/findings_alert_router.py:186-255,454,467`      | thread `kind`-labels into `_dispatch_github_issue`                                                                                                                                               | 2         |
| `src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py:~565` | assert labels reach `gh issue create`                                                                                                                                                            | 2         |
| `src/cofounder_agent/services/triage/sweep.py`                                    | pure: `find_gaps(issues)` → per-issue missing-axis report; reuses `derive_type`                                                                                                                  | 3         |
| `src/cofounder_agent/tests/unit/services/triage/test_sweep.py`                    | unit tests for `find_gaps`                                                                                                                                                                       | 3         |
| `scripts/triage/run_weekly_sweep.py`                                              | I/O wrapper: list issues per repo, apply derivable `type`, emit gap JSON                                                                                                                         | 3         |
| `docs/operations/issue-auto-triage.md`                                            | operator doc: cadence, config keys, how to approve                                                                                                                                               | 3         |

---

## Increment 1 — Deterministic `type` from title prefix (Action + shared core)

Independently shippable: after this, every newly-opened issue in either repo gets its `type` label from its CC prefix, with zero LLM cost.

### Task 1: Pure `derive_type` core

**Files:**

- Create: `src/cofounder_agent/services/triage/__init__.py`
- Create: `src/cofounder_agent/services/triage/derive_labels.py`
- Test: `src/cofounder_agent/tests/unit/services/triage/test_derive_labels.py`

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/triage/test_derive_labels.py
"""Unit tests for the cite-or-None type deriver."""
import pytest

from services.triage.derive_labels import derive_type


@pytest.mark.parametrize(
    "title,expected",
    [
        ("feat(content): more length variation", "feature"),
        ("fix(findings): authenticate gh in the worker", "bug"),
        ("harden(findings): don't let fallback suppress critical", "tech-debt"),
        ("refactor: collapse the dispatcher", "tech-debt"),
        ("chore(deps): bump httpx", "chore"),
        ("docs: update the runbook", "documentation"),
        ("test(qa): cover the edge case", "testing"),
        ("feat: scoped-less prefix still works", "feature"),
        ("FEAT(x): case-insensitive", "feature"),
    ],
)
def test_derive_type_from_prefix(title, expected):
    assert derive_type(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "",
        "No conventional prefix here",
        "wip(something): unknown prefix word",
        "Add a thing without a colon",
        "feat without parens or colon",
    ],
)
def test_derive_type_returns_none_when_no_basis(title):
    # cite-or-surface: no derivable basis -> None (leave bare), never a default.
    assert derive_type(title) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/triage/test_derive_labels.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.triage'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cofounder_agent/services/triage/__init__.py
"""Issue auto-triage helpers (cite-or-surface label derivation)."""
```

```python
# src/cofounder_agent/services/triage/derive_labels.py
"""Content-derived issue labels — the 'cite-or-surface' core.

Returns a label ONLY when the issue's own content justifies it (here: the
conventional-commit prefix the author already wrote), and ``None`` otherwise
so the caller leaves the axis bare. A missing label is the triage signal;
this module never invents a default.

Self-contained (stdlib ``re`` only) so it runs three ways from one source:
  * imported as ``services.triage.derive_labels`` by tests + the weekly sweep,
  * run as a bare script by the triage-on-open GitHub Action,
  * ``python -m services.triage.derive_labels --title "..."``.
"""
from __future__ import annotations

import argparse
import re

# Conventional-commit prefix -> type label. Each mapping is content-derived:
# the author chose the prefix, we only translate it.
_PREFIX_TYPE: dict[str, str] = {
    "feat": "feature",
    "fix": "bug",
    "bug": "bug",
    "harden": "tech-debt",
    "refactor": "tech-debt",
    "perf": "tech-debt",
    "chore": "chore",
    "docs": "documentation",
    "test": "testing",
}

# Matches a leading prefix word, an optional (scope), an optional ! and a colon:
#   "feat(content): x"  "fix: y"  "refactor!: z"
_PREFIX_RE = re.compile(r"^\s*([A-Za-z]+)\s*(?:\([^)]*\))?\s*!?:")


def derive_type(title: str | None) -> str | None:
    """Return the ``type`` label implied by a CC title prefix, else ``None``."""
    if not title:
        return None
    m = _PREFIX_RE.match(title)
    if not m:
        return None
    return _PREFIX_TYPE.get(m.group(1).lower())


def _main() -> int:
    ap = argparse.ArgumentParser(description="Derive content-justified issue labels.")
    ap.add_argument("--title", required=True)
    args = ap.parse_args()
    label = derive_type(args.title)
    # Print one label per line (empty output => nothing to apply). The Action
    # reads stdout; no output means "leave it bare", which is correct.
    if label:
        print(label)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/triage/test_derive_labels.py -q`
Expected: PASS (14 cases)

- [ ] **Step 5: Verify the bare-script CLI works**

Run: `python src/cofounder_agent/services/triage/derive_labels.py --title "feat(content): more length variation"`
Expected: prints `feature`
Run: `python src/cofounder_agent/services/triage/derive_labels.py --title "no prefix"`
Expected: prints nothing (exit 0)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/triage/ src/cofounder_agent/tests/unit/services/triage/test_derive_labels.py
git commit -m "feat(triage): content-derived type label core (cite-or-None)"
```

### Task 2: `triage-on-open` GitHub Action

**Files:**

- Create: `.github/workflows/triage-on-open.yml`

> Not unit-testable in-repo — verified by opening a real issue. The workflow rides the sync filter into poindexter and runs in both repos using each repo's default `GITHUB_TOKEN`. Labeling needs only `issues: write`; no App token (the [Bot PR App-token rule] applies to PR-opening workflows that must trigger required checks — this opens no PR).

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/triage-on-open.yml
# Deterministic, zero-LLM triage: stamp the `type` label implied by a new
# issue's conventional-commit title prefix. Cite-or-surface — applies ONLY
# what the prefix justifies and leaves every other axis bare for the weekly
# reasoning sweep. Runs in BOTH repos (rides the sync filter into poindexter).
name: triage-on-open

on:
  issues:
    types: [opened]

# Only the permission needed to add a label. No PR is opened, so no App token.
permissions:
  issues: write

concurrency:
  group: triage-on-open-${{ github.event.issue.number }}
  cancel-in-progress: false

jobs:
  label-type:
    runs-on: ubuntu-latest
    timeout-minutes: 3
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v4.3.1

      - name: Set up Python
        uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v5.6.0
        with:
          python-version: '3.13'

      - name: Derive type label from title
        id: derive
        env:
          ISSUE_TITLE: ${{ github.event.issue.title }}
        run: |
          set -euo pipefail
          LABEL="$(python src/cofounder_agent/services/triage/derive_labels.py --title "$ISSUE_TITLE")"
          echo "label=$LABEL" >> "$GITHUB_OUTPUT"
          if [[ -z "$LABEL" ]]; then
            echo "No CC prefix in title — leaving type bare for the weekly sweep."
          else
            echo "Derived type: $LABEL"
          fi

      - name: Apply label (tolerate already-present / missing label)
        if: steps.derive.outputs.label != ''
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
          NUM: ${{ github.event.issue.number }}
          LABEL: ${{ steps.derive.outputs.label }}
        run: |
          # Skip if the issue already carries a type-ish label decision — never
          # override a human's call. Only add when the type axis is empty.
          EXISTING="$(gh issue view "$NUM" --repo "$REPO" --json labels --jq '[.labels[].name] | join(",")')"
          case ",$EXISTING," in
            *,bug,*|*,feature,*|*,enhancement,*|*,improvement,*|*,chore,*|*,tech-debt,*|*,documentation,*|*,testing,*|*,security,*|*,question,*)
              echo "Type axis already set ($EXISTING) — leaving as-is."; exit 0;;
          esac
          if ! gh issue edit "$NUM" --repo "$REPO" --add-label "$LABEL"; then
            echo "::warning::label '$LABEL' not present in $REPO; skipped (surfaced, not failed)."
          fi
```

- [ ] **Step 2: Validate YAML locally**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/triage-on-open.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/triage-on-open.yml
git commit -m "ci(triage): label issue type from CC prefix on open (both repos)"
```

- [ ] **Step 4: Live verification (after merge to main)**

Open a throwaway issue: `gh issue create --repo Glad-Labs/poindexter --title "test(triage): verify auto-type" --body "delete me"`
Expected within ~1 min: the issue carries the `testing` label. Then close it: `gh issue close <num> --repo Glad-Labs/poindexter --comment "verified"`.

---

## Increment 2 — Findings filer stamps `kind`-derived labels (+ create `finding` label)

Independently shippable and a prerequisite for #546. Findings issues land in glad-labs-stack with content-derived labels instead of bare.

### Task 3: Create the missing `finding` label

**Files:** none (one-time GitHub state change).

- [ ] **Step 1: Create the label in glad-labs-stack**

Run:

```bash
gh label create finding --repo Glad-Labs/glad-labs-stack \
  --description "Auto-filed by findings_alert_router" --color 5319e7 || \
  echo "already exists"
```

Expected: `Label "finding" created` (or `already exists`).

- [ ] **Step 2: Confirm**

Run: `gh label list --repo Glad-Labs/glad-labs-stack --search finding`
Expected: a `finding` row.

### Task 4: Seed `findings.<kind>.labels` policy defaults

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (in the findings block, after line ~447)

- [ ] **Step 1: Add the seeds**

Insert after the `findings.cloud_sync_returned_false.*` block (only the kinds whose delivery/fallback is `github_issue` need labels — `quality_regression` and `missing_seo`):

```python
    # ----- Findings issue labels (content-derived from kind; cite-or-surface) -----
    # Comma-separated labels stamped on the GitHub issue a github_issue-delivery
    # finding opens. Derived from the finding KIND (its content), not a default.
    # Priority/milestone are deliberately omitted — those are the weekly sweep's
    # surfaced judgment axes, never auto-stamped here.
    'findings.quality_regression.labels': 'bug,pipeline',
    'findings.missing_seo.labels': 'bug,pipeline',
```

- [ ] **Step 2: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py
git commit -m "feat(findings): seed kind-derived issue labels for github_issue delivery"
```

### Task 5: Thread `kind`-labels into `_dispatch_github_issue`

**Files:**

- Modify: `src/cofounder_agent/services/jobs/findings_alert_router.py`
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py`

- [ ] **Step 1: Write the failing test** (append after line ~563, matching the existing `_FakeProc`/`fake_exec` style)

```python
async def test_github_issue_passes_kind_labels_to_create(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if "list" in args:
            return _FakeProc(0, stdout=b"[]")  # no dup -> proceed to create
        return _FakeProc(0)

    monkeypatch.setattr(router_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    finding = {"id": 9, "source": "audit_published_quality",
               "details": {"kind": "quality_regression", "title": "qr: z", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(
        finding, "quality_regression", labels=["finding", "bug", "pipeline"]
    )
    assert ok is True
    create = next(c for c in calls if "create" in c)
    # every label is passed as its own --label arg
    for lbl in ("finding", "bug", "pipeline"):
        assert lbl in create
    assert create.count("--label") == 3


async def test_github_issue_defaults_to_finding_label_only(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if "list" in args:
            return _FakeProc(0, stdout=b"[]")
        return _FakeProc(0)

    monkeypatch.setattr(router_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(router_mod.asyncio, "create_subprocess_exec", fake_exec)
    finding = {"id": 10, "source": "x",
               "details": {"kind": "topic_gap", "title": "t", "body": "b"}}
    ok = await router_mod._dispatch_github_issue(finding, "topic_gap", labels=None)
    assert ok is True
    create = next(c for c in calls if "create" in c)
    assert "finding" in create and create.count("--label") == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -k kind_labels -q`
Expected: FAIL — `_dispatch_github_issue() got an unexpected keyword argument 'labels'`

- [ ] **Step 3: Implement** — add the `labels` param + a resolver, and pass labels through.

Replace the signature and the `gh issue create` invocation in `_dispatch_github_issue` (findings_alert_router.py:186, 234-238):

```python
async def _dispatch_github_issue(
    finding: dict[str, Any], kind: str, labels: list[str] | None = None
) -> bool:
```

Replace the create subprocess call (lines 234-238) with:

```python
    # Always include the `finding` marker; add any kind-derived labels.
    # gh fails the whole create on an unknown label, so the labels MUST exist
    # in _FINDINGS_ISSUE_REPO (see Task 3 + the findings.<kind>.labels seeds).
    label_args: list[str] = []
    for lbl in ["finding", *(labels or [])]:
        if lbl and lbl not in (label_args[1::2] if label_args else []):
            label_args += ["--label", lbl]

    create_proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "create", "--repo", _FINDINGS_ISSUE_REPO,
        "--title", title, "--body", body, *label_args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
```

Add a resolver helper near `_load_policies` (after line 97):

```python
def _issue_labels_for(kind: str, policies: dict[str, dict[str, str]]) -> list[str]:
    """Per-kind issue labels from findings.<kind>.labels, split + stripped.

    Returns [] when unset — the caller still stamps the `finding` marker.
    Content-derived (the kind), never a default priority/milestone."""
    raw = (policies.get(kind) or {}).get("labels", "")
    return [p.strip() for p in raw.split(",") if p.strip()]
```

Update the call site in `run()` (line 467) and pass labels:

```python
                elif delivery == "github_issue":
                    labels = _issue_labels_for(kind, policies)
                    if await _dispatch_github_issue(r, kind, labels):
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_findings_alert_router.py -q`
Expected: PASS (existing tests + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/jobs/findings_alert_router.py src/cofounder_agent/tests/unit/services/jobs/test_findings_alert_router.py
git commit -m "feat(findings): stamp kind-derived labels on filed issues"
```

---

## Increment 3 — Weekly reasoning sweep (area applied-if-cited; priority/milestone surfaced)

Independently shippable. Catches the existing backlog + anything the Action missed, and is the only place `priority`/`milestone` are touched — always as a proposal.

### Task 6: Pure `find_gaps` core

**Files:**

- Create: `src/cofounder_agent/services/triage/sweep.py`
- Test: `src/cofounder_agent/tests/unit/services/triage/test_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/services/triage/test_sweep.py
from services.triage.sweep import find_gaps, PRIORITIES, TYPES, AREAS


def _issue(num, title, labels, milestone=None, body=""):
    return {"number": num, "title": title, "body": body,
            "labels": [{"name": n} for n in labels], "milestone": milestone}


def test_find_gaps_reports_each_missing_axis():
    issues = [_issue(1, "feat(x): y", labels=["P3-low"])]  # has priority, missing type+area+milestone
    gaps = find_gaps(issues)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["number"] == 1
    assert g["missing_priority"] is False
    assert g["missing_type"] is True
    assert g["missing_area"] is True
    assert g["missing_milestone"] is True
    # the deterministic derive runs here too, so the sweep can apply it
    assert g["derived_type"] == "feature"


def test_find_gaps_skips_fully_triaged_issues():
    issues = [_issue(2, "fix(x): y", labels=["bug", "P2-medium", "backend"],
                     milestone={"title": "Backlog"})]
    assert find_gaps(issues) == []


def test_find_gaps_derived_type_none_when_no_prefix():
    issues = [_issue(3, "No prefix here", labels=["P3-low", "backend"],
                     milestone={"title": "Backlog"})]
    g = find_gaps(issues)[0]
    assert g["missing_type"] is True
    assert g["derived_type"] is None  # cite-or-surface: nothing to auto-apply
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/triage/test_sweep.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.triage.sweep'`

- [ ] **Step 3: Implement**

```python
# src/cofounder_agent/services/triage/sweep.py
"""Gap analysis for the weekly issue-triage sweep.

Pure over a list of issue dicts (as returned by `gh issue list --json
number,title,labels,milestone,body`). For each issue it reports which of the
four triage axes are missing and pre-computes the one axis the sweep may apply
without judgment: the content-derived `type`. `area` is left to the reasoning
caller (apply-if-cited); `priority`/`milestone` are always surfaced.
"""
from __future__ import annotations

from typing import Any

from services.triage.derive_labels import derive_type

PRIORITIES = ("P0-critical", "P1-high", "P2-medium", "P3-low")
TYPES = ("bug", "feature", "enhancement", "improvement", "chore",
         "security", "tech-debt", "documentation", "question", "testing")
AREAS = ("backend", "frontend", "testing", "infra", "monitoring",
         "pipeline", "monetization")


def _names(issue: dict[str, Any]) -> set[str]:
    return {lbl.get("name") for lbl in (issue.get("labels") or [])}


def find_gaps(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one gap-report per issue that is missing ANY triage axis."""
    out: list[dict[str, Any]] = []
    for issue in issues:
        names = _names(issue)
        missing_priority = not (names & set(PRIORITIES))
        missing_type = not (names & set(TYPES))
        missing_area = not (names & set(AREAS))
        missing_milestone = not issue.get("milestone")
        if not (missing_priority or missing_type or missing_area or missing_milestone):
            continue
        out.append({
            "number": issue.get("number"),
            "title": issue.get("title", ""),
            "body_excerpt": (issue.get("body") or "")[:600],
            "missing_priority": missing_priority,
            "missing_type": missing_type,
            "missing_area": missing_area,
            "missing_milestone": missing_milestone,
            "derived_type": derive_type(issue.get("title", "")) if missing_type else None,
        })
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/triage/test_sweep.py -q`
Expected: PASS (3 cases)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/triage/sweep.py src/cofounder_agent/tests/unit/services/triage/test_sweep.py
git commit -m "feat(triage): per-issue gap analysis for the weekly sweep"
```

### Task 7: Sweep I/O wrapper

**Files:**

- Create: `scripts/triage/run_weekly_sweep.py`

> I/O-bound (shells `gh`, writes `audit_log`). Verified by a `--dry-run` against the live repos, not unit tests. Runs on Matt's PC where the sweep agent runs (DB + `gh` available).

- [ ] **Step 1: Implement**

```python
# scripts/triage/run_weekly_sweep.py
"""Weekly issue-triage sweep I/O wrapper.

For each target repo: list open issues, apply the content-derived `type`
label where the title justifies it (and the issue's type axis is bare),
record each applied change to audit_log, and print a JSON report of the
remaining priority/milestone/area gaps + that repo's live milestone list for
the reasoning agent to propose over. NEVER applies priority or milestone.

Usage:
  python scripts/triage/run_weekly_sweep.py --dry-run
  python scripts/triage/run_weekly_sweep.py            # applies derivable type
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

# Make `services.*` importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "cofounder_agent"))

from services.triage.sweep import find_gaps  # noqa: E402

REPOS = ["Glad-Labs/poindexter", "Glad-Labs/glad-labs-stack"]


def _gh_json(repo: str, *args: str) -> list[dict]:
    out = subprocess.run(
        ["gh", *args, "--repo", repo], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out or "[]")


def _milestones(repo: str) -> list[str]:
    data = subprocess.run(
        ["gh", "api", f"repos/{repo}/milestones", "--jq", "[.[].title]"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(data or "[]")


async def _record(applied: list[dict]) -> None:
    """Best-effort audit_log row per applied label (event_type='issue_triaged')."""
    if not applied:
        return
    try:
        import asyncpg  # local import: only needed when actually writing
        from brain.bootstrap import resolve_database_url
    except Exception as exc:  # pragma: no cover
        print(f"[sweep] audit_log skipped (deps unavailable): {exc}", file=sys.stderr)
        return
    dsn = resolve_database_url()
    conn = await asyncpg.connect(dsn)
    try:
        for a in applied:
            await conn.execute(
                """INSERT INTO audit_log (event_type, source, severity, details)
                   VALUES ('issue_triaged', 'weekly_sweep', 'info', $1::jsonb)""",
                json.dumps(a),
            )
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    report: dict[str, dict] = {}
    applied: list[dict] = []

    for repo in REPOS:
        issues = _gh_json(repo, "issue", "list", "--state", "open", "--limit", "300",
                          "--json", "number,title,labels,milestone,body")
        gaps = find_gaps(issues)
        for g in gaps:
            if g["missing_type"] and g["derived_type"]:
                reason = f"type from title prefix -> {g['derived_type']}"
                if args.dry_run:
                    print(f"[dry-run] {repo}#{g['number']}: +{g['derived_type']} ({reason})")
                else:
                    subprocess.run(
                        ["gh", "issue", "edit", str(g["number"]), "--repo", repo,
                         "--add-label", g["derived_type"]], check=False,
                    )
                applied.append({"repo": repo, "number": g["number"],
                                "label": g["derived_type"], "axis": "type",
                                "reason": reason})
        report[repo] = {"milestones": _milestones(repo), "gaps": gaps}

    if not args.dry_run:
        asyncio.run(_record(applied))

    # The reasoning agent consumes this JSON to propose area/priority/milestone.
    print(json.dumps({"applied": applied, "report": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Dry-run verification**

Run: `python scripts/triage/run_weekly_sweep.py --dry-run`
Expected: prints `[dry-run]` lines for any bare-type issues with a CC prefix, then a JSON blob containing each repo's `milestones` + `gaps`. No labels changed.

- [ ] **Step 3: Commit**

```bash
git add scripts/triage/run_weekly_sweep.py
git commit -m "feat(triage): weekly sweep wrapper — apply derivable type, surface the rest"
```

### Task 8: Scheduled routine + operator doc

**Files:**

- Create: `docs/operations/issue-auto-triage.md`

The reasoning + surfacing is a scheduled Claude routine (same mechanism as the existing "Code quality agent every 4h" cron). It is registered via the scheduling surface, not committed code; this task documents it and its exact prompt.

- [ ] **Step 1: Write the operator doc**

```markdown
# Issue Auto-Triage

Three layers, one rule (**cite-or-surface**): a label is applied only when the
issue's content justifies it; otherwise the axis stays bare and is surfaced.

## Layers

1. `triage-on-open.yml` — on issue open, stamps `type` from the CC prefix. Both repos.
2. `findings_alert_router` — stamps `finding` + `findings.<kind>.labels` on filed findings.
3. Weekly sweep (this doc) — applies derivable `type` across the backlog, applies
   `area` only when it can cite a reason, and posts `priority`/`milestone`
   PROPOSALS to Discord for one-tap approval. Never auto-applies priority/milestone.

## Config (app_settings)

- `findings.<kind>.labels` — comma-separated labels for filed findings (e.g. `bug,pipeline`).
- `triage.sweep.enabled` (default `true`), `triage.sweep.surface_channel` (default `discord`).

## Weekly routine prompt (register on the scheduling surface, Mondays 14:00 UTC)

> Run `python scripts/triage/run_weekly_sweep.py`. It has already applied the
> content-derived `type` labels and printed a JSON report. For each issue in
> `report[*].gaps`: if `missing_area`, decide the single best `area` ONLY if the
> body clearly cites one subsystem — apply it with `gh issue edit --add-label`
> and state the cited reason; if it's cross-cutting, leave it bare. For
> `missing_priority`/`missing_milestone`, do NOT apply — instead compose a
> proposal with a one-line rationale per issue (priority from blocking/impact
> signals in the body; milestone chosen from that repo's `report[repo].milestones`
> list). Post one Discord digest via the gladlabs `discord_post` tool titled
> "🔖 Weekly triage — N proposals" listing each issue, the proposed
> priority+milestone, and the rationale. Do not invent a value you cannot cite;
> a bare axis is the correct output when there is no basis.

## Approving proposals

Reply in the Discord thread (or tell the next run) which proposals to apply. The
agent applies approved priority/milestone via `gh issue edit`. Unapproved issues
stay bare — that's the queue, working as intended.
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/issue-auto-triage.md
git commit -m "docs(triage): operator runbook for the auto-triage layers"
```

- [ ] **Step 3: Register the schedule** (manual, operator action)

Use the scheduling surface (`/schedule` or the scheduled-tasks MCP) to register the routine in Step 1 of Task 8, weekly Mondays 14:00 UTC. Add a line to CLAUDE.md's "Cron Jobs" section: `Issue triage sweep: weekly Mon 14:00 UTC — apply derivable type, surface priority/milestone`.

---

## Self-review

- **Spec coverage:** hybrid shape ✓ (Increment 1 Action = coded deterministic; Increment 3 = reasoning agent). Apply-derivable/surface-judgment ✓ (`type` auto-applied by Action+sweep; `area` applied-if-cited by agent; `priority`/`milestone` surfaced only). Both repos ✓ (Action rides sync; sweep iterates `REPOS`). Never-default ✓ (`derive_type`/`find_gaps`/`_issue_labels_for` all return None/[] with no basis). Never-transfer ✓ (stated invariant; no transfer code). Latent `finding`-label bug ✓ (Task 3).
- **Per-repo milestones:** read live via `_milestones(repo)` ✓; never hardcoded.
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `derive_type` signature/return (`str | None`) consistent across derive_labels, sweep, run_weekly_sweep; `_dispatch_github_issue(finding, kind, labels=None)` consistent between Task 5 impl, call site, and tests.

## Sequencing & risk

Ship in order 1 → 2 → 3; each is independently valuable. Lowest blast radius first (Increment 1 is additive CI). Increment 2 touches load-bearing `findings_alert_router` but is guarded by existing + new unit tests and the `finding`-label prerequisite. Increment 3 only ever _adds_ labels and _proposes_ the judgment axes.
