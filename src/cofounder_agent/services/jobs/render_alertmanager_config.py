"""RenderAlertmanagerConfigJob — render alertmanager.yml.tmpl with the chat_id.

Delivery-plane dead-man's switch (Glad-Labs/poindexter#524).

``infrastructure/prometheus/alertmanager.yml.tmpl`` is synced to the PUBLIC
mirror, so it carries a ``${ALERTMANAGER_TELEGRAM_CHAT_ID}`` placeholder
instead of a real Telegram chat_id. Every 5 minutes (default) this job:

1. Reads the template from disk
2. Substitutes the placeholder with ``app_settings.telegram_chat_id``
   (read via the SiteConfig DI seam — chat_id is NON-secret, so
   ``site_config.get`` is correct, NOT ``get_secret``)
3. Compares byte-for-byte with the rendered file on the shared volume;
   writes only on change
4. If anything changed and ``reload_on_change`` is true, POSTs
   ``/-/reload`` to Alertmanager so the new config takes effect without a
   container restart

This is the same shape as ``RenderPrometheusRulesJob`` — it reuses that
job's reload helper pattern and the shared-volume hand-off (worker writes,
the consuming container reads). The dead-man's switch itself does NOT
depend on this job: if the render never runs, Alertmanager boots on the
minimal seed config the compose entrypoint drops in place, and the static
Prometheus rule still fires — it just can't reach the native-Telegram
receiver until the chat_id is rendered in.

Config (``plugin.job.render_alertmanager_config``):
- ``config.template_path`` (default ``/etc/alertmanager/alertmanager.yml.tmpl``)
- ``config.output_path`` (default ``/etc/alertmanager/config/alertmanager.yml``)
- ``config.alertmanager_url`` (default ``http://alertmanager:9093``)
- ``config.reload_on_change`` (default true)
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from plugins.job import JobResult

logger = logging.getLogger(__name__)

# The literal placeholder in alertmanager.yml.tmpl. Kept as a module
# constant so the unit test can assert it is fully substituted.
CHAT_ID_PLACEHOLDER = "${ALERTMANAGER_TELEGRAM_CHAT_ID}"


def render_template(template: str, chat_id: str) -> str:
    """Substitute the chat_id placeholder in the template body.

    Pure function (no I/O) so it is trivially unit-testable. Replaces every
    occurrence of ``${ALERTMANAGER_TELEGRAM_CHAT_ID}`` with ``chat_id``.
    """
    return template.replace(CHAT_ID_PLACEHOLDER, chat_id)


class RenderAlertmanagerConfigJob:
    name = "render_alertmanager_config"
    description = (
        "Render alertmanager.yml.tmpl with telegram_chat_id from app_settings "
        "and reload Alertmanager (dead-man's switch, #524)"
    )
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        template_path = Path(
            config.get(
                "template_path", "/etc/alertmanager/alertmanager.yml.tmpl"
            )
        )
        output_path = Path(
            config.get("output_path", "/etc/alertmanager/config/alertmanager.yml")
        )
        alertmanager_url = str(
            config.get("alertmanager_url", "http://alertmanager:9093")
        ).rstrip("/")
        reload_on_change = bool(config.get("reload_on_change", True))

        # DI seam (glad-labs-stack#330). chat_id is non-secret → sync .get().
        sc = config.get("_site_config")
        chat_id = str(sc.get("telegram_chat_id", "")) if sc is not None else ""
        if not chat_id:
            # Fail loud per feedback_no_silent_defaults — without a chat_id
            # the native-Telegram receiver can't deliver. Don't write a
            # half-rendered config that would leave the placeholder in
            # place (Alertmanager would reject it).
            return JobResult(
                ok=False,
                detail=(
                    "telegram_chat_id unset in app_settings — dead-man's-switch "
                    "Telegram delivery cannot be configured; run "
                    "`poindexter settings set telegram_chat_id <id>`"
                ),
                changes_made=0,
            )

        try:
            template = template_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.exception(
                "render_alertmanager_config: template read failed: %s", e
            )
            return JobResult(
                ok=False, detail=f"template read failed: {e}", changes_made=0
            )

        rendered = render_template(template, chat_id)

        existing: str | None = None
        try:
            if output_path.is_file():
                existing = output_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(
                "render_alertmanager_config: could not read %s: %s",
                output_path,
                e,
            )

        if existing == rendered:
            return JobResult(
                ok=True,
                detail="alertmanager config unchanged",
                changes_made=0,
                metrics={"bytes": len(rendered)},
            )

        # Atomic write via a temp file in the SAME dir + os.replace. The
        # worker runs non-root, but the shared volume's seed file
        # (alertmanager.yml) is created root-owned 0644 by the alertmanager
        # entrypoint, so a direct truncating write_text() hits EACCES. A
        # rename only needs write+execute on the (0777, non-sticky) parent
        # dir — not write access to the existing target — so it replaces the
        # root-owned seed cleanly, and the swap is atomic (no torn reads by
        # Alertmanager mid-write).
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(output_path.parent),
                prefix=".alertmanager.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(rendered)
                # mkstemp creates the temp 0600 (owner-only). Alertmanager
                # runs as a different uid (root today, but don't bank on it),
                # so a shared config it must READ has to be world-readable —
                # otherwise a future non-root alertmanager silently can't load
                # its config. Widen to 0644 before the atomic swap.
                os.chmod(tmp_name, 0o644)
                os.replace(tmp_name, output_path)
            except OSError:
                # Don't leave the temp file behind on a failed swap.
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.exception("render_alertmanager_config: write failed: %s", e)
            return JobResult(
                ok=False, detail=f"write failed: {e}", changes_made=0
            )

        reload_ok = True
        reload_detail = "written (reload skipped)"
        if reload_on_change:
            reload_ok, reload_detail = await _reload_alertmanager(alertmanager_url)

        # A failed reload means the new config was written to disk but is NOT
        # live — Alertmanager is still running the old config, so the updated
        # chat_id / routing never took effect. That is a silent delivery-plane
        # failure; surface it as ok=False so the scheduler escalates it (this
        # job is in _CIRCULAR_SAFE_JOBS -> direct critical page). The write
        # still happened (changes_made=1) so the next run won't re-write.
        return JobResult(
            ok=reload_ok,
            detail=f"alertmanager config updated; {reload_detail}",
            changes_made=1,
            metrics={"bytes": len(rendered)},
        )


async def _reload_alertmanager(base_url: str) -> tuple[bool, str]:
    """POST /-/reload; return (ok, short status string for JobResult.detail)."""
    url = f"{base_url}/-/reload"
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.post(url)
        if resp.status_code == 200:
            return True, "alertmanager reloaded"
        # Alertmanager enables POST /-/reload by default (no flag needed);
        # a non-200 here means a malformed config or a version that lacks it.
        # The config on disk is NOT live — treat as failure, not success.
        logger.warning(
            "render_alertmanager_config: reload returned %s", resp.status_code
        )
        return False, f"reload returned {resp.status_code} (config NOT live)"
    except httpx.HTTPError as e:
        logger.warning("render_alertmanager_config: reload failed: %s", e)
        return False, f"reload failed: {e} (config NOT live)"
