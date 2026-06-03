"""RenderPrometheusRulesJob — keep Prometheus's rules in sync with app_settings.

Every 5 minutes (default), this job:

1. Loads current thresholds + rule overrides from ``app_settings``
2. Renders them to the YAML format Prometheus expects
3. Compares byte-for-byte with the file on disk; writes only on change
4. If anything changed and ``reload_on_change`` is true, POSTs
   ``/-/reload`` to Prometheus so new rules take effect without a
   container restart

The output path and the Prometheus URL are both plugin config — see
``brain/seed_app_settings.json`` for the default seed. The rules file
lives on a volume shared between the worker (writer) and Prometheus
(reader); ``docker-compose.local.yml`` wires the volume.

Config (``plugin.job.render_prometheus_rules``):
- ``config.output_path`` (default ``/etc/prometheus/rules/dynamic.yml``)
- ``config.prometheus_url`` (default ``http://prometheus:9090``)
- ``config.reload_on_change`` (default true)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from plugins.job import JobResult
from services.prometheus_rule_builder import build_current

logger = logging.getLogger(__name__)


class RenderPrometheusRulesJob:
    name = "render_prometheus_rules"
    description = "Render prometheus.rule.* + threshold.* settings to a Prometheus rules file and reload"
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        output_path = Path(
            config.get("output_path", "/etc/prometheus/rules/dynamic.yml")
        )
        prometheus_url = str(
            config.get("prometheus_url", "http://prometheus:9090")
        ).rstrip("/")
        reload_on_change = bool(config.get("reload_on_change", True))

        try:
            rendered = await build_current(pool)
        except Exception as e:
            logger.exception("render_prometheus_rules: build failed: %s", e)
            return JobResult(ok=False, detail=f"build failed: {e}", changes_made=0)

        existing: str | None = None
        try:
            if output_path.is_file():
                existing = output_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("render_prometheus_rules: could not read %s: %s", output_path, e)

        if existing == rendered:
            return JobResult(
                ok=True,
                detail="rules unchanged",
                changes_made=0,
                metrics={"bytes": len(rendered)},
            )

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except OSError as e:
            logger.exception("render_prometheus_rules: write failed: %s", e)
            return JobResult(
                ok=False, detail=f"write failed: {e}", changes_made=0
            )

        reload_ok = True
        reload_detail = "written (reload skipped)"
        if reload_on_change:
            reload_ok, reload_detail = await _reload_prometheus(prometheus_url)

        # A failed reload means the new rules were written to disk but are NOT
        # live — Prometheus is still serving the old rules, so updated alert
        # thresholds silently never deploy. Surface ok=False so the scheduler
        # escalates (this job is in _CIRCULAR_SAFE_JOBS → direct critical page),
        # matching the render_alertmanager_config sibling. The write still
        # happened (changes_made=1) so the next run won't re-write. (audit M3)
        return JobResult(
            ok=reload_ok,
            detail=f"rules updated; {reload_detail}",
            changes_made=1,
            metrics={"bytes": len(rendered)},
        )


async def _reload_prometheus(base_url: str) -> tuple[bool, str]:
    """POST /-/reload; return ``(ok, short status string for JobResult.detail)``.

    ``ok`` is False on any non-200 (e.g. 403 when ``--web.enable-lifecycle``
    isn't set) or transport error, so the caller can propagate the failure
    instead of reporting a silent success while Prometheus keeps the old rules.
    """
    url = f"{base_url}/-/reload"
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.post(url)
        if resp.status_code == 200:
            return True, "prometheus reloaded"
        # 403 means --web.enable-lifecycle is not set on prometheus.
        return False, f"reload returned {resp.status_code}"
    except httpx.HTTPError as e:
        logger.warning("render_prometheus_rules: reload failed: %s", e)
        return False, f"reload failed: {e}"
