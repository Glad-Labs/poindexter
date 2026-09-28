"""The stale-comment ratchet must actually catch a stale comment."""
from __future__ import annotations

import json
import os
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

# What Actions sets on the public mirror's jobs. Assembled, not spelled, so the
# mirror sync's org/name rewrite can never turn one into the other in this file.
MIRROR_ENV = {"GITHUB_REPOSITORY": "Glad-Labs/" + "poindexter"}
SOURCE_ENV = {"GITHUB_REPOSITORY": "Glad-Labs/" + "glad-labs-stack"}

def _run(cwd: Path, script: Path | None = None, env: dict[str, str] | None = None,
         args: tuple[str, ...] = ()):
    """Run the lint. ``script`` matters: the lint resolves its scan root from
    its OWN __file__, not from cwd, so a test on a throwaway tree must execute
    the COPY inside that tree — running the real one with a different cwd
    proves nothing.

    Strict unless ``env`` says otherwise: an inherited GITHUB_REPOSITORY would
    switch the lint into its public-mirror mode behind the test's back."""
    run_env = {k: v for k, v in os.environ.items() if k != "GITHUB_REPOSITORY"}
    run_env.update(env or {})
    return subprocess.run([sys.executable, str(script or LINT), *args], cwd=str(cwd),
                          capture_output=True, text=True, env=run_env)

def _copy_lint(root: Path) -> Path:
    """Copy the lint and the helpers it imports into ``root/scripts/ci``; return
    the copy. Its repo root, scan root and baseline all resolve from its own
    __file__, so running the copy examines ``root`` and never the checkout."""
    ci = root / "scripts" / "ci"
    ci.mkdir(parents=True)
    for name in ("comment_reference_lint.py", "lib_scan_floor.py", "lib_public_mirror.py"):
        (ci / name).write_bytes((REPO / "scripts" / "ci" / name).read_bytes())
    return ci / "comment_reference_lint.py"

def _tree_citing(root: Path, comment: str) -> Path:
    """A one-module tree whose only comment is ``comment``, with an empty baseline."""
    lint = _copy_lint(root)
    (lint.parent / BASELINE.name).write_text('{"files": {}}\n', encoding="utf-8")
    module = root / "src/cofounder_agent/poindexter/services/settings_categories.py"
    module.parent.mkdir(parents=True)
    module.write_text(comment, encoding="utf-8")
    return lint

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
    """A comment citing a file that does not exist must fail the lint.

    The dead reference goes into a throwaway tree, never the checkout. This
    test used to prepend it to the real ``settings_categories.py`` and put the
    file back in a ``finally``. A pytest killed mid-test (OOM, timeout, Ctrl-C)
    never runs the ``finally``, which leaves a tracked source file corrupted,
    and anything else reading the tree during the run saw the injected line
    (seen on 2026-09-25 during a full unit run)."""
    lint = _copy_lint(tmp_path)
    (lint.parent / BASELINE.name).write_text('{"files": {}}\n', encoding="utf-8")
    module = tmp_path / "src/cofounder_agent/poindexter/services/settings_categories.py"
    module.parent.mkdir(parents=True)

    # Control: the same comment citing a file that exists (this module itself)
    # passes, so the failure below comes from the dead path, not from a tree
    # too thin to scan. The floor guard also exits 1, so without this control a
    # tree the lint refused would pass the returncode check.
    module.write_text("# See ``services/settings_categories.py`` for details.\n",
                      encoding="utf-8")
    r = _run(tmp_path, script=lint)
    assert r.returncode == 0, f"a live reference must pass:\n{r.stdout}\n{r.stderr}"

    module.write_text("# See ``services/this_module_does_not_exist.py`` for details.\n",
                      encoding="utf-8")
    r = _run(tmp_path, script=lint)
    assert r.returncode == 1, "a dead reference must fail the ratchet"
    assert "this_module_does_not_exist.py" in r.stdout

def test_ignores_placeholders_and_urls(tmp_path):
    """Illustrative stand-ins are not references and must not trip the gate."""
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    import comment_reference_lint as lint

    assert not lint.is_reference("services/x.py")
    assert not lint.is_reference("https://example.com/a.py")
    assert not lint.is_reference("foo.py")
    assert lint.is_reference("services/settings_categories.py")

def test_scan_floor_refuses_an_empty_tree(tmp_path):
    """A lint that scanned nothing has not passed, on the mirror as anywhere."""
    lint = _copy_lint(tmp_path)
    for env in (None, MIRROR_ENV):
        r = _run(tmp_path, script=lint, env=env)
        assert r.returncode != 0, f"an empty tree must not report clean (env={env})"
        out = (r.stdout + r.stderr).lower()   # the floor guard writes to stderr
        assert "refusing" in out or "does not exist" in out, out[:300]

