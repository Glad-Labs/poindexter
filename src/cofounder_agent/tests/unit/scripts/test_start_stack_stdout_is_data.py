"""``scripts/start-stack.sh``'s stdout is DATA — pin that contract.

``deploy-checkout-sync.sh`` runs ``start-stack.sh ps --status=created
--format '{{.Name}}'`` and reads its stdout line by line as container names
for the stranded-container sweep. Anything else on stdout becomes a fake
"container".

That contract was never written down, so #3976 broke it: an informational
``echo "Grafana dashboard links will point at: …"`` in the preamble ran on
every invocation, ``ps`` included. deploy-sync then ran
``docker start "Grafana dashboard links will point at: <host>"``,
which failed, marked the pass incomplete, and exited 1 — every cycle, for
every deploy, until someone noticed (2026-09-23).

Two tests, one per layer:

1. The script itself never writes to stdout except into a file.
2. deploy-sync's guard, read out of the real script, still rejects a stray
   log line — so the NEXT stdout leak warns instead of halting the fleet.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "start-stack.sh").exists()
)
_START_STACK = _REPO_ROOT / "scripts" / "start-stack.sh"
_DEPLOY_SYNC = _REPO_ROOT / "scripts" / "linux" / "deploy-checkout-sync.sh"


def _echoes_reaching_stdout(src: str) -> list[tuple[int, str]]:
    """``echo`` lines that would land on stdout.

    Allowed: ``>&2`` (stderr), ``>>`` (append to a file), and anything inside
    a ``{ … } > file`` group, which is how the runtime env files are written.
    """
    offenders: list[tuple[int, str]] = []
    in_file_group = False
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == "{":
            # Look ahead to this group's close: does it redirect into a file?
            depth = 0
            for later in lines[i:]:
                t = later.strip()
                if t == "{":
                    depth += 1
                elif t.startswith("}"):
                    if depth == 0:
                        in_file_group = bool(re.match(r"^\}\s*>{1,2}\s*\S", t))
                        break
                    depth -= 1
            continue
        if stripped.startswith("}"):
            in_file_group = False
            continue
        if not re.match(r"^echo\b", stripped):
            continue
        if in_file_group or ">&2" in stripped or ">>" in stripped:
            continue
        offenders.append((i, stripped))
    return offenders


def test_start_stack_writes_nothing_to_stdout_but_files():
    offenders = _echoes_reaching_stdout(_START_STACK.read_text())
    assert not offenders, (
        "start-stack.sh stdout is consumed as DATA by deploy-checkout-sync "
        "(container names). Send diagnostics to stderr with `>&2`:\n"
        + "\n".join(f"  line {n}: {s}" for n, s in offenders)
    )


def test_the_detector_catches_the_line_that_broke_deploys():
    """The guard above is only worth something if it would have caught
    #3976. Feed it the exact regression."""
    regressed = (
        'if [ -n "$HOST" ]; then\n'
        '    echo "Grafana dashboard links will point at: $HOST"\n'
        "fi\n"
    )
    assert _echoes_reaching_stdout(regressed), "detector missed the #3976 line"


def test_the_detector_allows_the_env_file_group():
    """The runtime env file is written via `{ echo …; } > file` — that is
    file output, not stdout, and must not be flagged."""
    group = (
        "{\n"
        '    echo "# Auto-managed"\n'
        '    echo "TOKEN=$TOKEN"\n'
        '} > "$_RUNTIME_ENV"\n'
    )
    assert _echoes_reaching_stdout(group) == []


def _deploy_sync_name_regex() -> str:
    """Read the guard's regex out of the real script, so this test tracks
    the script rather than a copy of it."""
    m = re.search(r'\[\[ "\$c" =~ (\S+) \]\]', _DEPLOY_SYNC.read_text())
    assert m, "deploy-checkout-sync.sh lost its container-name guard"
    return m.group(1)


@pytest.mark.parametrize(
    ("line", "accepted"),
    [
        ("poindexter-worker", True),
        ("poindexter_grafana.1", True),
        ("Grafana dashboard links will point at: test-host.example.ts.net", False),
        ("ERROR: /home/x/.poindexter/bootstrap.toml not found.", False),
        ("", False),
    ],
)
def test_deploy_sync_guard_accepts_names_and_rejects_log_lines(line, accepted):
    regex = _deploy_sync_name_regex()
    result = subprocess.run(
        ["bash", "-c", f'[[ "$1" =~ {regex} ]]', "_", line],
        check=False,
    )
    assert (result.returncode == 0) is accepted
