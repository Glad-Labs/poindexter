"""Verify + repair CLAUDE.md path references. Deterministic, worktree."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/poindexter"
# A reference starts at a path-token boundary. Without the lookbehind, `brain/`
# also matched INSIDE `poindexter/brain/seed_app_settings.json`, the repaired
# substring was spliced back into the longer path, and the 2026-09-11 run
# proposed `poindexter/src/cofounder_agent/poindexter/brain/...` (#3657).
_REF = re.compile(r"(?<![A-Za-z0-9_./@-])(?:src|docs|infrastructure|scripts|brain)/[A-Za-z0-9_./@-]+")


def replace_ref(text: str, ref: str, fix: str) -> str:
    """Replace whole-token occurrences of ``ref`` only — never a substring of a
    longer path (that is how a repair double-prefixes a shorthand reference)."""
    return re.sub(
        r"(?<![A-Za-z0-9_./@-])" + re.escape(ref) + r"(?!\.?[A-Za-z0-9_/@-])",
        fix.replace("\\", "\\\\"),
        text,
    )


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
            text = replace_ref(text, ref, fix)
            changed = True
            log.info("fixed %s -> %s", ref, fix)
        elif status == "flag":
            flags.append(ref)
    pr_ok = True
    if changed:
        claude_md.write_text(text, encoding="utf-8")
        pr_ok = c.commit_and_open_pr(
            cwd=str(root),
            repo=REPO,
            paths=["CLAUDE.md"],
            message="docs(CLAUDE.md): repair moved path references (ops doc-sync)",
            title="docs(CLAUDE.md): repair path references (ops)",
            body=f"Auto-corrected moved refs. Unresolved (need human): {flags or 'none'}",
            log=log,
            source="doc_sync",
        ) is not None
    log.info("changed=%s flags=%s", changed, flags)
    return 0 if pr_ok else 1


if __name__ == "__main__":
    sys.exit(main())