def test_public_mirror_counts_absent_paths_instead_of_failing(tmp_path):
    """The mirror is this tree minus the files the sync strips, so a comment
    citing one points at nothing there while the file is alive in the source
    repository. That held the mirror's unit-tests job red for eight days.

    The same citation must fail a local run and the source repository's CI,
    and pass on the mirror, COUNTED but not LISTED. A list would be an index of
    the stripped files, printed into the public repository's CI log."""
    lint = _tree_citing(tmp_path, "# See ``services/only_in_the_source_repo.py``.\n")

    for env in (None, SOURCE_ENV):
        r = _run(tmp_path, script=lint, env=env)
        assert r.returncode == 1, f"must fail outside the mirror (env={env}):\n{r.stdout}"
        assert "only_in_the_source_repo.py" in r.stdout

    r = _run(tmp_path, script=lint, env=MIRROR_ENV)
    assert r.returncode == 0, f"must pass on the mirror:\n{r.stdout}\n{r.stderr}"
    assert "public mirror" in r.stdout
    assert "1 reference(s) to paths absent from this tree" in r.stdout
    assert "only_in_the_source_repo" not in r.stdout + r.stderr, (
        "the mirror's log must not list what the sync stripped")

def test_public_mirror_still_passes_a_live_reference_cleanly(tmp_path):
    """Mirror mode only changes what happens to ABSENT paths."""
    lint = _tree_citing(tmp_path, "# See ``services/settings_categories.py``.\n")
    r = _run(tmp_path, script=lint, env=MIRROR_ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "clean" in r.stdout and "public mirror" not in r.stdout

def test_public_mirror_refuses_to_write_a_baseline(tmp_path):
    """A baseline written from the stripped tree would record every stripped
    file a comment cites, in a file that ships to the public mirror."""
    lint = _tree_citing(tmp_path, "# See ``services/only_in_the_source_repo.py``.\n")
    baseline = lint.parent / BASELINE.name
    before = baseline.read_text(encoding="utf-8")

    r = _run(tmp_path, script=lint, env=MIRROR_ENV, args=("--update-baseline",))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "refusing" in r.stderr
    assert baseline.read_text(encoding="utf-8") == before, "the baseline must not be touched"

def test_shrunk_baseline_passes_and_reports_found_and_baselined_separately(tmp_path):
    """A baseline allowing a reference the tree no longer has must stay clean.

    ``total`` in the success message used to be the count FOUND in the tree,
    not the count in the baseline — collapsing them into one figure hid a
    stale baseline entry (two dead references sat un-pruned for weeks because
    the message read the same either way). The fix prints both, plus a
    re-baseline tail when they differ.
    """
    lint = _copy_lint(tmp_path)
    module = tmp_path / "src/cofounder_agent/poindexter/services/settings_categories.py"
    module.parent.mkdir(parents=True)
    module.write_text("# nothing referenced here.\n", encoding="utf-8")

    # The baseline allows a reference this tree does not contain — the
    # "already fixed in code, never re-baselined" scenario.
    (lint.parent / BASELINE.name).write_text(
        json.dumps({"files": {
            "src/cofounder_agent/poindexter/services/settings_categories.py": {
                "docs/some_deleted_doc.md": 1,
            },
        }}),
        encoding="utf-8")

    r = _run(tmp_path, script=lint)
    assert r.returncode == 0, f"a shrunk baseline must still be clean:\n{r.stdout}\n{r.stderr}"
    assert "0 found" in r.stdout
    assert "1 baselined" in r.stdout
    assert "re-baseline to lock the win in" in r.stdout

def test_matched_baseline_reports_no_tail(tmp_path):
    """Found == baselined must print cleanly with no re-baseline tail."""
    lint = _copy_lint(tmp_path)
    module = tmp_path / "src/cofounder_agent/poindexter/services/settings_categories.py"
    module.parent.mkdir(parents=True)
    module.write_text("# See ``docs/some_deleted_doc.md`` for details.\n", encoding="utf-8")

    (lint.parent / BASELINE.name).write_text(
        json.dumps({"files": {
            "src/cofounder_agent/poindexter/services/settings_categories.py": {
                "docs/some_deleted_doc.md": 1,
            },
        }}),
        encoding="utf-8")

    r = _run(tmp_path, script=lint)
    assert r.returncode == 0, f"unexpected regression:\n{r.stdout}\n{r.stderr}"
    assert "1 found" in r.stdout
    assert "1 baselined" in r.stdout
    assert "re-baseline" not in r.stdout
