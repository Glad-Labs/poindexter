"""``deploy-checkout-sync.sh`` step 7b reloads Grafana's alert rules on change.

Grafana reads file-provisioned alert rules only at start or on
``POST /api/admin/provisioning/alerting/reload``, which needs
``provisioning:reload``. Grafana OSS grants that to the server admin only, so
the worker's ``render_grafana_alerts`` job, which holds a service-account token,
got 403 and the rule change waited for the next Grafana restart. On 2026-10-24
the Brain Daemon Stale fix sat unapplied that way.

Contract, against the real script in a throwaway origin + clone (rig from
``test_deploy_checkout_sync_retry.py``):

- a tree that changes a file under ``infrastructure/grafana/provisioning/alerting``
  runs the reload inside the Grafana container, using the container's own admin
  credentials (they are expanded in there, never on the host);
- a tree that changes nothing there does not touch Grafana;
- a failed reload is a status note, never a failed pass or a withheld marker;
- no Grafana container means nothing to reload, and the pass is still clean.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.scripts.test_deploy_checkout_sync_retry import (
    _advance_origin,
    _build_rig,
    _events,
    _record,
    _run_sync,
    _status,
)

_ALERT_RULE = "infrastructure/grafana/provisioning/alerting/alert-rules.yml"

# The retry rig's docker fake, plus: `docker exec <grafana> sh -c <cmd>` prints
# FAKE_RELOAD_CODE (what curl's -w prints), and `container inspect <grafana>`
# fails when FAKE_NO_GRAFANA is set.
_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-} ${2:-}" in
  "inspect -f") echo true; exit 0 ;;
  "exec poindexter-grafana") printf '%s' "${FAKE_RELOAD_CODE:-200}"; exit 0 ;;
  "container inspect")
    if [ -n "${FAKE_NO_GRAFANA:-}" ] && [ "${@: -1}" = "poindexter-grafana" ]; then exit 1; fi
    if [[ "$*" == *"-f"* ]]; then
      c="${@: -1}"
      if [ -n "${STARTED_DIR:-}" ] && [ -f "$STARTED_DIR/$c" ]; then cat "$STARTED_DIR/$c"
      else echo "${FAKE_STARTED_AT:-2020-01-01T00:00:00.000000000Z}"; fi
    fi
    exit 0 ;;
esac
exit 0
"""


def _rig(tmp_path: Path) -> dict:
    rig = _build_rig(tmp_path)
    docker = rig["bin"] / "docker"
    docker.write_text(_FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    return rig


def _reloads(rig: dict) -> list[str]:
    return [e for e in _events(rig) if e.startswith("docker exec poindexter-grafana ")]


class TestGrafanaAlertReload:
    def test_alerting_change_reloads_inside_the_container(self, tmp_path):
        rig = _rig(tmp_path)
        new = _advance_origin(rig, {_ALERT_RULE: "groups: []\n"})
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stderr
        reloads = _reloads(rig)
        assert len(reloads) == 1
        assert "/api/admin/provisioning/alerting/reload" in reloads[0]
        # The credentials are referenced, not substituted: the host never sees them.
        assert "${GF_SECURITY_ADMIN_PASSWORD}" in reloads[0]
        assert _record(rig, "deploy-last-restarted-sha") == new
        assert "grafana" not in _status(rig)["detail"]

    def test_other_changes_do_not_touch_grafana(self, tmp_path):
        rig = _rig(tmp_path)
        _advance_origin(rig)  # a services/ change only
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stderr
        assert _reloads(rig) == []

    def test_failed_reload_is_a_note_not_a_failed_pass(self, tmp_path):
        rig = _rig(tmp_path)
        new = _advance_origin(rig, {_ALERT_RULE: "groups: []\n"})
        proc = _run_sync(rig, FAKE_RELOAD_CODE="403")
        assert proc.returncode == 0, proc.stderr
        assert _record(rig, "deploy-last-restarted-sha") == new
        assert _status(rig)["result"] == "deployed"
        assert "grafana alert reload returned '403'" in _status(rig)["detail"]

    def test_no_grafana_container_is_still_a_clean_pass(self, tmp_path):
        rig = _rig(tmp_path)
        new = _advance_origin(rig, {_ALERT_RULE: "groups: []\n"})
        proc = _run_sync(rig, FAKE_NO_GRAFANA="1")
        assert proc.returncode == 0, proc.stderr
        assert _reloads(rig) == []
        assert _record(rig, "deploy-last-restarted-sha") == new
