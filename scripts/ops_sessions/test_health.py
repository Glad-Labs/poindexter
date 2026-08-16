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


def _fails_in_isolation(cwd: str, node_id: str) -> bool:
    """Does this test still fail when run ALONE?

    The whole session is built on one assumption: a failing test can be fixed
    by rewriting its own file. That is false for an *order-dependent* failure —
    one caused by state another test leaked (a module global, a patched
    ``sys.modules`` entry, a stray env var). Those pass in isolation no matter
    what the file contains.

    Without this check the re-run gate is worse than no gate. The gate re-runs
    exactly one test id, so for a polluted test it passes **whatever the LLM
    wrote** — including a rewrite that deletes the assertions. The session then
    counts a "fix", opens a PR, and the original suite-level failure is still
    there. Observed live: all four failures test-health hit on 2026-08-07 were
    order-dependent (``test_litellm_langfuse_callback.py``, green in isolation,
    red in a full run), so every one would have produced a bogus patch.

    Skipping them is the honest outcome — an order-dependent failure is a real
    bug, but it lives in whatever leaked the state, not in the file that
    tripped over it, and a local 7B model rewriting the victim cannot fix it.
    """
    probe = c.run([sys.executable, "-m", "pytest", node_id, "-q",
                   "-p", "no:cacheprovider"], cwd=cwd)
    return probe.returncode != 0


def main() -> int:
    log = c.get_logger("test-health")
    root = _repo_root()
    cwd = str(root / "src" / "cofounder_agent")
    # Fail in seconds with the pull remedy, not after a full suite run — the
    # missing-testfix-pin failure burned three consecutive 03:00 runs before
    # anything named the model (stack#3163).
    try:
        c.preflight_model_pins(c.MODEL_TESTFIX)
    except c.OllamaUnavailable as exc:
        log.error("model-pin preflight failed: %s", exc)
        c.notify_fail("test-health: Ollama preflight failed", str(exc)[:400], "test_health")
        return 1
    # sys.executable = the main env python that launched us; the worktree has no
    # provisioned venv, so never spawn a fresh `poetry run` from inside it.
    first = c.run([sys.executable, "-m", "pytest", "tests/unit/", "-q", "--tb=short",
                   "-p", "no:cacheprovider", "--continue-on-collection-errors"], cwd=cwd)
    failures = parse_pytest_failures(first.stdout)
    fixed = 0
    order_dependent = 0
    for f in failures:
        if not f["file"].startswith("tests/"):
            continue  # never touch production code
        node_id = f"{f['file']}::{f['test']}"
        # Pre-flight BEFORE spending an LLM call: a test that passes alone is
        # order-dependent, and the post-patch re-run gate below would rubber-
        # stamp any rewrite of it (see _fails_in_isolation).
        if not _fails_in_isolation(cwd, node_id):
            order_dependent += 1
            log.info("skipped %s (passes in isolation — order-dependent)", node_id)
            continue
        test_path = Path(cwd) / f["file"]
        original = test_path.read_text(encoding="utf-8")
        prompt = f"Failing test: {f['test']}\nError: {f['message']}\n\nFile:\n{original}"
        try:
            raw = c.ollama_chat(prompt, model=c.MODEL_TESTFIX, system=_SYSTEM, timeout=180)
        except c.OllamaUnavailable as exc:
            # Covers a missing model too, not just a dead daemon — ollama_chat
            # maps a 404 here rather than letting HTTPStatusError escape.
            c.notify_fail("test-health: Ollama unusable", str(exc)[:300], "test_health")
            return 1
        patched = extract_patched_file(raw)
        if not patched:
            continue
        test_path.write_text(patched + "\n", encoding="utf-8")
        rerun = c.run([sys.executable, "-m", "pytest", node_id, "-q",
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
    # order_dependent is reported separately rather than folded into failures:
    # a run of "failures=4 fixed=0" reads like the model was useless, when in
    # fact nothing was fixable by this session at all. The distinction is what
    # tells the operator to go look for the state leak instead of the model.
    log.info(
        "failures=%d fixed=%d order_dependent=%d",
        len(failures), fixed, order_dependent,
    )
    if order_dependent:
        log.warning(
            "%d failure(s) pass in isolation — order-dependent (leaked state "
            "from another test). Not fixable by rewriting the failing file; "
            "find the polluter with `pytest <dir> -p no:randomly` and bisect.",
            order_dependent,
        )
    return 0 if pr_ok else 1


if __name__ == "__main__":
    sys.exit(main())
