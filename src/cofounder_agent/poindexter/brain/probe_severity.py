"""Per-probe severity classification for the brain's probes.

Decides whether a probe's FAILURE notice pages Telegram or lands as a
Discord notice only. Shared by ``health_probes.py``, ``business_probes.py``
and ``post_performance_probe.py`` — one home for the same small helper
rather than three copies that drift, the same reason ``secret_reader.py``
exists.

Why this exists (2026-09-25)
-----------------------------
``health_probes.run_health_probes`` paged Telegram for EVERY probe that
failed ``ALERT_AFTER_FAILURES`` times in a row, with no severity — a
business/quality signal (a cadence target missed, an approval queue
backing up, a 7-day throughput dip) paged the phone exactly like a
database outage. 30 days to 2026-09-25 (Loki, ``container=
"poindexter-brain-daemon"``): 22 probe failures reached the threshold and
20 of them paged — ``cadence_slo`` x8, ``pipeline_throughput`` x6,
``approval_queue`` x3, ``traffic_anomaly``, ``quality_score``,
``grafana_datasources``. None was an outage, and under this module none
of the 20 would page. (The other two were ``publish_rate``, which
``PROMETHEUS_COVERED_PROBES`` suppressed because Alertmanager was healthy
all month.) ``business_probes.py`` and
``post_performance_probe.py`` had the same shape: ``post_performance``
paged 120 times (111 of them within 15 minutes of a brain restart, each
re-sending the whole broken-post list, one post until 2026-09-04 and then
78 growing to 129, rather than 120 new findings) and
``webhook_freshness`` 31 times (all within 15 minutes of a restart), both
business/SEO signals.

The fix is a severity per probe, checked against the SAME two-tier
boundary ``operator_notifier.notify_operator`` and
``alert_dispatcher._channels_for`` already use elsewhere in the brain:
``critical``/``error`` page, ``warning``/``info`` are a Discord notice.
Reusing that exact vocabulary means a probe's severity means the same
thing everywhere in the system, not a fourth private scale.

A probe's severity decides each of its failure-side notices: a plain
failure, a crash (being blind to a warning-class signal is a warning,
the rule #4051 set for the branch-drift canary; a critical probe's crash
still pages), and, for a Prometheus-covered probe, the report the brain
sends itself when Alertmanager is down. A recovery and a self-heal that
worked are always notices. A self-heal that failed always pages, per
docs/operations/self-healing.md: escalate when recovery fails.

Derive, don't hand-list (CLAUDE.md's own recurring lesson: a hand-typed
set that nobody revisits drifts silently — the ``qa_gates`` alias guard
did this eight times before it was made to derive from the seeded rows
instead). There is no mechanical signal here that says "this probe is a
genuine outage" the way a seeded DB row can say "this gate exists" — that
judgement call is inherently human. So :data:`PROBE_DEFAULT_SEVERITY` is
deliberately a SMALL allowlist of the probes whose failure represents a
genuine outage or a monitoring-blind condition, not a full severity map
for all of them. Every probe NOT named here defaults to
:data:`DEFAULT_SEVERITY` (``"warning"`` — Discord, not Telegram). That is
the safe direction for a NEW probe nobody has classified yet: the whole
point of this module is that an un-reconsidered probe should not wake
Matt at 3am by default. ``test_default_severity_keys_are_real_probe_names``
(in ``tests/unit/services/test_brain_health_probes.py``) is the drift
guard in the other direction — a stale entry naming a probe that no
longer exists.

Every probe stays fully operator-tunable via one DB-backed JSON setting
(``app_settings.brain_probe_severity_overrides``, per the
``clock_skew_severity`` precedent in ``clock_skew_probe.py`` — the
project rule is "could a customer tune this? -> app_settings", and this
clearly could: an operator may decide ``worker_error_rate`` doesn't need
to page on their install, or that ``cadence_slo`` should).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("brain.probe_severity")

SEVERITY_OVERRIDES_SETTING_KEY = "brain_probe_severity_overrides"

# Same vocabulary and the same critical/error-vs-warning/info boundary as
# operator_notifier.notify_operator's severity matrix and
# alert_dispatcher._channels_for's _TELEGRAM_SEVERITIES — a probe's
# severity means the same thing everywhere in the system.
PAGING_SEVERITIES: frozenset[str] = frozenset({"critical", "error"})
VALID_SEVERITIES: frozenset[str] = frozenset({"critical", "error", "warning", "info"})
DEFAULT_SEVERITY = "warning"

# Only the probes whose failure represents a genuine outage or a
# monitoring-blind condition. See the module docstring for why this list
# is deliberately small — everything else defaults to DEFAULT_SEVERITY.
#
# health_probes.py
PROBE_DEFAULT_SEVERITY: dict[str, str] = {
    "db_ping": "critical",
    # Postgres unreachable: nothing in the pipeline can run. Prometheus-
    # covered (health_probes.PROMETHEUS_COVERED_PROBES): while Alertmanager
    # can deliver, PoindexterPostgresDown (also critical) owns the page and
    # the brain stays silent. This value decides how the brain delivers it
    # itself when Alertmanager can't.
    "ollama_models": "critical",
    # The local Ollama is unreachable: no LLM call can succeed. Prometheus-
    # covered like db_ping, but its covering rule, PoindexterOllamaDown, is
    # severity=warning (unchanged since the rule was written 2026-04-19),
    # so while Alertmanager is healthy an Ollama outage reaches Discord,
    # not Telegram. This value only decides the brain's own delivery when
    # Alertmanager is down.
    "worker_error_rate": "critical",
    # The worker is up but its tasks are failing (the probe's own detail
    # calls a 100% rate "CRITICAL"). Not Prometheus-covered, so this probe
    # is what pages it.
    "disk_space": "critical",
    # A filling disk is an outage in the making (the 2026-07-23 root-fill
    # shut Postgres down mid-WAL-redo). The threshold is 10% of the brain
    # container's root filesystem, about 180 GB on prod's 1.8 TB Docker
    # disk, so this pages well before Prometheus's PoindexterDiskSpaceLow
    # (20 GB, warning) or PoindexterDiskSpaceCritical (10 GB, critical).
    # Demote it through the override if that is too early for an install.
    "public_site": "critical",
    # The public site is unreachable or serving 0 posts: a visitor-facing
    # outage.
    "gpu_temperature": "critical",
    # Thermal safety, the same severity as the DB-rendered
    # GpuTemperatureHigh rule. The probe also fails when gpu_metrics goes
    # stale (GPU monitoring blind), and that pages too.
    # business_probes.py
    "silent_alerter": "critical",
    # The meta-watchdog: probes are red and no alert has gone out for
    # hours, so the paging path itself may be broken. Unlike one probe
    # going blind, this is every page going missing at once, and it is
    # worth using both channels to say so.
    #
    # Everything else is DEFAULT_SEVERITY ("warning"), a Discord notice:
    # ollama_vision_models, ollama_embedding, content_gen,
    # grafana_datasources, r2_connectivity, stuck_tasks, approval_queue,
    # failed_task_spike, quality_score, quality_trend, publish_rate,
    # cost_freshness, podcast_health, newsletter_health, research_service,
    # image_search, embeddings_freshness, traffic_anomaly, topic_quality,
    # pipeline_throughput, cadence_slo, webhook_freshness,
    # post_performance, and any probe added after this was written. Three
    # of them (stuck_tasks, grafana_datasources, ollama_embedding) have a
    # self-heal, and a self-heal that fails pages whatever the probe's
    # severity (health_probes._try_remediation), so a problem the brain
    # cannot fix still reaches the phone.
}


async def _read_setting(pool: Any, key: str, default: str = "") -> str:
    """Read one ``app_settings`` value. Never raises."""
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001 — probe-support code must never crash a cycle
        logger.warning("[probe_severity] setting read %s failed: %s", key, exc)
        return default
    return str(val) if val else default


async def load_overrides(pool: Any) -> dict[str, str]:
    """Read + validate ``brain_probe_severity_overrides`` once per cycle.

    Empty/missing setting returns ``{}`` (use code defaults for every
    probe). A malformed JSON blob, or one that isn't an object, is
    dropped with a WARNING and treated as ``{}`` — mirrors
    ``data_freshness_probe``'s "invalid entries are dropped loudly, not
    silently" contract for its own JSON-blob setting. An unknown severity
    value for a probe is dropped the same way, falling back to that
    probe's code default; an unknown PROBE NAME is kept as-is (the
    operator may be tuning a probe added after this module's map was
    last touched — see the module docstring on why the map is
    deliberately incomplete).

    Never raises.
    """
    raw = await _read_setting(pool, SEVERITY_OVERRIDES_SETTING_KEY, "")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[probe_severity] %s is not a JSON object (%s) — ignoring, "
            "using code defaults for every probe",
            SEVERITY_OVERRIDES_SETTING_KEY, exc,
        )
        return {}

    overrides: dict[str, str] = {}
    for name, sev in parsed.items():
        sev_norm = str(sev).strip().lower()
        if sev_norm in VALID_SEVERITIES:
            overrides[str(name)] = sev_norm
        else:
            logger.warning(
                "[probe_severity] %s: invalid severity %r for probe %r "
                "(must be one of %s) — ignoring, using the code default "
                "for this probe",
                SEVERITY_OVERRIDES_SETTING_KEY, sev, name,
                sorted(VALID_SEVERITIES),
            )
    return overrides


def severity_for(
    probe_name: str,
    overrides: dict[str, str],
    *,
    defaults: dict[str, str] | None = None,
) -> str:
    """Resolve one probe's severity: DB override -> code default ->
    :data:`DEFAULT_SEVERITY`. Pure and synchronous — never raises.
    """
    if probe_name in overrides:
        return overrides[probe_name]
    table = PROBE_DEFAULT_SEVERITY if defaults is None else defaults
    return table.get(probe_name, DEFAULT_SEVERITY)


def is_paging_severity(severity: str) -> bool:
    """True for ``critical``/``error`` — the two severities that page
    Telegram, matching ``alert_dispatcher._TELEGRAM_SEVERITIES``."""
    return (severity or "").strip().lower() in PAGING_SEVERITIES


async def sender_for(pool: Any, probe_name: str, notify_fn: Any, info_fn: Any = None) -> Any:
    """Resolve which of ``notify_fn``/``info_fn`` should carry this probe's
    notice, in one call.

    For a single-probe-per-cycle caller (``business_probes.py``,
    ``post_performance_probe.py``) — loads the override setting fresh each
    call rather than threading a preloaded dict through, since these
    probes run at most once per brain cycle, unlike ``health_probes.py``'s
    27-probe loop (which loads overrides once per cycle via
    :func:`load_overrides` instead). ``info_fn`` falls back to
    ``notify_fn`` when omitted, matching every other notice fallback in
    the brain (``health_probes.run_health_probes``, ``brain_daemon``'s own
    ``info = info_fn or notify_fn``).
    """
    overrides = await load_overrides(pool)
    severity = severity_for(probe_name, overrides)
    info = info_fn or notify_fn
    return notify_fn if is_paging_severity(severity) else info
