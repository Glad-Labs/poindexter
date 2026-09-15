"""ProbeUnproductiveCapabilitiesJob — capabilities that are ON and produce nothing.

The complement to ``ProbeDisabledCapabilitiesJob``. That probe watches
capabilities that ship **off**, because silence reads as "working". This one
watches the opposite and more dangerous state: a capability that is **on**,
reports ``success``, and has never once produced its artifact. Nothing errors,
no flag looks wrong, every dashboard is green — and the feature has never run.

Seven instances inside two weeks, each correct code one hop short of its
consumer, none of them raising anything:

- ``[SCREENSHOT:]`` lived on a writer prompt the live graph does not use --
  0 screenshot assets, ever, with a target configured the whole time.
- ``benchmark_findings`` was enabled with no ``external_taps`` row.
- A pool-summary fix landed on the wrong handoff and **shipped a fabricated
  post**.
- Benchmark topics sat in the external bucket -- 0 candidates in 12 batches.
- ``_render_chart`` produced ``source="chart"`` with no injection branch.
- Three QA gates were enabled and documented live with no atom wiring them --
  0 reviews in 60 days.
- ``gsc_query_gap`` ran 187 times, returned 0 rows every time, reported
  ``success`` every time.

**The reliable detector is evidence, not inference:** ask the database whether
the thing has EVER produced its artifact. A static producer/consumer scanner
was built for this and deliberately NOT shipped -- it returned 243 candidate
orphans, nearly all audit labels routed by DB policy rather than ``==``
dispatch, which is the precision profile this repo already rejected for bandit
and semgrep.

Two checks, both join-free on purpose:

**A. Runs but never produces.** ``external_taps`` carries ``total_runs`` and
``total_records`` on the SAME row -- no join, no name alias, no ambiguity. A
tap past the run floor with zero records ever is either broken or configured
out of reach, and the two are indistinguishable from outside.

**B. Permitted but never scheduled.** A ``topic_source`` plugin registration
only PERMITS a source; an ``external_taps`` row is what SCHEDULES it. A
registered source with no row can never run, which is precisely how
``search_autocomplete`` contributed 0 topics for weeks and ``benchmark_findings``
shipped inert.

Both deliberately skip surfaces needing a name translation. Gate names are not
reviewer names (``llm_critic`` emits ``ollama_critic``) and
``publishing_adapters.name`` is not a ``social_post_drafts.platform`` -- a naive
join across either produced false "never ran" alarms on the first audit pass.
Those surfaces need an explicit alias map before they belong here; a probe that
cries wolf gets muted, and a muted probe is worse than none.

Severity is ``warn``, never ``info``: ``findings_alert_router``'s
``_fetch_unrouted_findings`` filters ``severity IN ('warn','warning','critical')``
*before* the per-kind ``min_severity`` policy is consulted, so an ``info``
finding can never route to Discord no matter how the policy is set. The weekly
cooldown, not the severity, is what keeps this from nagging.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "unproductive_capabilities_probe_enabled"
_MIN_RUNS_KEY = "unproductive_capabilities_min_runs"
_FINDING_KIND = "unproductive_capabilities"

# A tap that has barely run has not earned an alarm -- a newly-enabled tap with
# 0 records is normal, not broken. The floor is what separates "young" from
# "never works". gsc_query_gap had 187 runs when it was found.
_DEFAULT_MIN_RUNS = 20

# Enabled taps past the run floor that have NEVER produced a record.
# total_runs and total_records live on the same row, so this needs no join and
# cannot be confused by a name alias.
_BARREN_TAPS_SQL = """
SELECT name, tap_type, handler_name, total_runs,
       last_run_status, last_run_at
FROM external_taps
WHERE enabled
  AND COALESCE(total_runs, 0) >= $1
  AND COALESCE(total_records, 0) = 0
ORDER BY total_runs DESC
"""

# Every tap_type that HAS a row, so callers can diff registered-vs-scheduled.
_SCHEDULED_TAP_TYPES_SQL = """
SELECT DISTINCT tap_type FROM external_taps WHERE tap_type IS NOT NULL
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    if site_config is None:
        return default
    try:
        return int(site_config.get(key, default) or default)
    except (TypeError, ValueError):
        return default


