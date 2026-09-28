"""ProbeNewsletterSignupJob — daily end-to-end check of the public signup path.

Signs a Resend test inbox up through the public signup endpoint, confirms
the contact reached the segment the worker syncs, then deletes it. It is the
only detector that can tell "nobody signed up" from "signups are broken"; see
:mod:`poindexter.services.newsletter_signup_canary` for the two silent
outages that made it necessary.

Daily: the signup path changes only when the site deploys or someone edits
the Vercel or Resend config, and each run costs one welcome email to a Resend
test address. A failure raises ``newsletter_signup_capture_broken`` to Discord.

Off until ``newsletter_signup_canary_url`` is set. Pointing it at a live form
is an operator decision, because every run sends that welcome email.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

_REMEDIATION = (
    "Check the site's Vercel env (RESEND_API_KEY needs contact write access; "
    "RESEND_AUDIENCE_ID must equal the worker's resend_audience_id), then "
    "re-run with `poindexter newsletter canary`."
)


class ProbeNewsletterSignupJob:
    name = "probe_newsletter_signup"
    description = (
        "Sign a Resend test inbox up through the public signup endpoint and "
        "confirm the worker can see it"
    )
    schedule = "every 24 hours"
    # Creates and deletes the same contact; overlapping runs would delete
    # each other's canary mid-check.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        from poindexter.services.newsletter_signup_canary import (
            CanaryConfigError,
            run_signup_canary,
        )

        try:
            outcome = await run_signup_canary(site_config)
        except CanaryConfigError as exc:
            self._report(
                "Newsletter signup canary cannot run",
                f"{describe_exception(exc)}\n\nSet the value, or clear "
                "newsletter_signup_canary_url to switch the canary off.",
                dedup_key="newsletter_signup_canary_config",
            )
            return JobResult(ok=False, detail=describe_exception(exc))
        except Exception as exc:
            logger.error(
                "[ProbeNewsletterSignupJob] canary crashed: %s",
                describe_exception(exc), exc_info=True,
            )
            self._report(
                "Newsletter signup canary failed to run",
                f"{describe_exception(exc)}\n\n{_REMEDIATION}",
                dedup_key="newsletter_signup_canary_crashed",
            )
            return JobResult(ok=False, detail=describe_exception(exc))

        if outcome is None:
            return JobResult(
                ok=True, detail="newsletter_signup_canary_url not set — canary off"
            )

        if not outcome.healthy:
            self._report(
                "Newsletter signups are not being captured",
                f"{outcome.problem}\n\nEndpoint: {outcome.url}\n\n{_REMEDIATION}",
                dedup_key="newsletter_signup_capture_broken",
            )
            detail = f"capture BROKEN: {outcome.problem}"
        else:
            detail = (
                f"captured (HTTP {outcome.http_status}, {outcome.latency_ms} ms, "
                f"attempt {outcome.attempts})"
            )
        if not outcome.cleaned_up:
            # The sync skips the canary address, so a leftover contact is
            # harmless; log it rather than page on it.
            logger.warning(
                "[ProbeNewsletterSignupJob] canary contact was not removed "
                "from Resend; the next run retries the delete"
            )
        return JobResult(
            ok=outcome.healthy,
            detail=detail,
            metrics=outcome.as_metrics(),
        )

    @staticmethod
    def _report(title: str, body: str, *, dedup_key: str) -> None:
        emit_finding(
            source="newsletter_signup_canary",
            kind="newsletter_signup_capture_broken",
            title=title,
            body=body,
            severity="warn",
            dedup_key=dedup_key,
        )
