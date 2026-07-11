# Scheduled-Agent Fleet Rewire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the 7 keepable Windows-scheduled "Claude Session" tasks off the decayed Max-OAuth path — 5 onto deterministic scripts (no model), 2 onto the local Ollama fleet — and leave the 2 frontier-model sessions defined-but-disabled.

**Architecture:** A new standalone `scripts/ops_sessions/` Python package (one module per session + shared `_common.py`) carries the per-session logic. The proven `scripts/claude-sessions.ps1` harness (worktree isolation, `node_modules` junction, timeout, logging) is refactored to dispatch a per-session `Command` instead of hardcoding `claude.exe`, gated by new `NeedsWorktree` / `Enabled` fields. Local judgment (alert-triage, test-health) is one structured Ollama HTTP call per unit of work; the deterministic five make zero model calls.

**Tech Stack:** Python 3.13 (`asyncpg`, `httpx`, stdlib `tomllib`/`ast`/`re`), `gh` + `git` CLIs, PowerShell 5.1 (Task Scheduler harness), pytest.

## Global Constraints

- **Python `>=3.13,<3.14`.** `str | None` unions, `tomllib`, `ast.get_docstring` all available.
- **No new pip dependencies.** Use only `asyncpg` (^0.31), `httpx` (^0.28), and stdlib already in `src/cofounder_agent/pyproject.toml`.
- **`brain` is an installed package** in the poetry env — `from brain.bootstrap import resolve_database_url` and `from brain.operator_notifier import notify_operator` work under `poetry run python` from any CWD.
- **Invocation contract:** every ops script is run as a file — `poetry run python <runDir>/scripts/ops_sessions/<name>.py` — so `sys.path[0]` is the script's own dir and `import _common` (bare, sibling) resolves. `import brain.*` resolves via the installed package. `<runDir>` is the session's worktree when `NeedsWorktree=$true`, else `$WorkDir` (the shared checkout).
- **Working-directory rule (CWD-independent):** scripts must NOT rely on their own CWD. Every ops script computes `ROOT = _repo_root()` (parent-walk to the dir containing `CLAUDE.md`) and, where needed, `PKG = ROOT / "src" / "cofounder_agent"`. Every subprocess call passes an **explicit `cwd`**: `git` / `ruff` / `bandit` (repo-wide) → `str(ROOT)`; `pytest` and `poetry run python <script>` (the poetry project lives there) → `str(PKG)`. `gh` needs no `cwd` (it always passes `--repo`).
- **Tests** live in `src/cofounder_agent/tests/unit/scripts/`, add the ops dir to `sys.path`, then `import <module>`. Run from `src/cofounder_agent/` via `poetry run pytest`.
- **Exit contract:** exit `0` on success including the legitimate "nothing to do". A hard failure (missing DB URL, Ollama down, subprocess error) calls `notify_operator(...)` then exits non-zero. **No silent defaults** (`feedback_no_silent_defaults`).
- **PowerShell 5.1:** no `&&` — use `;`. The harness file MUST stay UTF-8 (BOM preferred) and ASCII-only in `param()`/definitions (see the file's own `.NOTES`).
- **Commits:** conventional-commit style, one deliverable per commit, `git commit --no-verify` (the worktree has no `node_modules`, so the husky/prettier pre-commit hook cannot resolve its tools), end every message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Repo routing:** all code + PRs go to `Glad-Labs/glad-labs-stack` only (poindexter is a force-rebuilt mirror). Security issues (bandit) stay in the private `glad-labs-stack`.
- **Frontier two** (`issue-resolver`, `test-expansion`) keep their definitions in `claude-sessions.ps1` but set `Enabled=$false` — never registered.

## File Structure

| Path                                                   | Responsibility                                                                        |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| `scripts/ops_sessions/_common.py`                      | DB query, Ollama call, `gh`/`git` wrappers, notify wrapper, logger, config resolution |
| `scripts/ops_sessions/dependency_review.py`            | Auto-merge green patch-bump dependabot PRs                                            |
| `scripts/ops_sessions/codebase_audit.py`               | ruff `--fix` F401/F841 + bandit → security issue                                      |
| `scripts/ops_sessions/doc_sync.py`                     | Verify + repair CLAUDE.md path references                                             |
| `scripts/ops_sessions/claude_md_sync.py`               | Run DB-stats script + refresh migration line                                          |
| `scripts/ops_sessions/triage_sweep.py`                 | Run weekly sweep + keyword area-labels + Discord digest                               |
| `scripts/ops_sessions/alert_triage.py`                 | Classify noisy alerts (Ollama) → file probe-bug issues                                |
| `scripts/ops_sessions/test_health.py`                  | Run pytest + local-model fix behind a re-run gate                                     |
| `scripts/claude-sessions.ps1`                          | _(modify)_ harness dispatches `Command`; `NeedsWorktree`/`Enabled` fields             |
| `src/cofounder_agent/tests/unit/scripts/test_ops_*.py` | Contract tests for the pure functions                                                 |
| `docs/operations/scheduled-agents.md`                  | _(create)_ operator runbook                                                           |
| `CLAUDE.md`                                            | _(modify)_ replace "all DISABLED" scheduled-agents section                            |

---

### Task 1: `_common.py` shared helpers

**Files:**

- Create: `scripts/ops_sessions/_common.py`
- Create: `scripts/ops_sessions/__init__.py` (empty — makes the dir a clean package for `-m`/test imports)
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_common.py`

**Interfaces:**

- Produces:
  - `bootstrap_value(key: str) -> str` — read a key from `~/.poindexter/bootstrap.toml` (`""` if absent).
  - `db_url() -> str` — `brain.bootstrap.resolve_database_url()`, or notify+`SystemExit(2)` on None.
  - `async fetch_all(sql: str, *args) -> list[asyncpg.Record]` — connect via `db_url()`, run, close.
  - `ollama_chat(user: str, *, model: str, system: str | None = None, as_json: bool = False, timeout: float = 120.0) -> str` — POST `<OPS_OLLAMA_URL>/api/chat`, `stream=false`; returns `message.content`; raises `OllamaUnavailable` on connect error.
  - `parse_ollama_content(payload: dict) -> str` — pure extractor of `payload["message"]["content"]`.
  - `run(cmd: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess` — subprocess wrapper, `text=True`, logs argv + rc.
  - `gh(*args: str) -> subprocess.CompletedProcess` / `git(*args: str) -> subprocess.CompletedProcess`.
  - `notify_fail(title: str, detail: str, source: str) -> None` — wraps `brain.operator_notifier.notify_operator(..., severity="warning")`.
  - `get_logger(name: str) -> logging.Logger` — file handler at `~/.poindexter/logs/claude-sessions/<name>-<ts>.log`.
  - `class OllamaUnavailable(RuntimeError)`.
  - Constants: `OPS_OLLAMA_URL = os.environ.get("OPS_OLLAMA_URL", "http://localhost:11434")`, `MODEL_TRIAGE = os.environ.get("OPS_OLLAMA_MODEL_TRIAGE", "llama3.2:3b")`, `MODEL_TESTFIX = os.environ.get("OPS_OLLAMA_MODEL_TESTFIX", "qwen2.5-coder:7b")`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_common.py
"""Contract tests for scripts/ops_sessions/_common.py pure helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import _common  # noqa: E402


def test_parse_ollama_content_extracts_message():
    payload = {"message": {"role": "assistant", "content": "hello"}, "done": True}
    assert _common.parse_ollama_content(payload) == "hello"


def test_parse_ollama_content_missing_raises():
    with pytest.raises(KeyError):
        _common.parse_ollama_content({"done": True})


def test_ollama_unavailable_is_runtimeerror():
    assert issubclass(_common.OllamaUnavailable, RuntimeError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_common.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_common'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ops_sessions/_common.py
"""Shared helpers for the deterministic + local-LLM ops sessions.

Standalone by design: needs only Python + asyncpg + httpx + the installed
``brain`` package (for DB-URL resolution and operator notification). No
FastAPI app / SiteConfig / DI bootstrap — these run as short cron scripts.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import subprocess
import tomllib
from pathlib import Path

import asyncpg
import httpx

OPS_OLLAMA_URL = os.environ.get("OPS_OLLAMA_URL", "http://localhost:11434")
MODEL_TRIAGE = os.environ.get("OPS_OLLAMA_MODEL_TRIAGE", "llama3.2:3b")
MODEL_TESTFIX = os.environ.get("OPS_OLLAMA_MODEL_TESTFIX", "qwen2.5-coder:7b")

_LOG_DIR = Path.home() / ".poindexter" / "logs" / "claude-sessions"


class OllamaUnavailable(RuntimeError):
    """Raised when the local Ollama endpoint cannot be reached."""


def bootstrap_value(key: str) -> str:
    path = Path.home() / ".poindexter" / "bootstrap.toml"
    if not path.exists():
        return ""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(data.get(key, "") or "")


def db_url() -> str:
    from brain.bootstrap import resolve_database_url

    url = resolve_database_url()
    if not url:
        notify_fail(
            "Ops session cannot start",
            "No database URL resolved (bootstrap.toml / DATABASE_URL).",
            "ops_sessions",
        )
        raise SystemExit(2)
    return url


async def fetch_all(sql: str, *args) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(db_url())
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def parse_ollama_content(payload: dict) -> str:
    return payload["message"]["content"]


def ollama_chat(
    user: str,
    *,
    model: str,
    system: str | None = None,
    as_json: bool = False,
    timeout: float = 120.0,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    body: dict = {"model": model, "messages": messages, "stream": False}
    if as_json:
        body["format"] = "json"
    try:
        resp = httpx.post(f"{OPS_OLLAMA_URL}/api/chat", json=body, timeout=timeout)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise OllamaUnavailable(f"{OPS_OLLAMA_URL}: {exc}") from exc
    return parse_ollama_content(resp.json())


def run(cmd: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    logging.getLogger("ops").info("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        logging.getLogger("ops").warning("rc=%s stderr=%s", proc.returncode, proc.stderr[:500])
    return proc


def gh(*args: str) -> subprocess.CompletedProcess:
    return run(["gh", *args])


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd)


def notify_fail(title: str, detail: str, source: str) -> None:
    try:
        from brain.operator_notifier import notify_operator

        notify_operator(title, detail, source=source, severity="warning")
    except Exception:  # noqa: BLE001 — notification must never mask the real error
        logging.getLogger("ops").warning("notify_operator failed: %s | %s", title, detail)


def get_logger(name: str) -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    handler = logging.FileHandler(_LOG_DIR / f"{name}-{ts}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def asyncio_run(coro):
    return asyncio.run(coro)
```

Also create the empty package marker:

```python
# scripts/ops_sessions/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_common.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/_common.py scripts/ops_sessions/__init__.py src/cofounder_agent/tests/unit/scripts/test_ops_common.py
git commit --no-verify -m "feat(ops): shared helpers for ops-session scripts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `dependency_review.py` (deterministic, no worktree)

**Files:**

- Create: `scripts/ops_sessions/dependency_review.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_dependency_review.py`

**Interfaces:**

- Consumes: `_common.gh`, `_common.get_logger`.
- Produces: `is_patch_bump(title: str) -> bool`; `all_checks_green(rollup: list[dict]) -> bool`; `older_than_hours(created_at_iso: str, hours: int, *, now: datetime | None = None) -> bool`; `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_dependency_review.py
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import dependency_review as dr  # noqa: E402


def test_patch_bump_true():
    assert dr.is_patch_bump("Bump lodash from 4.17.20 to 4.17.21") is True
    assert dr.is_patch_bump("chore(deps): bump urllib3 from 2.1.0 to 2.1.2") is True


def test_minor_and_major_bumps_false():
    assert dr.is_patch_bump("Bump react from 18.2.0 to 18.3.0") is False
    assert dr.is_patch_bump("Bump next from 15.0.0 to 16.0.0") is False


def test_non_version_title_false():
    assert dr.is_patch_bump("Update the CI workflow") is False


def test_checks_green():
    assert dr.all_checks_green([{"state": "SUCCESS"}, {"conclusion": "SUCCESS"}]) is True
    assert dr.all_checks_green([{"state": "SUCCESS"}, {"conclusion": "FAILURE"}]) is False
    assert dr.all_checks_green([]) is False


def test_older_than_hours():
    now = dt.datetime(2026, 7, 9, 12, 0, tzinfo=dt.timezone.utc)
    old = "2026-07-09T05:00:00Z"
    fresh = "2026-07-09T11:30:00Z"
    assert dr.older_than_hours(old, 6, now=now) is True
    assert dr.older_than_hours(fresh, 6, now=now) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_dependency_review.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dependency_review'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ops_sessions/dependency_review.py
"""Auto-merge green patch-bump dependabot PRs. Deterministic, no model."""
from __future__ import annotations

import datetime as dt
import json
import re
import sys

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
_VER = re.compile(r"from\s+v?(\d+)\.(\d+)\.(\d+)\S*\s+to\s+v?(\d+)\.(\d+)\.(\d+)\S*", re.I)


def is_patch_bump(title: str) -> bool:
    m = _VER.search(title)
    if not m:
        return False
    f_maj, f_min, f_pat, t_maj, t_min, t_pat = (int(x) for x in m.groups())
    return f_maj == t_maj and f_min == t_min and t_pat != f_pat


def all_checks_green(rollup: list[dict]) -> bool:
    if not rollup:
        return False
    for ctx in rollup:
        outcome = ctx.get("conclusion") or ctx.get("state") or ""
        if outcome.upper() not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            return False
    return True


def older_than_hours(created_at_iso: str, hours: int, *, now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    created = dt.datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
    return (now - created) >= dt.timedelta(hours=hours)


def main() -> int:
    log = c.get_logger("dependency-review")
    proc = c.gh(
        "pr", "list", "--repo", REPO,
        "--search", "is:pr is:open author:app/dependabot",
        "--json", "number,title,createdAt,statusCheckRollup", "--limit", "30",
    )
    if proc.returncode != 0:
        c.notify_fail("dependency-review failed", proc.stderr[:500], "dependency_review")
        return 1
    prs = json.loads(proc.stdout or "[]")
    merged, skipped = [], []
    for pr in prs:
        num = pr["number"]
        if not (is_patch_bump(pr["title"]) and all_checks_green(pr.get("statusCheckRollup", []))
                and older_than_hours(pr["createdAt"], 6)):
            skipped.append(num)
            continue
        c.gh("pr", "review", "--repo", REPO, str(num), "--approve")
        c.gh("pr", "merge", "--repo", REPO, str(num), "--squash", "--delete-branch", "--auto")
        merged.append(num)
    log.info("merged=%s skipped=%s", merged, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_dependency_review.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/dependency_review.py src/cofounder_agent/tests/unit/scripts/test_ops_dependency_review.py
git commit --no-verify -m "feat(ops): deterministic dependency-review auto-merge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `codebase_audit.py` (deterministic, worktree)

**Files:**

- Create: `scripts/ops_sessions/codebase_audit.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_codebase_audit.py`

**Interfaces:**

- Consumes: `_common.run`, `_common.gh`, `_common.git`, `_common.get_logger`.
- Produces: `bandit_issue_body(finding: dict) -> tuple[str, str]` (returns `(title, body)`); `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_codebase_audit.py
from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import codebase_audit as ca  # noqa: E402


def test_bandit_issue_body_templating():
    finding = {
        "filename": "brain/foo.py",
        "line_number": 42,
        "test_id": "B605",
        "issue_severity": "HIGH",
        "issue_text": "Starting a process with a shell",
        "code": "os.system(cmd)",
    }
    title, body = ca.bandit_issue_body(finding)
    assert "B605" in title
    assert "brain/foo.py:42" in body
    assert "HIGH" in body
    assert "os.system(cmd)" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_codebase_audit.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

````python
# scripts/ops_sessions/codebase_audit.py
"""ruff --fix (F401/F841) + bandit → security issue. Deterministic, worktree."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
RUFF_TARGETS = ["src/", "brain/", "scripts/"]
BANDIT_TARGETS = ["brain/", "scripts/", "src/cofounder_agent/services/", "src/cofounder_agent/routes/"]


def bandit_issue_body(finding: dict) -> tuple[str, str]:
    loc = f"{finding['filename']}:{finding['line_number']}"
    title = f"security(bandit): {finding['test_id']} in {finding['filename']}"
    body = (
        f"**Severity:** {finding['issue_severity']}\n"
        f"**Rule:** {finding['test_id']}\n"
        f"**Location:** `{loc}`\n\n"
        f"{finding['issue_text']}\n\n"
        f"```python\n{finding.get('code', '').strip()}\n```\n\n"
        f"_Filed by the deterministic codebase-audit ops session._"
    )
    return title, body


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("codebase-audit")
    root = str(_repo_root())
    c.run(["ruff", "check", "--fix", "--select", "F401,F841", *RUFF_TARGETS], cwd=root)
    bandit = c.run(["bandit", "-r", *BANDIT_TARGETS, "-q", "-ll", "-f", "json"], cwd=root)
    findings = json.loads(bandit.stdout or "{}").get("results", []) if bandit.stdout else []
    for f in findings:
        title, body = bandit_issue_body(f)
        c.gh("issue", "create", "--repo", REPO, "--label", "security", "--title", title, "--body", body)
    status = c.git("status", "--porcelain", cwd=root)
    if status.stdout.strip():
        c.git("add", "-A", cwd=root)
        c.git("commit", "--no-verify", "-m", "fix(lint): ruff --fix F401/F841 (ops codebase-audit)", cwd=root)
        c.git("push", "-u", "origin", "HEAD", cwd=root)
        c.gh("pr", "create", "--repo", REPO, "--base", "main",
             "--title", "fix(lint): ruff F401/F841 sweep (ops)",
             "--body", "Automated unused-import/variable fixes.")
        log.info("opened lint PR; bandit findings filed=%d", len(findings))
    else:
        log.info("no ruff fixes; bandit findings filed=%d", len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
````

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_codebase_audit.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/codebase_audit.py src/cofounder_agent/tests/unit/scripts/test_ops_codebase_audit.py
git commit --no-verify -m "feat(ops): deterministic codebase-audit (ruff+bandit)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `doc_sync.py` (deterministic, worktree)

**Files:**

- Create: `scripts/ops_sessions/doc_sync.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_doc_sync.py`

**Interfaces:**

- Consumes: `_common.git`, `_common.gh`, `_common.get_logger`.
- Produces: `extract_refs(md: str) -> list[str]`; `resolve_ref(ref: str, repo_root: Path) -> tuple[str, str | None]` (status ∈ `{"ok","fix","flag"}`); `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_doc_sync.py
from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import doc_sync as ds  # noqa: E402


def test_extract_refs_finds_paths_and_dedups():
    md = "See `src/cofounder_agent/main.py` and docs/operations/foo.md, plus src/cofounder_agent/main.py again."
    refs = ds.extract_refs(md)
    assert "src/cofounder_agent/main.py" in refs
    assert "docs/operations/foo.md" in refs
    assert refs.count("src/cofounder_agent/main.py") == 1


def test_extract_refs_strips_trailing_punctuation():
    assert "scripts/foo.py" in ds.extract_refs("run scripts/foo.py.")


def test_resolve_ref_ok_fix_flag(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "here.py").write_text("x")
    (tmp_path / "moved").mkdir()
    (tmp_path / "moved" / "unique.py").write_text("x")
    assert ds.resolve_ref("src/here.py", tmp_path) == ("ok", None)
    assert ds.resolve_ref("src/unique.py", tmp_path) == ("fix", "moved/unique.py")
    assert ds.resolve_ref("src/nope.py", tmp_path) == ("flag", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_doc_sync.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ops_sessions/doc_sync.py
"""Verify + repair CLAUDE.md path references. Deterministic, worktree."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
_REF = re.compile(r"(?:src|docs|infrastructure|scripts|brain)/[A-Za-z0-9_./-]+")


def extract_refs(md: str) -> list[str]:
    seen: list[str] = []
    for m in _REF.finditer(md):
        ref = m.group(0).rstrip(".,;:`)")
        if ref not in seen:
            seen.append(ref)
    return seen


def resolve_ref(ref: str, repo_root: Path) -> tuple[str, str | None]:
    if (repo_root / ref).exists():
        return "ok", None
    matches = [p for p in repo_root.rglob(Path(ref).name) if ".git" not in p.parts]
    if len(matches) == 1:
        return "fix", matches[0].relative_to(repo_root).as_posix()
    return "flag", None


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("doc-sync")
    root = _repo_root()
    claude_md = root / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8")
    changed = False
    flags: list[str] = []
    for ref in extract_refs(text):
        status, fix = resolve_ref(ref, root)
        if status == "fix" and fix:
            text = text.replace(ref, fix)
            changed = True
            log.info("fixed %s -> %s", ref, fix)
        elif status == "flag":
            flags.append(ref)
    if changed:
        claude_md.write_text(text, encoding="utf-8")
        roots = str(root)
        c.git("add", "CLAUDE.md", cwd=roots)
        c.git("commit", "--no-verify", "-m", "docs(CLAUDE.md): repair moved path references (ops doc-sync)", cwd=roots)
        c.git("push", "-u", "origin", "HEAD", cwd=roots)
        c.gh("pr", "create", "--repo", REPO, "--base", "main",
             "--title", "docs(CLAUDE.md): repair path references (ops)",
             "--body", f"Auto-corrected moved refs. Unresolved (need human): {flags or 'none'}")
    log.info("changed=%s flags=%s", changed, flags)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_doc_sync.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/doc_sync.py src/cofounder_agent/tests/unit/scripts/test_ops_doc_sync.py
git commit --no-verify -m "feat(ops): deterministic doc-sync path-reference repair

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `claude_md_sync.py` (deterministic, worktree)

**Files:**

- Create: `scripts/ops_sessions/claude_md_sync.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_claude_md_sync.py`

**Interfaces:**

- Consumes: `_common.run`, `_common.git`, `_common.gh`, `_common.get_logger`.
- Produces: `extract_migration_clause(source: str) -> str` (docstring first line, or filename fallback); `newest_migration(migrations_dir: Path) -> Path | None`; `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_claude_md_sync.py
from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import claude_md_sync as cms  # noqa: E402


def test_extract_migration_clause_reads_docstring():
    src = '"""Drop pipeline_tasks.category column.\n\nMore detail.\n"""\n\ndef up(): ...\n'
    assert cms.extract_migration_clause(src) == "Drop pipeline_tasks.category column."


def test_extract_migration_clause_no_docstring_returns_empty():
    assert cms.extract_migration_clause("def up(): ...\n") == ""


def test_newest_migration_picks_latest_timestamp(tmp_path):
    (tmp_path / "20260601_010101_a.py").write_text("x")
    (tmp_path / "20260622_200222_b.py").write_text("x")
    (tmp_path / "0000_baseline.py").write_text("x")
    assert cms.newest_migration(tmp_path).name == "20260622_200222_b.py"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_claude_md_sync.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ops_sessions/claude_md_sync.py
"""Refresh CLAUDE.md DB counts + migration line. Deterministic, worktree."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"


def extract_migration_clause(source: str) -> str:
    try:
        doc = ast.get_docstring(ast.parse(source))
    except SyntaxError:
        return ""
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def newest_migration(migrations_dir: Path) -> Path | None:
    stamped = sorted(
        p for p in migrations_dir.glob("20*.py") if p.name[:8].isdigit()
    )
    return stamped[-1] if stamped else None


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("claude-md-sync")
    root = _repo_root()
    roots = str(root)
    pkg = str(root / "src" / "cofounder_agent")
    # Step 1: DB counts via the existing script (self-edits CLAUDE.md in place).
    stats = root / "scripts" / "sync_claude_md_db_stats.py"
    if stats.exists():
        c.run(["poetry", "run", "python", str(stats)], cwd=pkg)
    # Step 2: migration-drift CHECK (surface, do not auto-rewrite prose).
    drift_note = ""
    migrations = root / "src" / "cofounder_agent" / "services" / "migrations"
    newest = newest_migration(migrations)
    if newest:
        clause = extract_migration_clause(newest.read_text(encoding="utf-8"))
        referenced = newest.name in (root / "CLAUDE.md").read_text(encoding="utf-8")
        if not referenced:
            drift_note = f"⚠️ CLAUDE.md does not reference newest migration `{newest.name}` — {clause}"
            log.info(drift_note)
    # Step 3: PR only if the DB-count script actually changed CLAUDE.md.
    status = c.git("status", "--porcelain", cwd=roots)
    if status.stdout.strip():
        c.git("add", "CLAUDE.md", cwd=roots)
        c.git("commit", "--no-verify", "-m", "docs(CLAUDE.md): sync DB-derived counts (ops)", cwd=roots)
        c.git("push", "-u", "origin", "HEAD", cwd=roots)
        c.gh("pr", "create", "--repo", REPO, "--base", "main",
             "--title", "docs(CLAUDE.md): sync DB-derived counts (ops)",
             "--body", f"Automated DB-count refresh.\n\n{drift_note}".strip())
        log.info("opened CLAUDE.md sync PR")
    elif drift_note:
        c.notify_fail("CLAUDE.md migration drift", drift_note, "claude_md_sync")
    else:
        log.info("no CLAUDE.md changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> **Design note (calculated-vs-generated):** the migration _narrative_ is prose, not a
> deterministic value, so this script does NOT auto-rewrite it — it runs the
> deterministic DB-count sync and _surfaces_ drift (CLAUDE.md missing the newest
> migration filename) via the PR body or a Discord note for a human/LLM to word.
> `extract_migration_clause` + `newest_migration` stay because the drift check uses
> them. Repo file-stat counts are owned by the `sync-claude-md.yml` Action — never
> recomputed here.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_claude_md_sync.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/claude_md_sync.py src/cofounder_agent/tests/unit/scripts/test_ops_claude_md_sync.py
git commit --no-verify -m "feat(ops): deterministic claude-md-sync

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `triage_sweep.py` (deterministic, no worktree)

**Files:**

- Create: `scripts/ops_sessions/triage_sweep.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_triage_sweep.py`

**Interfaces:**

- Consumes: `_common.run`, `_common.gh`, `_common.get_logger`, `_common.bootstrap_value`.
- Produces: `AREA_KEYWORDS: dict[str, tuple[str, ...]]`; `pick_area_label(body: str) -> str | None` (single area or None if zero/multiple); `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_triage_sweep.py
from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import triage_sweep as ts  # noqa: E402


def test_single_area_match():
    assert ts.pick_area_label("The Grafana dashboard panel is broken") == "monitoring"


def test_cross_cutting_returns_none():
    # mentions both frontend and backend -> ambiguous -> bare
    assert ts.pick_area_label("The Next.js page calls the FastAPI backend route") is None


def test_no_signal_returns_none():
    assert ts.pick_area_label("Please improve this") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_triage_sweep.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ops_sessions/triage_sweep.py
"""Weekly triage: run sweep script + keyword area-labels + Discord digest."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

import _common as c

AREA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": ("fastapi", "asyncpg", "worker", "service layer", "endpoint"),
    "frontend": ("next.js", "nextjs", "react", "public-site", "vercel"),
    "testing": ("pytest", "unit test", "coverage", "flaky test"),
    "infra": ("docker", "compose", "container", "deploy"),
    "monitoring": ("grafana", "prometheus", "dashboard", "panel", "loki", "alert"),
    "pipeline": ("canonical_blog", "graph_def", "atom", "qa rail", "template_runner"),
    "monetization": ("adsense", "affiliate", "revenue", "stripe", "lemon squeezy"),
}


def pick_area_label(body: str) -> str | None:
    low = body.lower()
    hits = [area for area, kws in AREA_KEYWORDS.items() if any(k in low for k in kws)]
    return hits[0] if len(hits) == 1 else None


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("triage-sweep")
    root = _repo_root()
    sweep = c.run(
        ["poetry", "run", "python", str(root / "scripts" / "triage" / "run_weekly_sweep.py")],
        cwd=str(root / "src" / "cofounder_agent"),
    )
    report = json.loads(sweep.stdout or "{}") if sweep.stdout else {}
    proposals: list[str] = []
    for repo, gaps in report.get("gaps", {}).items():
        for gap in gaps:
            if "area" in gap.get("missing", []):
                area = pick_area_label(gap.get("body", ""))
                if area:
                    c.gh("issue", "edit", "--repo", repo, str(gap["number"]), "--add-label", area)
            proposals.append(f"{repo}#{gap['number']}: {gap.get('proposal', '')}")
    webhook = c.bootstrap_value("discord_ops_webhook_url")
    if webhook and proposals:
        body = "**Weekly triage: %d proposals**\n" % len(proposals) + "\n".join(proposals[:25])
        try:
            httpx.post(webhook, json={"content": body[:1900]}, timeout=15)
        except httpx.HTTPError as exc:
            log.warning("discord post failed: %s", exc)
    log.info("proposals=%d", len(proposals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> **Implementer note:** confirm the JSON shape emitted by `run_weekly_sweep.py`
> (`gaps` / `number` / `missing` / `body` / `proposal` keys) and adjust the accessors
> to match; the pure `pick_area_label` contract does not depend on it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_triage_sweep.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/triage_sweep.py src/cofounder_agent/tests/unit/scripts/test_ops_triage_sweep.py
git commit --no-verify -m "feat(ops): deterministic triage-sweep

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: `alert_triage.py` (local LLM, no worktree)

**Files:**

- Create: `scripts/ops_sessions/alert_triage.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_alert_triage.py`

**Interfaces:**

- Consumes: `_common.fetch_all`, `_common.ollama_chat`, `_common.MODEL_TRIAGE`, `_common.OllamaUnavailable`, `_common.gh`, `_common.notify_fail`, `_common.get_logger`, `_common.asyncio_run`.
- Produces: `build_classification_prompt(alertname, dispatch_result, probe_src) -> str`; `parse_classification(raw: str) -> dict` (keys `classification`, `reason`, `suspect_file`; `classification` normalized to `probe_bug`/`real_failure`); `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_alert_triage.py
from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import alert_triage as at  # noqa: E402


def test_prompt_includes_alert_and_probe():
    p = at.build_classification_prompt("BrainDaemonStale", "dispatched ok", "def probe(): ...")
    assert "BrainDaemonStale" in p
    assert "def probe()" in p


def test_parse_classification_valid_json():
    raw = '{"classification": "probe_bug", "reason": "dedup broken", "suspect_file": "brain/x_probe.py"}'
    out = at.parse_classification(raw)
    assert out["classification"] == "probe_bug"
    assert out["suspect_file"] == "brain/x_probe.py"


def test_parse_classification_normalizes_and_defaults():
    out = at.parse_classification('{"classification": "REAL_FAILURE"}')
    assert out["classification"] == "real_failure"
    assert out["reason"] == ""


def test_parse_classification_bad_json_raises():
    with pytest.raises(ValueError):
        at.parse_classification("not json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_alert_triage.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/ops_sessions/alert_triage.py
"""Classify noisy alerts (local Ollama) → file probe-bug issues."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
_SYSTEM = (
    "You are an SRE triaging a repeatedly-firing alert. Decide if the alert is a "
    "PROBE BUG (false positive / broken dedup repeating one fingerprint) or a REAL "
    "FAILURE (service down, resource exhausted). Respond with strict JSON: "
    '{"classification": "probe_bug"|"real_failure", "reason": "...", "suspect_file": "..."}'
)

NOISE_THRESHOLD = 5
WINDOW = "24 hours"


def build_classification_prompt(alertname: str, dispatch_result: str, probe_src: str) -> str:
    return (
        f"Alert: {alertname}\n"
        f"Most recent dispatch_result: {dispatch_result}\n\n"
        f"Probe source (if any):\n{probe_src or '(no probe file found)'}\n"
    )


def parse_classification(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"non-JSON classification: {raw[:120]}") from exc
    cls = str(data.get("classification", "")).strip().lower()
    return {
        "classification": cls,
        "reason": str(data.get("reason", "") or ""),
        "suspect_file": str(data.get("suspect_file", "") or ""),
    }


def _probe_source(root: Path, alertname: str) -> str:
    slug = alertname.lower().replace("-", "_")
    for cand in root.glob("brain/*probe*.py"):
        if slug[:8] in cand.name.lower():
            return cand.read_text(encoding="utf-8")[:4000]
    return ""


async def _noisy_alerts() -> list[dict]:
    rows = await c.fetch_all(
        f"""
        SELECT alertname, MAX(dispatch_result) AS dispatch_result, COUNT(*) AS n
        FROM alert_events
        WHERE received_at > NOW() - INTERVAL '{WINDOW}'
        GROUP BY alertname HAVING COUNT(*) > {NOISE_THRESHOLD}
        ORDER BY n DESC LIMIT 20
        """
    )
    return [dict(r) for r in rows]


def main() -> int:
    log = c.get_logger("alert-triage")
    root = next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())
    try:
        alerts = c.asyncio_run(_noisy_alerts())
    except Exception as exc:  # noqa: BLE001
        c.notify_fail("alert-triage DB error", str(exc)[:400], "alert_triage")
        return 1
    filed = 0
    for a in alerts:
        probe = _probe_source(root, a["alertname"])
        prompt = build_classification_prompt(a["alertname"], a.get("dispatch_result") or "", probe)
        try:
            raw = c.ollama_chat(prompt, model=c.MODEL_TRIAGE, system=_SYSTEM, as_json=True)
        except c.OllamaUnavailable as exc:
            c.notify_fail("alert-triage: Ollama down", str(exc)[:300], "alert_triage")
            return 1
        verdict = parse_classification(raw)
        if verdict["classification"] == "probe_bug":
            c.gh("issue", "create", "--repo", REPO, "--label", "bug",
                 "--title", f"probe bug: {a['alertname']} firing {a['n']}x/24h",
                 "--body", f"{verdict['reason']}\n\nSuspect: `{verdict['suspect_file']}`\n\n"
                           f"_Filed by the alert-triage ops session (local-model triage)._")
            filed += 1
    log.info("alerts=%d filed=%d", len(alerts), filed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_alert_triage.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/alert_triage.py src/cofounder_agent/tests/unit/scripts/test_ops_alert_triage.py
git commit --no-verify -m "feat(ops): local-LLM alert-triage classifier

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: `test_health.py` (local LLM, worktree, re-run gate)

**Files:**

- Create: `scripts/ops_sessions/test_health.py`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_test_health.py`

**Interfaces:**

- Consumes: `_common.run`, `_common.ollama_chat`, `_common.MODEL_TESTFIX`, `_common.OllamaUnavailable`, `_common.git`, `_common.gh`, `_common.get_logger`.
- Produces: `parse_pytest_failures(output: str) -> list[dict]` (each `{"file", "test", "message"}`); `extract_patched_file(raw: str) -> str | None` (pull a fenced code block); `main() -> int`.

- [ ] **Step 1: Write the failing test**

````python
# src/cofounder_agent/tests/unit/scripts/test_ops_test_health.py
from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import test_health as th  # noqa: E402


def test_parse_pytest_failures_extracts_nodeids():
    out = (
        "tests/unit/test_a.py::test_one PASSED\n"
        "FAILED tests/unit/test_b.py::test_two - AssertionError: 1 != 2\n"
        "FAILED tests/unit/scripts/test_c.py::test_three\n"
    )
    failures = th.parse_pytest_failures(out)
    assert {"file": "tests/unit/test_b.py", "test": "test_two", "message": "AssertionError: 1 != 2"} in failures
    assert any(f["test"] == "test_three" for f in failures)
    assert all("test_a" not in f["file"] for f in failures)


def test_extract_patched_file_pulls_code_fence():
    raw = "Here is the fix:\n```python\ndef test_x():\n    assert True\n```\nDone."
    assert th.extract_patched_file(raw) == "def test_x():\n    assert True"


def test_extract_patched_file_none_when_no_fence():
    assert th.extract_patched_file("no code here") is None
````

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_test_health.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

````python
# scripts/ops_sessions/test_health.py
"""Run pytest; local-model fix behind a deterministic re-run gate."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
_FAIL = re.compile(r"^FAILED\s+(\S+?)::(\S+?)(?:\s+-\s+(.*))?$", re.M)
_FENCE = re.compile(r"```(?:python)?\n(.*?)```", re.S)
_SYSTEM = (
    "You fix a single failing pytest test. Return ONLY the corrected full contents "
    "of the test file inside one ```python fenced block. Never edit production code."
)


def parse_pytest_failures(output: str) -> list[dict]:
    out = []
    for m in _FAIL.finditer(output):
        out.append({"file": m.group(1), "test": m.group(2), "message": (m.group(3) or "").strip()})
    return out


def extract_patched_file(raw: str) -> str | None:
    m = _FENCE.search(raw)
    return m.group(1).strip() if m else None


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("test-health")
    root = _repo_root()
    cwd = str(root / "src" / "cofounder_agent")
    first = c.run(["poetry", "run", "pytest", "tests/unit/", "-q", "--tb=short",
                   "-p", "no:cacheprovider", "--continue-on-collection-errors"], cwd=cwd)
    failures = parse_pytest_failures(first.stdout)
    fixed = 0
    for f in failures:
        if not f["file"].startswith("tests/"):
            continue  # never touch production code
        test_path = Path(cwd) / f["file"]
        original = test_path.read_text(encoding="utf-8")
        prompt = f"Failing test: {f['test']}\nError: {f['message']}\n\nFile:\n{original}"
        try:
            raw = c.ollama_chat(prompt, model=c.MODEL_TESTFIX, system=_SYSTEM, timeout=180)
        except c.OllamaUnavailable as exc:
            c.notify_fail("test-health: Ollama down", str(exc)[:300], "test_health")
            return 1
        patched = extract_patched_file(raw)
        if not patched:
            continue
        test_path.write_text(patched + "\n", encoding="utf-8")
        rerun = c.run(["poetry", "run", "pytest", f"{f['file']}::{f['test']}", "-q",
                       "-p", "no:cacheprovider"], cwd=cwd)
        if rerun.returncode == 0:
            fixed += 1
            log.info("fixed %s::%s", f["file"], f["test"])
        else:
            test_path.write_text(original, encoding="utf-8")  # re-run gate: revert
            log.info("reverted %s::%s (fix did not pass)", f["file"], f["test"])
    if fixed:
        c.git("add", "-A", cwd=cwd)
        c.git("commit", "--no-verify", "-m", f"test: repair {fixed} failing unit test(s) (ops test-health)", cwd=cwd)
        c.git("push", "-u", "origin", "HEAD", cwd=cwd)
        c.gh("pr", "create", "--repo", REPO, "--base", "main",
             "--title", f"test: repair {fixed} failing unit test(s) (ops)",
             "--body", "Local-model fixes, each verified green by re-run before inclusion.")
    log.info("failures=%d fixed=%d", len(failures), fixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
````

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_test_health.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/ops_sessions/test_health.py src/cofounder_agent/tests/unit/scripts/test_ops_test_health.py
git commit --no-verify -m "feat(ops): local-LLM test-health with re-run gate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Refactor the harness — `Command` / `NeedsWorktree` / `Enabled`

**Files:**

- Modify: `scripts/claude-sessions.ps1`
- Test: `src/cofounder_agent/tests/unit/scripts/test_ops_sessions_wiring.py`

**Interfaces:**

- Consumes: the 8 ops scripts (by path).
- Produces: a `$Sessions` hashtable where each rewired entry has `Command` (string, run inside the worktree/checkout), `NeedsWorktree` (bool), `Enabled` (bool); `Run-Session` dispatches `Command` (falling back to the legacy `claude.exe` path only for entries that still carry a `Prompt` and no `Command`); `Install-Sessions` skips `Enabled=$false`.

**Wiring table (put in the plan for the implementer).** Every `Command` uses the
literal `{runDir}` token; `Run-Session` substitutes the worktree path (when
`NeedsWorktree=$true`) or `$WorkDir` (when `$false`). The `cd …\src\cofounder_agent`
puts `poetry run` in the project dir; the script self-locates `ROOT` via `_repo_root()`.

| Session           | Command (PowerShell, `;` not `&&`)                                                                          | NeedsWorktree | Enabled  |
| ----------------- | ----------------------------------------------------------------------------------------------------------- | ------------- | -------- |
| dependency-review | `cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\dependency_review.py"` | `$false`      | `$true`  |
| codebase-audit    | `cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\codebase_audit.py"`    | `$true`       | `$true`  |
| doc-sync          | `cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\doc_sync.py"`          | `$true`       | `$true`  |
| claude-md-sync    | `cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\claude_md_sync.py"`    | `$true`       | `$true`  |
| triage-sweep      | `cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\triage_sweep.py"`      | `$false`      | `$true`  |
| alert-triage      | `cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\alert_triage.py"`      | `$false`      | `$true`  |
| test-health       | `cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\test_health.py"`       | `$true`       | `$true`  |
| issue-resolver    | _(keep existing `claude.exe` Prompt; no Command)_                                                           | `$true`       | `$false` |
| test-expansion    | _(keep existing `claude.exe` Prompt; no Command)_                                                           | `$true`       | `$false` |

- [ ] **Step 1: Write the failing test** (structural — Python asserts over the ps1 text)

```python
# src/cofounder_agent/tests/unit/scripts/test_ops_sessions_wiring.py
from __future__ import annotations

from pathlib import Path


def _ps1() -> str:
    root = next(p for p in Path(__file__).resolve().parents if (p / "scripts" / "claude-sessions.ps1").exists())
    return (root / "scripts" / "claude-sessions.ps1").read_text(encoding="utf-8")


def test_frontier_sessions_disabled():
    text = _ps1()
    # issue-resolver and test-expansion must be present but Enabled = $false
    for name in ("issue-resolver", "test-expansion"):
        assert name in text
    assert text.count("Enabled = $false") >= 2


def test_rewired_sessions_point_at_ops_scripts():
    text = _ps1()
    # Path uses backslashes in the ps1 (ops_sessions\dependency_review.py); assert on
    # the filename token, which is separator-agnostic.
    for module in (
        "dependency_review.py", "codebase_audit.py", "doc_sync.py",
        "claude_md_sync.py", "triage_sweep.py", "alert_triage.py", "test_health.py",
    ):
        assert module in text
    assert "ops_sessions" in text


def test_run_session_substitutes_rundir_token():
    text = _ps1()
    # the harness must replace the {runDir} placeholder before Start-Process
    assert "{runDir}" in text                      # tokens present in definitions
    assert ".Replace('{runDir}'" in text           # substitution wired in Run-Session
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_sessions_wiring.py -q`
Expected: FAIL — assertions about `ops_sessions` / `Enabled = $false` not yet present.

- [ ] **Step 3: Edit `claude-sessions.ps1`**

For each rewired session, replace the `Prompt = "..."` value with a `Command` + `NeedsWorktree` + `Enabled` triplet (use the literal `{runDir}` token), e.g.:

```powershell
    "dependency-review" = @{
        Command = 'cd "{runDir}\src\cofounder_agent"; poetry run python "{runDir}\scripts\ops_sessions\dependency_review.py"'
        NeedsWorktree = $false
        Enabled = $true
        Cron = "30 6 * * *"; TimeHH = "06"; TimeMM = "30"; Days = "daily"; MaxMinutes = 15
    }
```

Add `Enabled = $false` (and keep the existing `Prompt`) to `issue-resolver` and `test-expansion`.

In `Run-Session`, make the worktree setup conditional and pick `$runDir`; then dispatch on `Command` when present. `$runDir` is the worktree for `NeedsWorktree=$true`, else the shared `$WorkDir`:

```powershell
    $needsWt = $session.NeedsWorktree -ne $false   # default $true when unset (legacy claude.exe sessions)
    if ($needsWt) {
        # ... existing worktree add / node_modules junction block, producing $wt ...
        $runDir = $wt
    } else {
        $runDir = $WorkDir
    }

    if ($session.Command) {
        $cmd = $session.Command.Replace('{runDir}', $runDir)
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-NonInteractive", "-Command", $cmd) `
            -WorkingDirectory $runDir `
            -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err" `
            -NoNewWindow -PassThru
    } else {
        # legacy claude.exe path (frontier sessions only; currently disabled)
        $proc = Start-Process -FilePath $Claude `
            -ArgumentList "-p", "`"$prompt`"", "--model", $model, "--output-format", "text", "--dangerously-skip-permissions" `
            -WorkingDirectory $StartDir `
            -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err" `
            -NoNewWindow -PassThru
    }
```

**Also guard the `$wtPreamble` + `$prompt` construction** (currently built
unconditionally before the try-block; both reference `$wt`) so it only runs for
legacy `Prompt` sessions — e.g. wrap it in `if (-not $session.Command) { ... }`.
Guard the `finally` worktree teardown with the same `if ($needsWt)`. In
`Install-Sessions`, skip disabled entries:

```powershell
    foreach ($name in $Sessions.Keys) {
        if ($Sessions[$name].Enabled -eq $false) { Write-Host "Skipping disabled: $name"; continue }
        ...
    }
```

- [ ] **Step 4: Run test + a dry-run listing**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_sessions_wiring.py -q`
Expected: PASS.
Run: `powershell.exe -NoProfile -File scripts/claude-sessions.ps1 -List`
Expected: lists the 7 enabled sessions; issue-resolver / test-expansion absent from the registered set.

- [ ] **Step 5: Commit**

```bash
git add scripts/claude-sessions.ps1 src/cofounder_agent/tests/unit/scripts/test_ops_sessions_wiring.py
git commit --no-verify -m "feat(ops): harness dispatches Command; disable frontier sessions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Docs — runbook + CLAUDE.md section

**Files:**

- Create: `docs/operations/scheduled-agents.md`
- Modify: `CLAUDE.md` (the "Scheduled agents" section)

- [ ] **Step 1: Write `docs/operations/scheduled-agents.md`**

Cover: the two-tier model (5 deterministic + 2 local-LLM live; 2 frontier disabled), the OAuth-decay reason they were rewired (link the spec), what each session does + its schedule, where logs land (`~/.poindexter/logs/claude-sessions/`), the `OPS_OLLAMA_*` knobs, how to enable/disable (`.\claude-sessions.ps1 -Install` / `-List`), how a graceful stack-down surfaces (Discord via `notify_operator`), and the deferred metered-`issue-resolver` decision.

- [ ] **Step 2: Update the CLAUDE.md "Scheduled agents" section**

Replace the "STATUS … all currently DISABLED" narrative with the new reality: 7 rewired (list buckets), 2 frontier still disabled pending a metered decision, and a pointer to `docs/operations/scheduled-agents.md`. Do not touch the DB-derived stat lines.

- [ ] **Step 3: Commit**

```bash
git add docs/operations/scheduled-agents.md CLAUDE.md
git commit --no-verify -m "docs(ops): scheduled-agents runbook + CLAUDE.md refresh

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification (whole-plan, before PR)

- [ ] `cd src/cofounder_agent && poetry run pytest tests/unit/scripts/test_ops_*.py -q` → all green.
- [ ] `poetry run ruff check scripts/ops_sessions/` → clean.
- [ ] `powershell.exe -NoProfile -File scripts/claude-sessions.ps1 -List` → 7 enabled, 2 disabled.
- [ ] Manual smoke (stack up): `poetry run python scripts/ops_sessions/dependency_review.py` and `.../alert_triage.py` exit 0 and write a log under `~/.poindexter/logs/claude-sessions/`.
- [ ] Open the PR to `Glad-Labs/glad-labs-stack --base main`; let CI (test-backend, migrations-smoke) gate it.

## Self-Review Notes (author)

- **Spec coverage:** every session in the spec's triage table maps to a task (2–8); harness `Command`/`NeedsWorktree`/`Enabled` → Task 9; tests + docs → each task + Task 10; graceful stack-down → `notify_fail` in Tasks 7/8; re-run gate → Task 8 Step 3.
- **Spec divergences to confirm at handoff:** (1) `_common` reads `bootstrap.toml` directly for the Discord webhook + reuses `brain` for DB-URL/notify, rather than routing everything through one brain call — chosen for isolation; behavior identical. (2) directory is `ops_sessions` (underscore), not `ops-sessions` (hyphen) — required for import. (3) Discord digest posts via webhook directly (routine channel per Telegram-vs-Discord rule). (4) **`claude-md-sync` surfaces migration-narrative drift rather than auto-rewriting it** — the DB-count sync stays deterministic; prose is generated, not calculated, so the script flags rather than fakes it (`feedback_calculated_vs_generated`).
- **CWD contract (added in review):** a real bug in the first draft — several `main()`s used repo-root-relative paths with no explicit `cwd`. Fixed by the Working-directory rule in Global Constraints + explicit `cwd=` on every `git`/`ruff`/`bandit`/`pytest`/`poetry` call, and the `{runDir}` token + `cd …\src\cofounder_agent` in the harness Command.
- **Placeholder scan:** the one remaining "implementer note" (triage_sweep JSON shape) is a concrete instruction to confirm `run_weekly_sweep.py`'s emitted keys at read-time — the tested `pick_area_label` contract is independent of it. No "TODO"/"handle appropriately" vagueness remains.
- **Docs tasks (10):** prose deliverables (runbook, CLAUDE.md section) can't be TDD'd; each step names the exact sections to cover.
