"""``deploy-checkout-sync.sh`` holds EVERY restart for a busy stack, not only the reset.

poindexter#1068: 2026-09-22 02:20 the deploy clone had been fast-forwarded by
hand, so the pass logged "Already at origin/main; no reset needed", skipped the
gap wait (which guarded only ``git reset``) and bounced poindexter-worker 43 s
into a media render — with ``media_render_running`` true the whole time.

Run against the real script with the rig from ``test_deploy_checkout_sync_retry``
(throwaway origin + clone, recorder fakes on PATH). ``docker exec`` answers the
busy probes' ``count(*)`` with ``FAKE_BUSY_COUNT`` and ``curl`` reports no
Prefect flow, so "busy" here is the media-render / GPU-lock signal.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.unit.scripts.test_deploy_checkout_sync_retry import (
    _advance_origin,
    _build_rig,
    _git,
    _record,
    _repo_root,
    _restarts,
    _status,
)

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("bash") is None, reason="needs bash + git",
)

# Same recorder as the retry rig's fake, plus an answer for the busy probes.
_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-} ${2:-}" in
  "container inspect")
    [[ "$*" == *"-f"* ]] && echo "${FAKE_STARTED_AT:-2020-01-01T00:00:00.000000000Z}"
    exit 0 ;;
esac
if [[ "${1:-}" == exec ]]; then echo "${FAKE_BUSY_COUNT:-0}"; fi
exit 0
"""


def _rig(tmp_path):
    rig = _build_rig(tmp_path)
    for name, body in (("docker", _FAKE_DOCKER), ("curl", "#!/usr/bin/env bash\necho '[]'\n")):
        f = rig["bin"] / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    return rig


def _run(rig, **env_extra):
    """A normal timer pass — WITH the flow check, a zero-second gap wait."""
    return subprocess.run(
        ["bash", str(_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh")],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["home"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]),
            "EVENTS_FILE": str(rig["events"]),
            "SYNC_APPLY_RETRY_SETTLE_SEC": "0",
            "SYNC_FLOW_WAIT_MAX_SEC": "0",
            **env_extra,
        },
        capture_output=True, text=True, timeout=180,
    )


def _hand_fast_forward(rig):
    """What the operator did: FF the deploy clone so the next pass sees 0 behind."""
    _git(rig["clone"], "fetch", "-q", "origin")
    _git(rig["clone"], "reset", "-q", "--hard", "origin/main")


class TestNoResetPathIsGuarded:
    def test_busy_stack_defers_the_bounce_when_the_clone_is_already_current(self, tmp_path):
        rig = _rig(tmp_path)
        _advance_origin(rig)
        _hand_fast_forward(rig)
        proc = _run(rig, FAKE_BUSY_COUNT="1")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _restarts(rig) == [], "a busy stack must not be bounced"
        assert _status(rig)["result"] == "deferred-active-flow"
        assert "already current" in _status(rig)["detail"]
        assert _record(rig, "deploy-last-bounced-sha") is None
        assert _record(rig, "deploy-last-restarted-sha") == rig["base_sha"]

    def test_the_next_idle_pass_deploys(self, tmp_path):
        rig = _rig(tmp_path)
        new = _advance_origin(rig)
        _hand_fast_forward(rig)
        _run(rig, FAKE_BUSY_COUNT="1")
        proc = _run(rig, FAKE_BUSY_COUNT="0")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert sorted(_restarts(rig)) == [
            "docker restart poindexter-pipeline-bot", "docker restart poindexter-worker",
        ]
        assert _record(rig, "deploy-last-restarted-sha") == new

    def test_force_still_forces(self, tmp_path):
        rig = _rig(tmp_path)
        _advance_origin(rig)
        _hand_fast_forward(rig)
        proc = _run(rig, FAKE_BUSY_COUNT="1", SYNC_FLOW_FORCE="1")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert len(_restarts(rig)) == 2


class TestResetPathUnchanged:
    def test_busy_stack_still_defers_the_reset(self, tmp_path):
        rig = _rig(tmp_path)
        _advance_origin(rig)
        proc = _run(rig, FAKE_BUSY_COUNT="1")
        assert proc.returncode == 0
        assert _restarts(rig) == []
        assert _status(rig)["result"] == "deferred-active-flow"
        assert "before reset" in _status(rig)["detail"]
        # The clone was NOT reset onto the new tree.
        assert _git(rig["clone"], "rev-parse", "HEAD") == rig["base_sha"]

    def test_idle_reset_pass_is_not_double_guarded_into_a_deferral(self, tmp_path):
        rig = _rig(tmp_path)
        _advance_origin(rig)
        proc = _run(rig, FAKE_BUSY_COUNT="0")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert len(_restarts(rig)) == 2
        assert _status(rig)["result"] == "deployed"
