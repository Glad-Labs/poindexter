"""Contract tests for ``deploy-checkout-sync.sh`` step 6c — game-mode re-park.

``start-stack.sh up -d --no-build`` starts every project service, including
the GPU sidecars ``poindexter game on`` parked (they sit ``exited``, which
compose reads as "start me"). Live 2026-09-15: the 04:30 deploy woke
wan-server, speaches and chatterbox mid-game; the brain re-parked them 13 s
later only because it had just learned to. The deploy must not wake them in
the first place — or rather, must put back what it woke, after the health
gate so a rebuilt sidecar still has to come up healthy first.

Text-contract tests (the full rig lives in the sibling apply/retry tests): the
block exists, sits after the health gate, reads the same keys as
``services/game_mode.py``, and its default parked list cannot drift from the
Python constant.
"""
from __future__ import annotations

from pathlib import Path

from poindexter.services import game_mode


def _script() -> str:
    root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "deploy-checkout-sync.sh").exists()
    )
    return (root / "scripts" / "linux" / "deploy-checkout-sync.sh").read_text(encoding="utf-8")


def test_repark_block_runs_after_the_health_gate():
    s = _script()
    gate = s.index("# ---- health gate: did what we just rebuilt")
    repark = s.index("# ---- game-mode re-park (step 6c)")
    connector = s.index("# ---- claude.ai-connector sync")
    assert gate < repark < connector


def test_repark_reads_the_same_keys_as_game_mode_py():
    s = _script()
    assert "key='game_mode_until'" in s
    assert "NULLIF(value,'')::timestamptz > now()" in s
    assert f"game_mode_setting {game_mode.PARKED_SERVICES_KEY} " in s
    assert f"game_mode_setting {game_mode.CONTAINER_PREFIX_KEY} {game_mode.CONTAINER_PREFIX_DEFAULT}" in s


def test_repark_default_list_matches_the_python_constant():
    s = _script()
    default = game_mode.PARKED_SERVICES_DEFAULT
    expected = default if isinstance(default, str) else ",".join(default)
    assert f'game_mode_setting game_mode_parked_services "{expected}"' in s, (
        "the script's fallback parked list must equal game_mode.PARKED_SERVICES_DEFAULT"
    )


def test_repark_only_stops_running_containers_and_never_blocks_the_deploy():
    s = _script()
    block = s[s.index("# ---- game-mode re-park (step 6c)"):s.index("# ---- claude.ai-connector sync")]
    assert "docker inspect -f '{{.State.Running}}'" in block
    assert "docker stop -t 20" in block
    assert "2>/dev/null" in block  # DB unreachable ⇒ not in game mode, deploy proceeds
    assert "exit " not in block  # nothing in the step aborts the pass
