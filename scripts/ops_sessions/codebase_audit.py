"""ruff --fix (F401/F841) -> lint PR. Deterministic, worktree.

Bandit used to run here too, filing one GitHub issue per finding. It is gone:
security findings are now gated by the **bandit ratchet**
(``scripts/ci/bandit_lint.py`` + ``scripts/ci/bandit_baseline.json``), which
blocks a net-new finding in CI at PR time and files nothing.

Why the flip: bandit is a textual matcher with no dataflow analysis, so on this
codebase it cannot tell a real injection from the sanctioned "hardcoded
identifier + asyncpg bind params" pattern that ``services/`` is built on. It
filed 91 issues; every one examined was a false positive (#2594-#2623, all
closed by #2644), and they buried the 18 genuine engineering issues under three
pages of noise. A ratchet holds the same line without the noise — and unlike a
weekly issue, it blocks the regression *before* it lands.

That also makes the dedup logic (#2645) moot: nothing is filed, so nothing can
be re-filed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"
RUFF_TARGETS = ["src/", "brain/", "scripts/"]


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def main() -> int:
    log = c.get_logger("codebase-audit")
    root = str(_repo_root())
    # Reuse the launching interpreter (main checkout's env) so tooling resolves
    # even though the worktree has no provisioned venv of its own.
    c.run([sys.executable, "-m", "ruff", "check", "--fix", "--select", "F401,F841", *RUFF_TARGETS], cwd=root)
    status = c.git("status", "--porcelain", cwd=root)
    has_lint_fixes = bool(status.stdout.strip())
    if has_lint_fixes:
        c.git("add", "-A", cwd=root)
        c.git("commit", "--no-verify", "-m", "fix(lint): ruff --fix F401/F841 (ops codebase-audit)", cwd=root)
        c.git("push", "-u", "origin", "HEAD", cwd=root)
        c.gh("pr", "create", "--repo", REPO, "--base", "main",
             "--title", "fix(lint): ruff F401/F841 sweep (ops)",
             "--body", "Automated unused-import/variable fixes.")
    log.info("%s", "opened lint PR" if has_lint_fixes else "no ruff fixes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
