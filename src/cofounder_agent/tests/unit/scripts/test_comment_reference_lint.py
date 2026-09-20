"""The stale-comment ratchet must actually catch a stale comment."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Anchor on a sentinel, never a parents[N] depth: this file's depth changed
# once already when the package moved under poindexter/ (#1046), and a depth
# walk fails silently by resolving to the wrong directory.
REPO = next(p for p in Path(__file__).resolve().parents
            if (p / "scripts" / "ci" / "comment_reference_lint.py").exists())
LINT = REPO / "scripts" / "ci" / "comment_reference_lint.py"
BASELINE = REPO / "scripts" / "ci" / "comment_reference_baseline.json"

def _run(cwd: Path, script: Path | None = None):
    """Run the lint. ``script`` matters: the lint resolves its scan root from
    its OWN __file__, not from cwd, so an empty-tree test must execute the
    COPY inside that tree — running the real one with a different cwd proves
    nothing."""
    return subprocess.run([sys.executable, str(script or LINT)], cwd=str(cwd),
                          capture_output=True, text=True)

def test_repo_is_clean_against_its_baseline():
    r = _run(REPO)
    assert r.returncode == 0, f"stale comment introduced:\n{r.stdout}\n{r.stderr}"
    assert "scanned" in r.stdout, "must report what it looked at"

def test_baseline_is_valid_and_non_empty():
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert data["files"], "an empty baseline means the scan found nothing — suspicious"
    for rel, refs in data["files"].items():
        assert rel.startswith("src/"), rel
        assert all(isinstance(n, int) and n > 0 for n in refs.values())

def test_detects_a_dead_reference(tmp_path):
    """A comment citing a file that does not exist must fail the lint."""
    mod = REPO / "src/cofounder_agent/poindexter/services/settings_categories.py"
    original = mod.read_text(encoding="utf-8")
    try:
        mod.write_text(
            "# See ``services/this_module_does_not_exist.py`` for details.\n"
            + original, encoding="utf-8")
        r = _run(REPO)
        assert r.returncode == 1, "a dead reference must fail the ratchet"
        assert "this_module_does_not_exist.py" in r.stdout
    finally:
        mod.write_text(original, encoding="utf-8")

def test_ignores_placeholders_and_urls(tmp_path):
    """Illustrative stand-ins are not references and must not trip the gate."""
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    import comment_reference_lint as lint

    assert not lint.is_reference("services/x.py")
    assert not lint.is_reference("https://example.com/a.py")
    assert not lint.is_reference("foo.py")
    assert lint.is_reference("services/settings_categories.py")

def test_scan_floor_refuses_an_empty_tree(tmp_path):
    """A lint that scanned nothing has not passed."""
    (tmp_path / "scripts" / "ci").mkdir(parents=True)
    for name in ("comment_reference_lint.py", "lib_scan_floor.py"):
        (tmp_path / "scripts" / "ci" / name).write_bytes(
            (REPO / "scripts" / "ci" / name).read_bytes())
    r = _run(tmp_path, script=tmp_path / "scripts" / "ci" / "comment_reference_lint.py")
    assert r.returncode != 0, "an empty tree must not report clean"
    out = (r.stdout + r.stderr).lower()   # the floor guard writes to stderr
    assert "refusing" in out or "does not exist" in out, out[:300]
