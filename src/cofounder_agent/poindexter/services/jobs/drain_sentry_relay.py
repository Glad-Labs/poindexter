"""DrainSentryRelayJob — move queued site errors into the local GlitchTip.

The sentry-relay Cloudflare Worker queues error envelopes from the public
site's browsers and serverless functions; this job forwards them to GlitchTip
over the LAN (see ``services/sentry_relay.py``). GlitchTip issues then reach
the operator through the brain's GlitchTip triage probe.

Every 2 minutes, so an error on the site surfaces within one brain cycle
of the brain seeing it in GlitchTip.

Three findings, because each is a different way for site errors to stop
reaching anyone while every individual piece reports healthy:

* ``sentry_relay_drain_failed``: the relay or GlitchTip is unreachable, or
  the bearer secret is missing. Envelopes wait in the queue.
* ``sentry_relay_envelope_rejected``: GlitchTip refused envelopes outright,
  most likely a DSN key or project id that no longer matches the site's
  build. Those envelopes are gone, and every later error will follow them.
* ``sentry_relay_envelopes_expired``: the Worker pruned envelopes that sat
  past its retention window, so the drain was down for days.

No-op until ``sentry_relay_url`` is set.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

_SOURCE = "sentry_relay"


class DrainSentryRelayJob:
    name = "drain_sentry_relay"
    description = "Forward error envelopes queued at the edge relay into the local GlitchTip"
    schedule = "every 2 minutes"
    # Forwards and then acks. Two overlapping passes would forward the same
    # rows twice before either acked, so the scheduler must serialize.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        if not (site_config.get("sentry_relay_url", "") or "").strip():
            return JobResult(
                ok=True, detail="no relay configured — no-op", metrics={"configured": 0}
            )

        from poindexter.services.sentry_relay import drain_sentry_relay

        try:
            outcome = await drain_sentry_relay(site_config)
        except Exception as exc:
            logger.error(
                "[DrainSentryRelayJob] drain failed: %s",
                describe_exception(exc), exc_info=True,
            )
            emit_finding(
                source=_SOURCE,
                kind="sentry_relay_drain_failed",
                title="Site error relay drain failed",
                body=(
                    f"{describe_exception(exc)}\n\nQueued site errors stay at the "
                    "relay until the next successful pass."
                ),
                severity="warn",
                dedup_key="sentry_relay_drain_failed",
            )
            return JobResult(ok=False, detail=describe_exception(exc))

        if outcome.errors:
            emit_finding(
                source=_SOURCE,
                kind="sentry_relay_drain_failed",
                title=f"Site error relay drain hit {len(outcome.errors)} problem(s)",
                body=(
                    "\n".join(outcome.errors[:5])
                    + f"\n\n{outcome.backlog} envelope(s) still queued at the relay."
                ),
                severity="warn",
                dedup_key="sentry_relay_drain_errors",
            )
        if outcome.rejected:
            emit_finding(
                source=_SOURCE,
                kind="sentry_relay_envelope_rejected",
                title=f"GlitchTip refused {outcome.rejected} site error envelope(s)",
                body=(
                    "GlitchTip answered with a client error, so these were "
                    "acked and dropped. If every envelope is refused, the "
                    "site's DSN key or project id no longer matches GlitchTip.\n\n"
                    + "\n".join(outcome.rejections)
                ),
                severity="warn",
                dedup_key="sentry_relay_envelope_rejected",
            )
        if outcome.expired:
            emit_finding(
                source=_SOURCE,
                kind="sentry_relay_envelopes_expired",
                title=f"{outcome.expired} site error envelope(s) expired at the relay",
                body=(
                    "The relay pruned envelopes that waited past its retention "
                    "window without being drained. Those errors never reached "
                    "GlitchTip."
                ),
                severity="warn",
                dedup_key="sentry_relay_envelopes_expired",
            )

        detail = (
            f"{outcome.pulled} pulled, {outcome.forwarded} forwarded, "
            f"{outcome.rejected} rejected, {outcome.deferred} deferred, "
            f"{outcome.backlog} queued"
        )
        return JobResult(
            ok=not outcome.errors,
            detail=detail,
            changes_made=outcome.forwarded,
            metrics=outcome.as_metrics(),
        )
