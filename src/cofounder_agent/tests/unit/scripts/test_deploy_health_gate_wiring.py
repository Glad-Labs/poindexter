"""The deploy sync must run the health gate around every rebuild and after every bounce."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SYNC = REPO_ROOT / "scripts" / "linux" / "deploy-checkout-sync.sh"
GATE = REPO_ROOT / "scripts" / "linux" / "deploy_health_gate.py"


def _text() -> str:
    return SYNC.read_text(encoding="utf-8")


def test_gate_script_exists_and_is_referenced():
    assert GATE.is_file()
    assert 'HEALTH_GATE="$DEPLOY_DIR/scripts/linux/deploy_health_gate.py"' in _text()


def test_snapshot_is_taken_before_the_rebuild_and_verify_runs_after_apply():
    text = _text()
    snap = text.index('"$HEALTH_GATE" snapshot')
    build = text.index('start-stack.sh" build $rebuild_services')
    apply_ = text.index('start-stack.sh" up -d --no-build')
    verify = text.index('"$HEALTH_GATE" "${gate_args[@]}"')
    assert snap < build < apply_ < verify


def test_rollback_marker_blocks_a_rebuild_of_the_same_sha():
    text = _text()
    assert 'printf \'%s %s\\n\' "$head_sha" "$gate_rolled_back" > "$ROLLBACK_MARKER_FILE"' in text
    assert 'if [ "$rb_sha" = "$head_sha" ]' in text
    assert text.index('if [ "$rb_sha" = "$head_sha" ]') < text.index('start-stack.sh" build $rebuild_services')


def test_bounced_containers_are_verified_by_name():
    assert 'gate_units="${gate_units:+$gate_units }container:$c"' in _text()


def test_no_gate_flag_exists():
    assert "--no-gate) NO_GATE=1 ;;" in _text()
