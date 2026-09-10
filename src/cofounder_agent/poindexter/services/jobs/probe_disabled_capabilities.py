"""ProbeDisabledCapabilitiesJob — surface opt-in capabilities that ship off.

glad-labs-stack#2133: "I don't like default off because there's no way of
keeping track of what's working. If it's quiet I assume it's working, not
disabled." Several capabilities ship ``false`` by design (they depend on
external accounts/services an operator must configure, or are genuinely
opt-in quality passes) and stay completely silent once disabled — nothing
pages, nothing logs, the pipeline just quietly never exercises that code
path. This probe makes that silence loud instead.

Runs daily (these are operator-toggled settings, not fast-moving state).
Checks a fixed, curated list of capability flags — deliberately NOT a
generic "any false boolean in app_settings" scan, which would bury the
handful of business-meaningful toggles under noise from settings that are
correctly false as their permanent steady state (individual QA-rail
advisory gates, etc.).

Emits ONE advisory ``disabled_capabilities`` finding with a stable
``dedup_key`` so the alert dispatcher collapses repeats per the usual
findings-routing policy.

Severity is ``warn``, not ``info``, even though "off" is frequently correct
(e.g. social OAuth not yet configured) — ``findings_alert_router``'s SQL
layer (``_fetch_unrouted_findings``) only ever fetches
``severity IN ('warn', 'warning', 'critical')`` *before* the per-kind
``findings.<kind>.min_severity`` policy is even consulted, so an
``info``-severity finding can never route to Discord regardless of policy
config — it would silently sit unrouted forever. See
``findings-delivery-needs-warn-severity`` (glad-labs-stack#1471, the same
mistake previously bit ``topic_gap``). The weekly
``findings.disabled_capabilities.cooldown_minutes`` (not severity) is what
keeps this from nagging about unchanged state.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "disabled_capabilities_probe_enabled"
_FINDING_KIND = "disabled_capabilities"

# (setting key, human label) — curated per glad-labs-stack#2133. Add here
# when a new opt-in, silently-off-by-default capability ships.
_WATCHED_CAPABILITIES: list[tuple[str, str]] = [
    ("enable_writer_self_review", "Writer self-review pass"),
    ("self_consistency_enabled", "QA self-consistency rail"),
    ("media_pipeline_trigger_enabled", "Video/media pipeline"),
    ("podcast_pipeline_trigger_enabled", "Podcast pipeline"),
    ("podcast_tts_enabled", "Podcast TTS narration"),
    ("newsletter_enabled", "Weekly newsletter digest"),
    ("social_drafts_enabled", "Social draft generation (Postiz)"),
]


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _build_body(disabled: list[tuple[str, str]]) -> str:
    lines = [
        f"{len(disabled)} of {len(_WATCHED_CAPABILITIES)} watched capabilities "
        "are currently off. Being off is often correct (e.g. social OAuth not "
        "yet connected) — this is a visibility reminder, not an alarm.",
        "",
    ]
    for key, label in disabled:
        lines.append(f"- **{label}** (`{key}`)")
    return "\n".join(lines)


class ProbeDisabledCapabilitiesJob:
    """Emit a finding listing currently-disabled opt-in capabilities."""

    name = "probe_disabled_capabilities"
    description = (
        "Surface opt-in capabilities that ship disabled, so silence never "
        "reads as 'working' (glad-labs-stack#2133)"
    )
    schedule = "every 24 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)

        disabled = [
            (key, label)
            for key, label in _WATCHED_CAPABILITIES
            if not _cfg_bool(site_config, key, False)
        ]

        if not disabled:
            return JobResult(
                ok=True,
                detail="all watched capabilities enabled",
                changes_made=0,
            )

        keys = [key for key, _label in disabled]
        emit_finding(
            source="disabled_capabilities_probe",
            kind=_FINDING_KIND,
            title=f"{len(disabled)} capabilit{'y is' if len(disabled) == 1 else 'ies are'} disabled",
            body=_build_body(disabled),
            severity="warn",
            dedup_key=_FINDING_KIND,
            extra={"count": len(disabled), "keys": keys},
        )
        logger.info(
            "[probe_disabled_capabilities] emitted finding for %d disabled "
            "capabilit%s: %s",
            len(disabled), "y" if len(disabled) == 1 else "ies", ", ".join(keys),
        )
        return JobResult(
            ok=True,
            detail=f"emitted finding for {len(disabled)} disabled capability(ies)",
            changes_made=0,
        )
