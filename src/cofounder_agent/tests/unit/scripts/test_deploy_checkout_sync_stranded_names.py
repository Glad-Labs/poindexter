"""``deploy-checkout-sync.sh`` renames containers a dying recreate stranded.

compose recreates a container by renaming the old one to ``<id>_<name>``
while the replacement comes up. On 2026-10-14 the first compose-apply lost
the dependency race mid-recreate and the RUNNING worker was left as
``5ad23a82015e_poindexter-worker``; the retry did not rename it, and the
script's own restart step then logged ``skip 'poindexter-worker' (not
present)``. Step 6a-ter renames such a container back when its real name is
free, and leaves it alone (logged) when the real name is taken.

Rig: ``test_deploy_checkout_sync_apply``'s, with a docker that lists
stranded names for the ``name=_poindexter-`` query and fails ``inspect`` for
names that do not exist.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.scripts.test_deploy_checkout_sync_apply import (
    _FAKE_DOCKER,
    _advance_origin,
    _build_rig,
    _events,
    _run_sync,
    pytestmark,  # noqa: F401  # same bash + git requirement
)

_STRANDED_DOCKER = _FAKE_DOCKER.replace(
    'case "${1:-}" in\n',
    'case "${1:-}" in\n'
    '  ps) case "$*" in *name=_poindexter-*) printf \'%s\' "${STRANDED_RUNNING:-}" | tr \',\' \'\\n\' ;; esac; exit 0 ;;\n'
    '  inspect) for n in ${MISSING_NAMES//,/ }; do [ "${@: -1}" = "$n" ] && exit 1; done; echo true; exit 0 ;;\n',
    1,
)


def _rig(tmp_path: Path) -> dict:
    rig = _build_rig(tmp_path)
    docker = rig["bin"] / "docker"
    docker.write_text(_STRANDED_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    _advance_origin(rig)
    return rig


def test_a_stranded_running_container_gets_its_name_back(tmp_path):
    rig = _rig(tmp_path)
    _run_sync(
        rig,
        STRANDED_RUNNING="5ad23a82015e_poindexter-worker,8a32c7362f55_poindexter-auto-embed,unrelated_thing",
        MISSING_NAMES="poindexter-worker,poindexter-auto-embed",
    )
    events = _events(rig)
    assert "docker rename 5ad23a82015e_poindexter-worker poindexter-worker" in events
    assert "docker rename 8a32c7362f55_poindexter-auto-embed poindexter-auto-embed" in events
    assert not any("rename unrelated_thing" in e for e in events)


def test_a_taken_real_name_is_left_for_compose(tmp_path):
    rig = _rig(tmp_path)
    proc = _run_sync(rig, STRANDED_RUNNING="5ad23a82015e_poindexter-worker", MISSING_NAMES="")
    assert not any(e.startswith("docker rename") for e in _events(rig))
    log = (rig["home"] / ".poindexter" / "deploy-checkout-sync.log").read_text(encoding="utf-8")
    assert "exists too; leaving both for compose" in log, proc.stdout[-2000:]
