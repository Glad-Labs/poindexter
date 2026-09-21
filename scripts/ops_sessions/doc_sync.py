"""Verify + repair CLAUDE.md path references, and report stale memory
citations in code. Deterministic, worktree.

The memory half runs HERE rather than in CI because it needs the operator's
memory directory, which is not in the repo. Its complement — dead file paths
in code comments — is fully checkable and lives in
``scripts/ci/comment_reference_lint.py`` as a ratchet.

Memory citations are REPORTED, never auto-repaired. A dead citation means one
of two things that look identical from here: the memory file was renamed (the
policy stands, fix the name) or the policy was reversed and its file deleted
(the comment is now a lie, rewrite it). Guessing between those is how a stale
comment becomes a confidently-wrong comment. 2026-09-20: a comment citing
``feedback_no_humans_in_ai_media`` described a policy retired six days earlier
and produced a recommendation to re-introduce the retired ban.
"""
from __future__ import annotations

import ast
import os
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


_MEM_RE = re.compile(r"``?((?:feedback|reference|project|decision)_[a-z0-9_]{4,})``?")
_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}


def _memory_dir() -> Path | None:
    """The operator's memory directory, or None when not on that machine."""
    env = os.environ.get("POINDEXTER_MEMORY_DIR")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    home = Path.home() / ".claude" / "projects"
    if not home.is_dir():
        return None
    cands = sorted(home.glob("*/memory"))
    return cands[0] if cands else None


def _comment_text(path: Path):
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in src.splitlines():
        i = line.find("#")
        if i >= 0 and line[:i].count('"') % 2 == 0:
            yield line[i:]
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                yield doc


def stale_memory_citations(root: Path, mem: Path) -> dict[str, list[str]]:
    """{memory_name: [files citing it]} for names with no file on disk."""
    have = {f.stem for f in mem.glob("*.md")}
    dead: dict[str, list[str]] = {}
    scan = root / "src" / "cofounder_agent"
    for dirpath, dirs, files in os.walk(scan):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            for blob in _comment_text(p):
                for name in set(_MEM_RE.findall(blob)):
                    if name in have:
                        continue
                    dead.setdefault(name, [])
                    if rel not in dead[name]:
                        dead[name].append(rel)
    return dead


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
    # Memory citations: report only. A missing memory dir is reported as
    # SKIPPED, never as clean — a check that looked at nothing has not passed.
    mem = _memory_dir()
    if mem is None:
        mem_note = ("memory citations: SKIPPED — no memory directory on this "
                    "machine (set POINDEXTER_MEMORY_DIR to enable)")
        log.info(mem_note)
    else:
        stale = stale_memory_citations(root, mem)
        if stale:
            lines = [f"- `{n}` cited by {', '.join(f) if len(f) <= 3 else f'{len(f)} files'}"
                     for n, f in sorted(stale.items())]
            mem_note = ("**Stale memory citations** (the named memory file no "
                        "longer exists — either it was renamed and the policy "
                        "stands, or the policy was reversed and the comment is "
                        "now wrong; both need a human):\n" + "\n".join(lines))
            log.warning("stale memory citations: %s", sorted(stale))
        else:
            mem_note = f"memory citations: all live ({len(list(mem.glob('*.md')))} files on disk)"
            log.info(mem_note)

    pr_ok = True
    if changed:
        claude_md.write_text(text, encoding="utf-8")
        pr_ok = c.commit_and_open_pr(
            cwd=str(root),
            repo=REPO,
            paths=["CLAUDE.md"],
            message="docs(CLAUDE.md): repair moved path references (ops doc-sync)",
            title="docs(CLAUDE.md): repair path references (ops)",
            body=(f"Auto-corrected moved refs. Unresolved (need human): "
                  f"{flags or 'none'}\n\n{mem_note}"),
            log=log,
            source="doc_sync",
        ) is not None
    log.info("changed=%s flags=%s", changed, flags)
    if not changed:
        log.info("no CLAUDE.md change; %s", mem_note.splitlines()[0])
    return 0 if pr_ok else 1


if __name__ == "__main__":
    sys.exit(main())
