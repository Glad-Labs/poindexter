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
