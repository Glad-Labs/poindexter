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
    # sys.executable = the main env python that launched us; the worktree has no
    # provisioned venv, so never spawn a fresh `poetry run` from inside it.
    first = c.run([sys.executable, "-m", "pytest", "tests/unit/", "-q", "--tb=short",
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
        rerun = c.run([sys.executable, "-m", "pytest", f"{f['file']}::{f['test']}", "-q",
                       "-p", "no:cacheprovider"], cwd=cwd)
        if rerun.returncode == 0:
            fixed += 1
            log.info("fixed %s::%s", f["file"], f["test"])
        else:
            test_path.write_text(original, encoding="utf-8")  # re-run gate: revert
            log.info("reverted %s::%s (fix did not pass)", f["file"], f["test"])
    pr_ok = True
    if fixed:
        pr_ok = c.commit_and_open_pr(
            cwd=cwd,
            repo=REPO,
            paths=["-A"],
            message=f"test: repair {fixed} failing unit test(s) (ops test-health)",
            title=f"test: repair {fixed} failing unit test(s) (ops)",
            body="Local-model fixes, each verified green by re-run before inclusion.",
            log=log,
            source="test_health",
        ) is not None
    log.info("failures=%d fixed=%d", len(failures), fixed)
    return 0 if pr_ok else 1


if __name__ == "__main__":
    sys.exit(main())