async def _registered_topic_sources(pool: Any) -> dict[str, bool]:
    """Registered topic_source names -> whether their plugin row permits them.

    A source whose ``plugin.topic_source.<name>`` row says ``enabled: false`` is
    a deliberate operator "no" (``igdb`` ships this way) and must never be
    reported as a gap. A MISSING row is not a no -- ``topic_sources/runner.py``
    defaults a missing row to enabled -- so absence counts as permitted.
    """
    from poindexter.plugins.registry import get_topic_sources

    names = {
        getattr(p, "name", type(p).__name__)
        for p in get_topic_sources()
    }
    permitted: dict[str, bool] = {n: True for n in names if n}
    if not permitted:
        return permitted

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
            [f"plugin.topic_source.{n}" for n in permitted],
        )
    for row in rows:
        name = str(row["key"]).rsplit(".", 1)[-1]
        try:
            parsed = json.loads(row["value"] or "{}")
        except (TypeError, ValueError):
            # An unparseable row is not a deliberate "no". Leave it permitted so
            # a malformed value can never silently suppress a real gap.
            logger.warning(
                "[probe_unproductive_capabilities] plugin.topic_source.%s has "
                "unparseable JSON; treating as permitted", name,
            )
            continue
        if isinstance(parsed, dict) and parsed.get("enabled") is False:
            permitted[name] = False
    return permitted


def _build_body(barren: list[dict[str, Any]], unscheduled: list[str], min_runs: int) -> str:
    lines: list[str] = []
    if barren:
        lines += [
            f"**Ran but never produced** ({len(barren)}) — enabled, past "
            f"{min_runs} runs, zero records ever. Either broken or configured "
            "out of reach; from outside those look identical.",
            "",
        ]
        for tap in barren:
            lines.append(
                f"- `{tap['name']}` ({tap['tap_type'] or tap['handler_name']}) — "
                f"{tap['total_runs']} runs, last `{tap['last_run_status']}`"
            )
        lines.append("")
    if unscheduled:
        lines += [
            f"**Permitted but never scheduled** ({len(unscheduled)}) — "
            "registered topic sources with no `external_taps` row. A plugin row "
            "PERMITS a source; a tap row SCHEDULES it. Without one it can never run.",
            "",
        ]
        for name in unscheduled:
            lines.append(f"- `{name}`")
        lines.append("")
    lines.append(
        "Being listed is not proof of a bug — a source may be deliberately "
        "dormant. It is proof that nothing here has ever produced its artifact, "
        "which no other signal shows."
    )
    return "\n".join(lines)


class ProbeUnproductiveCapabilitiesJob:
    """Emit a finding for capabilities that are on and have produced nothing."""

    name = "probe_unproductive_capabilities"
    description = (
        "Surface capabilities that are ENABLED and have never produced their "
        "artifact — the complement to probe_disabled_capabilities"
    )
    schedule = "every 24 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)
        if pool is None:
            return JobResult(ok=False, detail="no pool", changes_made=0)

        min_runs = _cfg_int(site_config, _MIN_RUNS_KEY, _DEFAULT_MIN_RUNS)

        async with pool.acquire() as conn:
            barren_rows = await conn.fetch(_BARREN_TAPS_SQL, min_runs)
            scheduled_rows = await conn.fetch(_SCHEDULED_TAP_TYPES_SQL)

        barren = [dict(r) for r in barren_rows]
        scheduled = {str(r["tap_type"]) for r in scheduled_rows}

        permitted = await _registered_topic_sources(pool)
        unscheduled = sorted(
            name for name, is_permitted in permitted.items()
            if is_permitted and name not in scheduled
        )

        if not barren and not unscheduled:
            return JobResult(
                ok=True,
                detail="every enabled capability has produced at least once",
                changes_made=0,
            )

        total = len(barren) + len(unscheduled)
        emit_finding(
            source="unproductive_capabilities_probe",
            kind=_FINDING_KIND,
            title=(
                f"{total} capabilit{'y has' if total == 1 else 'ies have'} "
                "never produced anything"
            ),
            body=_build_body(barren, unscheduled, min_runs),
            severity="warn",
            dedup_key=_FINDING_KIND,
            extra={
                "barren_taps": [t["name"] for t in barren],
                "unscheduled_sources": unscheduled,
                "min_runs": min_runs,
            },
        )
        logger.info(
            "[probe_unproductive_capabilities] %d barren tap(s), %d unscheduled "
            "source(s): %s | %s",
            len(barren), len(unscheduled),
            ", ".join(t["name"] for t in barren) or "-",
            ", ".join(unscheduled) or "-",
        )
        return JobResult(
            ok=True,
            detail=f"emitted finding for {total} unproductive capability(ies)",
            changes_made=0,
        )
