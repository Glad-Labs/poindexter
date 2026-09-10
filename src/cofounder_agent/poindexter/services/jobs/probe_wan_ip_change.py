"""ProbeWanIpChangeJob — notice when this host's public egress IP changes.

Several outbound integrations are authorised by **source IP allowlist** rather
than by token alone. Mercury (the finance module's read-only banking API) is
the live example: it 401s from an address that is not on its allowlist, and
that 401 is indistinguishable from an expired or revoked token. The 2026-07-20
Mercury outage was diagnosed as a token problem for weeks before the allowlist
turned out to be the cause.

This host sits on a **residential WAN address with no static guarantee**. It
has been stable for over a month, but a modem swap, a long outage, or an ISP
re-provision silently re-leases it — and the first symptom is an integration
returning 401 with no indication that anything about *this* machine changed.

So the probe records the current egress IP and emits a finding when it moves.
The point is not to fix anything automatically: the fix is an allowlist edit in
someone else's dashboard. The point is to make the cause **legible at the
moment it happens**, so the next 401 reads as "the WAN IP changed at 03:12" and
not as "Mercury is broken again, why".

Two design notes:

- The IP is fetched from the *worker container*, not the host, because that is
  the process whose egress actually matters — it is the one calling Mercury.
  Docker's default bridge NATs through the same WAN address, so the two agree
  today, but asking the caller is the honest question.
- The previous value lives in ``app_settings.wan_ip_last_seen`` rather than in
  memory, so a worker restart cannot lose the baseline and re-alert. The very
  first run seeds the baseline and stays silent — an empty baseline is "not yet
  known", never "it changed".
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_LAST_SEEN_KEY = "wan_ip_last_seen"


class ProbeWanIpChangeJob:
    name = "probe_wan_ip_change"
    description = (
        "Resolve this host's public egress IP and emit a finding when it "
        "changes, so IP-allowlisted integrations (Mercury) fail legibly."
    )
    schedule = "every 30 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(
                ok=True,
                detail="no _site_config in job config — skipping probe",
                changes_made=0,
            )

        if (sc.get("wan_ip_probe_enabled", "true") or "").strip().lower() != "true":
            return JobResult(
                ok=True, detail="wan_ip_probe_enabled=false — skipping", changes_made=0
            )

        url = (sc.get("wan_ip_probe_url", "") or "").strip()
        if not url:
            # Absence of config must never read as a fault.
            return JobResult(
                ok=True, detail="wan_ip_probe_url unset — skipping", changes_made=0
            )

        try:
            import httpx
        except ImportError:
            return JobResult(ok=False, detail="httpx not available", changes_made=0)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=3.0)
            ) as client:
                resp = await client.get(url)
            resp.raise_for_status()
            current = resp.text.strip()
        except Exception as e:  # noqa: BLE001 — any failure ⇒ unknown, not changed
            # A lookup failure is NOT a change. Returning ok=True keeps a flaky
            # echo service (or a brief outage) from both alerting and tripping
            # apscheduler back-off on an otherwise healthy probe.
            detail = describe_exception(e)
            logger.warning("[WAN_IP] lookup failed via %s: %s", url, detail)
            return JobResult(
                ok=True, detail=f"lookup failed ({detail}) — no comparison", changes_made=0
            )

        # Guard against an echo service returning a banner, an HTML error page,
        # or a rate-limit message: only something IP-shaped may overwrite the
        # baseline, or one bad response would fake a change AND poison it.
        if not _looks_like_ip(current):
            logger.warning("[WAN_IP] %s returned non-IP payload: %r", url, current[:80])
            return JobResult(
                ok=True,
                detail=f"non-IP response from {url} — ignored",
                changes_made=0,
            )

        previous = (sc.get(_LAST_SEEN_KEY, "") or "").strip()

        if not previous:
            await self._persist(pool, current)
            logger.info("[WAN_IP] baseline seeded: %s", current)
            return JobResult(
                ok=True, detail=f"baseline seeded ({current})", changes_made=1
            )

        if previous == current:
            return JobResult(ok=True, detail=f"unchanged ({current})", changes_made=0)

        await self._persist(pool, current)
        logger.warning("[WAN_IP] public egress IP changed: %s -> %s", previous, current)
        emit_finding(
            source="probe_wan_ip_change",
            kind="wan_ip_changed",
            severity="warn",
            title=f"Public egress IP changed: {previous} → {current}",
            body=(
                f"This host's public egress address changed from {previous} to "
                f"{current}.\n\n"
                f"Integrations authorised by source-IP allowlist will start "
                f"returning 401/403 until the new address is allowlisted — and "
                f"that failure looks identical to an expired token, so check "
                f"this first.\n\n"
                f"Known IP-allowlisted integration: **Mercury** (finance "
                f"module). Add {current} in the Mercury dashboard, then verify "
                f"with `curl -s -o /dev/null -w '%{{http_code}}' "
                f"http://localhost:8002/api/finance/healthcheck` (expect 200).\n\n"
                f"If the address now changes often, ask whether the provider "
                f"accepts a CIDR range rather than a single host."
            ),
            # Keyed on the destination address: each distinct new IP is its own
            # finding. A kind-level key would collapse a second change during
            # the dedup window into silence — precisely the fire you need to
            # see, because the allowlist you just edited is already stale.
            dedup_key=f"wan_ip_changed:{current}",
        )
        return JobResult(
            ok=True,
            detail=f"WAN IP changed {previous} -> {current} — finding emitted",
            changes_made=1,
        )

    async def _persist(self, pool: Any, value: str) -> None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO app_settings
                        (key, value, category, description, is_active, is_secret)
                    VALUES (
                        'wan_ip_last_seen',
                        $1,
                        'monitoring',
                        'Last observed public egress IP, written by the '
                        'probe_wan_ip_change job. Compared each run to detect '
                        'a WAN address change that would break IP-allowlisted '
                        'integrations.',
                        true,
                        false
                    )
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value
                    """,
                    value,
                )
        except Exception as e:
            # Failing to persist means we re-alert next run. Noisy, but strictly
            # better than swallowing a real change, so this stays non-fatal.
            logger.warning(
                "[WAN_IP] failed to persist last-seen IP (%s): %s",
                value,
                describe_exception(e),
            )


def _looks_like_ip(value: str) -> bool:
    """True for a bare IPv4/IPv6 literal. Deliberately strict: this gates what
    may overwrite the stored baseline."""
    import ipaddress

    if not value or len(value) > 45:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
