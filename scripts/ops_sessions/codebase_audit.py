"""ruff --fix (F401/F841) + bandit → security issue. Deterministic, worktree."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/poindexter"
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
    # Reuse the launching interpreter (main checkout's env) so tooling resolves
    # even though the worktree has no provisioned venv of its own.
    c.run([sys.executable, "-m", "ruff", "check", "--fix", "--select", "F401,F841", *RUFF_TARGETS], cwd=root)
    bandit = c.run([sys.executable, "-m", "bandit", "-r", *BANDIT_TARGETS, "-q", "-ll", "-f", "json"], cwd=root)
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
