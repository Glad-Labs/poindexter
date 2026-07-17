"""ruff --fix (F401/F841) + bandit → security issue. Deterministic, worktree."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
RUFF_TARGETS = ["src/", "brain/", "scripts/"]
BANDIT_TARGETS = ["brain/", "scripts/", "src/cofounder_agent/services/", "src/cofounder_agent/routes/"]


def finding_dedup_key(finding: dict) -> str:
    """Stable identity for a bandit finding — survives line drift + reformatting.

    Keyed on test_id + filename + a whitespace-normalized snippet of the
    flagged code, deliberately NOT line_number (which drifts whenever an
    unrelated edit lands above the finding).
    """
    normalized_code = " ".join(finding.get("code", "").split())
    raw = f"{finding['test_id']}|{finding['filename']}|{normalized_code}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def bandit_issue_body(finding: dict) -> tuple[str, str]:
    loc = f"{finding['filename']}:{finding['line_number']}"
    title = f"security(bandit): {finding['test_id']} in {finding['filename']}"
    body = (
        f"**Severity:** {finding['issue_severity']}\n"
        f"**Rule:** {finding['test_id']}\n"
        f"**Location:** `{loc}`\n\n"
        f"{finding['issue_text']}\n\n"
        f"```python\n{finding.get('code', '').strip()}\n```\n\n"
        f"_Filed by the deterministic codebase-audit ops session._\n"
        f"<!-- codebase-audit-dedup-key: {finding_dedup_key(finding)} -->"
    )
    return title, body


def _has_existing_issue(key: str) -> bool:
    """True if any issue (open OR closed) already tracks this exact finding.

    Deliberately state=all: a finding closed as "false positive" or "won't
    fix" must never be re-filed just because the flagged code is untouched —
    unlike a probe-bug alert, the same line will trip bandit forever until
    someone edits it, so "closed" here means "decided", not "resolved".

    Fail-safe: any error checking (gh failure, unparseable output) returns
    True so a transient hiccup skips filing rather than risking a duplicate
    burst — the weekly cron retries regardless.
    """
    proc = c.gh(
        "issue", "list", "--repo", REPO, "--state", "all",
        "--search", f'label:security "{key}" in:body',
        "--json", "number", "--limit", "1",
    )
    if proc.returncode != 0:
        return True
    try:
        return bool(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return True


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("codebase-audit")
    root = str(_repo_root())
    # Reuse the launching interpreter (main checkout's env) so tooling resolves
    # even though the worktree has no provisioned venv of its own.
    c.run([sys.executable, "-m", "ruff", "check", "--fix", "--select", "F401,F841", *RUFF_TARGETS], cwd=root)
    bandit = c.run([sys.executable, "-m", "bandit", "-r", *BANDIT_TARGETS, "-q", "-ll", "-f", "json"], cwd=root)
    findings = json.loads(bandit.stdout or "{}").get("results", []) if bandit.stdout else []
    filed = 0
    skipped = 0
    for f in findings:
        if _has_existing_issue(finding_dedup_key(f)):
            skipped += 1
            continue
        title, body = bandit_issue_body(f)
        c.gh("issue", "create", "--repo", REPO, "--label", "security", "--title", title, "--body", body)
        filed += 1
    status = c.git("status", "--porcelain", cwd=root)
    has_lint_fixes = bool(status.stdout.strip())
    prefix = "opened lint PR" if has_lint_fixes else "no ruff fixes"
    if has_lint_fixes:
        c.git("add", "-A", cwd=root)
        c.git("commit", "--no-verify", "-m", "fix(lint): ruff --fix F401/F841 (ops codebase-audit)", cwd=root)
        c.git("push", "-u", "origin", "HEAD", cwd=root)
        c.gh("pr", "create", "--repo", REPO, "--base", "main",
             "--title", "fix(lint): ruff F401/F841 sweep (ops)",
             "--body", "Automated unused-import/variable fixes.")
    log.info(
        "%s; bandit findings=%d filed=%d skipped_already_tracked=%d",
        prefix, len(findings), filed, skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
