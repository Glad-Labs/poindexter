#!/usr/bin/env python3
"""Lint .github/workflows/ for unpinned third-party Actions usage.

Defends against the floating-tag supply-chain vector documented in
``docs/security/audit-2026-05-12.md`` P1 #14:

- ``pypa/gh-action-pypi-publish@release/v1`` pinned to a moving branch.
  A compromised maintainer with ``id-token: write`` could mint a PyPI
  OIDC token and publish a backdoored ``poindexter`` wheel under our
  Trusted Publisher.
- ``actions/checkout@v4`` etc. pinned to a major-version tag. Tags
  are mutable; an attacker who gained write to the action repo could
  retarget ``v4`` at a malicious commit.

The mitigation: every ``uses: <owner>/<repo>...@<ref>`` line MUST point
at a full 40-char commit SHA. A trailing ``# <semver tag> as of <date>``
comment is recommended (and not enforced) — it makes Dependabot's
SHA refresh PRs reviewable.

EXEMPTIONS

- Local actions (``uses: ./.github/actions/...``) — owned by this repo,
  no upstream-maintainer attack surface.
- Docker-image actions (``uses: docker://...``) — versioning is the
  container registry's job, not ours.

EXIT CODES

- 0: every third-party action is SHA-pinned.
- 1: one or more unpinned references found (printed with file:line).
- 2: invocation / IO error.

Wired into CI as a step in ``.github/workflows/security.yml``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Match the `uses:` value on a workflow step. We do NOT use a YAML parser
# here on purpose: the lint should still flag the literal text even if
# someone hand-edits a workflow into an unusual indentation, and PyYAML
# strips comments (so we'd lose the trailing-tag-comment styling check
# if we ever add one).
USES_PATTERN = re.compile(r"^\s*-?\s*uses:\s*(['\"]?)([^'\"\s#]+)\1\s*(?:#.*)?$")

# Full-length git SHA — actions resolve ref → commit on dispatch, so the
# only SHA value GitHub Actions accepts as a pin is the 40-char form.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _is_exempt(uses_value: str) -> bool:
    """Return True for refs that don't need SHA-pinning."""
    if uses_value.startswith("./"):
        # Local composite/JS action — same trust boundary as this repo.
        return True
    if uses_value.startswith("docker://"):
        # Container-image action — registry handles versioning.
        return True
    return False


def _check_workflow(path: Path) -> list[str]:
    """Return a list of failure descriptions for *path*.

    Each entry is a human-readable string like
    ``release.yml:42 actions/checkout@v4 — not SHA-pinned``.
    """
    failures: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = USES_PATTERN.match(raw)
        if not match:
            continue
        uses_value = match.group(2)
        if _is_exempt(uses_value):
            continue
        # Split on the last '@' — owner/repo can contain slashes but
        # never an '@' on the GitHub side.
        if "@" not in uses_value:
            failures.append(
                f"{path.name}:{lineno} {uses_value} — missing @<ref> entirely"
            )
            continue
        action, _, ref = uses_value.rpartition("@")
        if not SHA_RE.fullmatch(ref):
            failures.append(
                f"{path.name}:{lineno} {action}@{ref} — not SHA-pinned "
                f"(needs 40-char commit SHA; tag the resolved version "
                f"in a trailing '# <tag> as of <date>' comment)"
            )
    return failures


def main() -> int:
    if not WORKFLOWS_DIR.is_dir():
        print(
            f"check-action-pins: workflows directory not found at {WORKFLOWS_DIR}",
            file=sys.stderr,
        )
        return 2
    all_failures: list[str] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        all_failures.extend(_check_workflow(path))
    for path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        all_failures.extend(_check_workflow(path))
    if all_failures:
        print(
            "check-action-pins: found unpinned third-party Actions usage:",
            file=sys.stderr,
        )
        for line in all_failures:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nFix: resolve each tag to its commit SHA "
            "(`gh api repos/<owner>/<repo>/git/refs/tags/<tag>`), "
            "replace the @ref, and append a trailing "
            "`# <tag> as of <YYYY-MM-DD>` comment. See "
            "docs/security/audit-2026-05-12.md P1 #14 for the rationale.",
            file=sys.stderr,
        )
        return 1
    print("check-action-pins: OK — every third-party action is SHA-pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
