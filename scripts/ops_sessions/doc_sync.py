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

REPO = "Glad-Labs/glad-labs-stack"
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


# Citations are written in backticks by convention. Requiring them (and a left
# boundary) is not cosmetic: the first version matched bare text and flagged
# `project_directory` inside COMPOSE_PROJECT_DIRECTORY_SETTING_KEY.
_MEM_RE = re.compile(
    r"(?<![A-Za-z0-9_])``?((?:feedback|reference|project|decision)_[a-z0-9_]{3,})"
    r"(?:\.md)?``?"
)


def _is_citation(name: str, have: set[str]) -> bool:
    """Is this token a memory citation, or just a backticked identifier?

    The prefixes are ordinary English words, so ``project_directory`` — a
    function PARAMETER in compose_drift_probe — parses as a citation and then
    reports stale forever, because no such memory file will ever exist.

    Memory slugs are long: 397 of the 402 on disk carry two or more words
    after the prefix. The five that do not (``decision_log``,
    ``feedback_honesty``, ``project_monetization``, ``reference_gladlabs``,
    ``reference_glitchtip``) are recognised only when they RESOLVE. The cost
    is a false negative if one of those five is ever deleted while still
    cited; the benefit is that no single-word identifier can produce a
    permanent false positive. False positives are the expensive failure here
    — a report nobody trusts gets muted.
    """
    if name in have:
        return True
    return name.count("_") >= 2
_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}


def _memory_dir(repo_root: Path | None = None) -> Path | None:
    """The memory directory for THIS repo, or None when not on that machine.

    Derived from the repo path, never guessed. Claude Code keys a project's
    memory directory by the checkout path with ``/`` replaced by ``-``, so
    ``/home/x/glad-labs-website`` -> ``-home-x-glad-labs-website``.

    The first version of this globbed ``*/memory`` and took ``sorted(...)[0]``.
    That machine has FIVE such directories and the alphabetically-first one
    holds ZERO files — so every citation in the codebase came back "stale",
    a false-positive flood that read exactly like a real finding. An empty
    directory means the scan found nothing to compare against; it never means
    every reference is dead. Same floor as ``scripts/ci/lib_scan_floor.py``:
    a check that looked at nothing has not passed.
    """
    env = os.environ.get("POINDEXTER_MEMORY_DIR")
    if env:
        p = Path(env)
        return p if p.is_dir() and any(p.glob("*.md")) else None
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return None
    if repo_root is not None:
        keyed = projects / (str(repo_root.resolve()).replace("/", "-")) / "memory"
        if keyed.is_dir() and any(keyed.glob("*.md")):
            return keyed
    # Fall back to the richest candidate rather than the first alphabetically,
    # and require it to be non-empty.
    cands = [(len(list(p.glob("*.md"))), p) for p in projects.glob("*/memory")]
    cands = [(n, p) for n, p in cands if n > 0]
    if not cands:
        return None
    return max(cands)[1]


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
                    if name in have or not _is_citation(name, have):
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
    mem = _memory_dir(root)
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
