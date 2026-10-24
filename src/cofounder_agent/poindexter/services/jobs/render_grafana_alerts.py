"""RenderGrafanaAlertsJob — keep Grafana SQL alert rules in sync with app_settings.

Every 5 minutes (default), this job:

1. Reads ``grafana.threshold.*`` from ``app_settings``
2. Substitutes them into the alert-rules template
3. Compares byte-for-byte with the on-disk file; writes only on change
4. If anything changed and ``reload_on_change`` is true, POSTs
   ``/api/admin/provisioning/alerting/reload`` to Grafana using the
   ``grafana_api_token`` service account token

Grafana reads alert-rule files only at start or on that reload; it does not
poll them. The reload needs ``provisioning:reload``, which Grafana OSS grants
only to the server admin, so a service-account token gets 403. A rule change
that arrives by deploy is reloaded by the deploy pass instead (step 7b of
``scripts/linux/deploy-checkout-sync.sh``, inside the Grafana container); a
threshold change made in app_settings applies at the next Grafana restart, and
the 403 detail says so.

The template (``alert-rules.yml.tmpl``) and output (``alert-rules.yml``)
live in the same bind-mounted directory, accessible to both the worker
(writer) and Grafana (reader) via ``docker-compose.local.yml``.

Config (``plugin.job.render_grafana_alerts``):
- ``config.template_path`` (default ``/etc/grafana-alerting/alert-rules.yml.tmpl``)
- ``config.output_path`` (default ``/etc/grafana-alerting/alert-rules.yml``)
- ``config.grafana_url`` (default ``http://grafana:3000``)
- ``config.reload_on_change`` (default true)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from poindexter.plugins.job import JobResult
from poindexter.services.grafana_alert_builder import build_current
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)


class RenderGrafanaAlertsJob:
    name = "render_grafana_alerts"
    description = "Render grafana.threshold.* settings to alert-rules.yml and reload"
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        template_path = Path(
            config.get("template_path", "/etc/grafana-alerting/alert-rules.yml.tmpl")
        )
        output_path = Path(
            config.get("output_path", "/etc/grafana-alerting/alert-rules.yml")
        )
        grafana_url = str(config.get("grafana_url", "http://grafana:3000")).rstrip("/")
        reload_on_change = bool(config.get("reload_on_change", True))

        if not template_path.is_file():
            logger.warning(
                "render_grafana_alerts: template not found: %s", template_path
            )
            return JobResult(
                ok=False, detail=f"template not found: {template_path}", changes_made=0
            )

        try:
            rendered = await build_current(pool, template_path)
        except Exception as e:
            logger.exception("render_grafana_alerts: build failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=f"build failed: {describe_exception(e)}", changes_made=0)

        existing: str | None = None
        try:
            if output_path.is_file():
                existing = output_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(
                "render_grafana_alerts: could not read %s: %s", output_path, describe_exception(e)
            )

        if existing == rendered:
            return JobResult(
                ok=True,
                detail="alert rules unchanged",
                changes_made=0,
                metrics={"bytes": len(rendered)},
            )

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except OSError as e:
            logger.exception("render_grafana_alerts: write failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=f"write failed: {describe_exception(e)}", changes_made=0)

        reload_ok = True
        reload_detail = "written (reload skipped)"
        if reload_on_change:
            api_token = await _get_grafana_token(pool)
            if api_token:
                reload_ok, reload_detail = await _reload_grafana(grafana_url, api_token)
            else:
                reload_detail = (
                    "written; reload skipped (grafana_api_token not configured) "
                    "— the rules apply at the next Grafana restart"
                )

        return JobResult(
            ok=reload_ok,
            detail=f"alert rules updated; {reload_detail}",
            changes_made=1,
            metrics={"bytes": len(rendered)},
        )


async def _get_grafana_token(pool: Any) -> str:
    """Return the decrypted ``grafana_api_token``, or empty string.

    The token is a secret row stored as ``enc:v1:...``. Reading the column
    directly sent that ciphertext as the Bearer header, so every reload
    failed with "Illegal header value" (2026-10-23/24) and only Grafana's
    own file polling picked the rules up. ``plugins.secrets.get_secret``
    decrypts it, and reads a legacy plaintext row unchanged.
    """
    from poindexter.plugins.secrets import get_secret

    try:
        async with pool.acquire() as conn:
            value = await get_secret(conn, "grafana_api_token")
        if value:
            return str(value).strip()
    except Exception as e:
        logger.warning(
            "render_grafana_alerts: could not read grafana_api_token: %s",
            describe_exception(e),
        )
    return ""


async def _reload_grafana(base_url: str, api_token: str) -> tuple[bool, str]:
    """POST /api/admin/provisioning/alerting/reload; return ``(ok, detail)``."""
    url = f"{base_url}/api/admin/provisioning/alerting/reload"
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.post(
                url, headers={"Authorization": f"Bearer {api_token}"}
            )
        if resp.status_code == 200:
            return True, "grafana alerting reloaded"
        if resp.status_code == 403:
            return False, (
                "reload returned 403: the token lacks provisioning:reload, which "
                "Grafana OSS grants only to the server admin. The rules apply at "
                "the next Grafana restart, or now with: docker exec poindexter-grafana "
                "sh -c 'curl -s -X POST -u \"$GF_SECURITY_ADMIN_USER:$GF_SECURITY_ADMIN_PASSWORD\" "
                "http://localhost:3000/api/admin/provisioning/alerting/reload'"
            )
        return False, f"reload returned {resp.status_code}"
    except httpx.HTTPError as e:
        # Name the error class only: an httpx header error quotes the header
        # value, which here is the API token.
        logger.warning("render_grafana_alerts: reload failed: %s", type(e).__name__)
        return False, f"reload failed: {type(e).__name__}"
