"""scripts/ci/pipe_to_grep_lint.py -- the classifier shape that lies on big inputs."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "pipe_to_grep_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location("pipe_to_grep_lint_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_repo_is_clean():
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.unit
def test_flags_the_pipe_shape_and_accepts_the_here_string(tmp_path: Path):
    mod = _load()
    bad = tmp_path / "bad.yml"
    bad.write_text('run: |\n  if echo "$changed" | grep -qE "^src/"; then echo hit; fi\n', encoding="utf-8")
    good = tmp_path / "good.yml"
    good.write_text('run: |\n  if grep -qE "^src/" <<<"$changed"; then echo hit; fi\n  # echo "$x" | grep -q in a comment is fine\n', encoding="utf-8")
    assert [ln for ln, _ in mod._offenders(bad)] == [2]
    assert mod._offenders(good) == []


@pytest.mark.unit
def test_shell_scripts_without_pipefail_are_not_flagged():
    """Without pipefail the pipeline status is grep's, so the shape is safe there."""
    mod = _load()
    assert mod.PIPEFAIL.search("set -euo pipefail\n")
    assert mod.PIPEFAIL.search("set -o pipefail\n")
    assert not mod.PIPEFAIL.search("set -eu\n")


@pytest.mark.unit
def test_the_bug_really_happens_in_bash():
    """Reproduce the failure mode the lint exists for, so the rule stays earned:
    a large list piped into grep -q under pipefail reports NO MATCH even though
    a matching line is present; the here-string form matches."""
    script = r"""
set -uo pipefail
changed=$(python3 -c 'print("\n".join([".github/x.yml"]*5 + ["src/cofounder_agent/poindexter/services/module_%05d.py" % i for i in range(3000)]))')
if echo "$changed" | grep -qE "^src/cofounder_agent/"; then echo pipe=match; else echo pipe=nomatch; fi
if grep -qE "^src/cofounder_agent/" <<<"$changed"; then echo here=match; else echo here=nomatch; fi
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    out = proc.stdout.split()
    assert "here=match" in out, proc.stdout + proc.stderr
    assert "pipe=nomatch" in out, "bash no longer reproduces the SIGPIPE flip -- re-examine whether this lint still earns its keep"


# --- the producer was never the point (stack#3779) --------------------------
#
# This lint originally matched `echo "$var" | grep -q` only. The offsite
# coverage check used `printf '%s\n' "${listing}" | grep -qxF`, so the lint
# passed clean while the identical hazard shipped: a 23,763-line listing made a
# PRESENT bootstrap.toml read as missing and paged CRITICAL on the backup a
# restore depends on.
#
# A guard that matches one spelling of a hazard is not coverage of the hazard.


@pytest.mark.unit
@pytest.mark.parametrize(
    "line,caught,why",
    [
        (
            """        if ! printf '%s\\n' "${listing}" | grep -qxF "${artifact}"; then""",
            True,
            "printf producer — the shape that actually shipped (stack#3779)",
        ),
        (
            '''    if echo "$changed" | grep -q foo; then''',
            True,
            "echo producer — the original shape (stack#3652)",
        ),
        (
            '''    if printf '%s\\n' "${listing}" | grep -q "$needle"; then''',
            True,
            "printf with a variable needle too",
        ),
        (
            """if ! docker ps --format '{{.Names}}' | grep -q poindexter; then""",
            False,
            "fixed-output command: far under the 64 KB pipe buffer, cannot SIGPIPE",
        ),
        (
            """    if uname -s | grep -qiE 'mingw|msys'; then""",
            False,
            "same — no variable, tiny output",
        ),
        (
            '''    if grep -qxF "${artifact}" <<<"${listing}"; then''',
            False,
            "the here-string fix must not be flagged",
        ),
    ],
)
def test_pattern_matches_the_hazard_not_one_spelling(line, caught, why):
    mod = _load()
    assert bool(mod.PATTERN.search(line)) is caught, why
