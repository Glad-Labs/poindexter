"""RenderPrometheusRulesJob — keep Prometheus's rules in sync with app_settings.

Every 5 minutes (default), this job:

1. Loads current thresholds + rule overrides from ``app_settings``
2. Renders them to the YAML format Prometheus expects
3. Compares byte-for-byte with the file on disk; writes only on change
4. If anything changed and ``reload_on_change`` is true, POSTs
   ``/-/reload`` to Prometheus so new rules take effect without a
   container restart
5. Even on a *no-change* pass, if Prometheus reports its last ``/-/reload``
   failed (``prometheus_config_last_reload_successful == 0``), retries the
   reload — so a transient reload failure self-heals and is reported truthfully
   instead of being masked green by the next unchanged pass (#842)

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
from utils.exception_format import describe_exception

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
            return JobResult(ok=False, detail=f"build failed: {describe_exception(e)}", changes_made=0)

        existing: str | None = None
        try:
            if output_path.is_file():
                existing = output_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("render_prometheus_rules: could not read %s: %s", output_path, e)

        changed = existing != rendered

        if changed:
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(rendered, encoding="utf-8")
            except OSError as e:
                logger.exception("render_prometheus_rules: write failed: %s", e)
                return JobResult(
                    ok=False, detail=f"write failed: {describe_exception(e)}", changes_made=0
                )

        # Reloading disabled → we're only responsible for the on-disk file;
        # don't reload and don't probe Prometheus health.
        if not reload_on_change:
            return JobResult(
                ok=True,
                detail=(
                    "rules updated; written (reload skipped)"
                    if changed
                    else "rules unchanged"
                ),
                changes_made=1 if changed else 0,
                metrics={"bytes": len(rendered)},
            )

        # Decide whether a /-/reload is needed this pass:
        #   * content changed → always reload (write it and make it live).
        #   * no change → only when Prometheus reports its LAST /-/reload
        #     FAILED, i.e. the rules it is serving are stale versus what we
        #     already wrote. Without this retry a transient reload failure is
        #     masked green by the very next no-change pass — the file on disk
        #     and the rules Prometheus serves stay diverged with last_status=ok
        #     (#842; observed diverged ~25 min until a human intervened).
        #   `None` health (Prometheus unreachable) is NOT treated as stale — a
        #   liveness alert owns "Prometheus down", and reloading it would fail
        #   anyway; failing safe avoids a reload storm on an unreachable probe.
        stale_retry = False
        if not changed:
            stale_retry = await _prometheus_reload_healthy(prometheus_url) is False
            if not stale_retry:
                return JobResult(
                    ok=True,
                    detail="rules unchanged",
                    changes_made=0,
                    metrics={"bytes": len(rendered)},
                )

        reload_ok, reload_detail = await _reload_prometheus(prometheus_url)

        # A failed reload means the rules on disk are NOT live — Prometheus is
        # still serving the old rules, so updated alert thresholds silently
        # never deploy. Surface ok=False so the scheduler escalates (this job is
        # in _CIRCULAR_SAFE_JOBS → direct critical page after the
        # consecutive-failure threshold, #831), matching render_alertmanager_config.
        # On a content-change pass the write happened (changes_made=1) so the
        # next run won't re-write; a stale-retry pass wrote nothing (0). (audit M3)
        return JobResult(
            ok=reload_ok,
            detail=(
                f"rules updated; {reload_detail}"
                if changed
                else f"serving config stale — reload retried; {reload_detail}"
            ),
            changes_made=1 if changed else 0,
            metrics={"bytes": len(rendered)},
        )


async def _prometheus_reload_healthy(base_url: str) -> bool | None:
    """Ask Prometheus whether its last ``/-/reload`` succeeded.

    Truth-based staleness probe for #842 — no persisted flag, survives worker
    restarts. Reads the ``prometheus_config_last_reload_successful`` gauge via
    the instant-query API.

    Returns:
      * ``True``  — last reload succeeded; the rules Prometheus serves are
        current with what we wrote, so a no-change pass can stay a noop.
      * ``False`` — last reload FAILED; the served rules are stale versus disk
        and the caller should retry the reload.
      * ``None``  — the metric can't be read (Prometheus unreachable / query
        error). Deliberately NOT treated as stale: a Prometheus-down condition
        is owned by a separate liveness alert, and reloading a down Prometheus
        would fail regardless. Failing safe avoids a reload storm when only the
        query endpoint is transiently unreachable.
    """
    url = f"{base_url}/api/v1/query"
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(
                url,
                params={"query": "prometheus_config_last_reload_successful"},
            )
        if resp.status_code != 200:
            return None
        result = resp.json().get("data", {}).get("result", [])
        if not result:
            return None
        # instant-vector sample shape: {"value": [<unix_ts>, "<value>"]}
        return float(result[0]["value"][1]) == 1.0
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(
            "render_prometheus_rules: reload-health query failed: %s", e
        )
        return None


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
