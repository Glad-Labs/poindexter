"""Unit tests for ``scripts/postiz_upgrade_check.py``.

The script gates a Postiz image bump on its Prisma models agreeing with its
bundled Mastra schemas (Glad-Labs/poindexter#1091). The probe itself runs in
the image under Docker, so these tests pin the Python side with canned probe
output: the verdict, the parser, and above all that a probe which compared
nothing, or could not run, is never reported as a pass.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "scripts" / "postiz_upgrade_check.py").exists()
)
SCRIPT_PATH = REPO_ROOT / "scripts" / "postiz_upgrade_check.py"

_spec = importlib.util.spec_from_file_location("postiz_upgrade_check", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
check_mod = importlib.util.module_from_spec(_spec)
sys.modules["postiz_upgrade_check"] = check_mod
_spec.loader.exec_module(check_mod)

IMAGE = "ghcr.io/gitroomhq/postiz-app:vX"


def _line(table: str, *, modeled: bool = True, churn: list[str] | None = None) -> str:
    return json.dumps(
        {
            "table": table,
            "modeled": modeled,
            "mastra": 10,
            "prisma": 10 if modeled else 0,
            "churn": churn or [],
            "prisma_only": [],
        }
    )


def _runner(stdout: str = "", returncode: int = 0, stderr: str = ""):
    calls: list[list[str]] = []

    def run(cmd):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_aligned_image_passes(capsys: pytest.CaptureFixture[str]) -> None:
    out = "\n".join([_line("mastra_ai_spans"), _line("mastra_scorers")])
    assert check_mod.main([IMAGE], runner=_runner(out)) == 0
    assert "OK: no column churn" in capsys.readouterr().out


def test_drifting_image_fails_and_names_the_columns(capsys: pytest.CaptureFixture[str]) -> None:
    # The v2.21.10 shape: spans churns (abridged here), scorers churns one.
    out = "\n".join(
        [
            _line("mastra_ai_spans", churn=["entityType", "requestContext"]),
            _line("mastra_scorers", churn=["requestContext"]),
            _line("mastra_threads"),
        ]
    )
    assert check_mod.main([IMAGE], runner=_runner(out)) == 1
    printed = capsys.readouterr().out
    assert "DRIFT mastra_ai_spans: 2 column(s)" in printed
    assert "entityType, requestContext" in printed
    assert "DRIFT mastra_scorers: 1 column(s)" in printed
    assert "DRIFT mastra_threads" not in printed


def test_unmodeled_tables_are_reported_but_never_fail() -> None:
    rows = check_mod.parse_probe_output(
        "\n".join([_line("mastra_ai_spans"), _line("mastra_harness_sessions", modeled=False)])
    )
    assert check_mod.verdict(rows) == 0


def test_comparing_zero_tables_is_not_a_pass(capsys: pytest.CaptureFixture[str]) -> None:
    # An image whose layout moved can yield only unmodeled tables, or nothing
    # at all. Neither may read as "OK".
    only_unmodeled = _line("mastra_agents", modeled=False)
    assert check_mod.main([IMAGE], runner=_runner(only_unmodeled)) == 2
    assert check_mod.main([IMAGE], runner=_runner("")) == 2
    assert "compared 0 tables" in capsys.readouterr().err


def test_probe_failure_is_not_a_pass(capsys: pytest.CaptureFixture[str]) -> None:
    runner = _runner(stdout="", returncode=127, stderr='exec: "node": executable file not found')
    assert check_mod.main(["redis:7-alpine"], runner=runner) == 2
    assert "probe exited 127" in capsys.readouterr().err


def test_docker_missing_is_not_a_pass() -> None:
    def no_docker(cmd):
        raise FileNotFoundError("docker")

    assert check_mod.main([IMAGE], runner=no_docker) == 2


def test_garbage_output_is_not_a_pass() -> None:
    assert check_mod.main([IMAGE], runner=_runner("not json\n")) == 2


def test_probe_runs_offline_against_the_named_image() -> None:
    runner = _runner(_line("mastra_ai_spans"))
    check_mod.main([IMAGE], runner=runner)
    (cmd,) = runner.calls
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert cmd[cmd.index("--network") + 1] == "none"
    assert IMAGE in cmd
    assert cmd[-2:] == [check_mod.MASTRA_STORAGE_MODULE, check_mod.PRISMA_SCHEMA]
