"""Render Prometheus alert rules from ``app_settings`` (DB-first).

Phase D follow-up (GitHub #68). Alert thresholds and rule bodies live in
``app_settings`` so operators can tune without editing YAML or rebuilding
the worker image. The repo still ships with sensible defaults — the
brain seeds them into ``app_settings`` on first boot (see
``brain/seed_app_settings.json``), but every value can be overridden
with a single ``UPDATE app_settings ...``.

## Key scheme

``prometheus.threshold.<name>``
    Scalar value (stored as a string in app_settings.value, parsed at
    render time). Referenced inside rule expressions as
    ``{threshold.<name>}``. Examples:

    - ``prometheus.threshold.daily_spend_warning = "4.0"``
    - ``prometheus.threshold.embeddings_stale_seconds = "21600"``

``prometheus.rule.<alert_name>``
    Full JSON override for a single alert. Partial keys are merged on
    top of the built-in default, so an operator who just wants to
    disable one alert writes ``{"enabled": false}`` and nothing else.

    Keys:
    - ``enabled`` (bool, default true)
    - ``expr`` (string — may reference ``{threshold.X}``)
    - ``for`` (duration string, e.g. ``"5m"``)
    - ``severity`` (``"info"``, ``"warning"``, ``"critical"``)
    - ``category`` (``"infrastructure"``, ``"content"``, ``"business"``)
    - ``group`` (which Prometheus group the rule lives in)
    - ``interval`` (group evaluation interval, e.g. ``"30s"``)
    - ``summary``, ``description`` (annotation strings)

## Rendering

:func:`build_current` is the entry point — reads both key prefixes,
merges with :data:`DEFAULT_RULES` / :data:`DEFAULT_THRESHOLDS`, returns a
YAML string suitable for Prometheus's ``rule_files`` directive.

YAML is hand-rendered (no PyYAML dep) because the shape is fixed and
Prometheus's parser is strict about quoting in expressions — manual
control beats fighting ``safe_dump`` quirks.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults — also used as the seed in brain/seed_app_settings.json.
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: dict[str, str] = {
    # Content pipeline
    "embeddings_stale_seconds": "21600",  # 6h
    # poindexter#553 — QaRailFullySkipped trigger. "1" = the rail skipped
    # 100% of the last N passes. Lower it (e.g. "0.9") to page before a
    # rail reaches a total blackout. The window N itself is the exporter's
    # app_settings.qa_rail_skip_window_passes (default 20), not a threshold.
    "qa_rail_skip_ratio": "1",
    # Infrastructure (Gitea #238 recovery)
    "postgres_p99_latency_seconds": "0.1",  # alert when SELECT 1 > 100ms p99
    "embeddings_missing_posts": "3",  # alert when >3 published posts lack embeddings
    # Business / cost guards
    "daily_spend_warning_usd": "4.0",
    "daily_spend_critical_usd": "5.0",
    # Sits just ABOVE the full-cost ceiling (cost_throttle_monthly_budget_usd,
    # default $60) because the gauge it watches — poindexter_monthly_spend_usd —
    # is the BLENDED axis: paid API + measured electricity. It is a
    # "the throttle failed to hold the line" backstop, not a budget echo.
    # At $35 it false-fired 46x in July 2026 once idle electricity alone
    # (~$1.4/day) crossed it, which is a budget being met, not an incident.
    "monthly_spend_warning_usd": "65.0",
    # GPU hardware (audit C3). Thresholds for the container-supervised
    # nvidia-smi exporter (job="nvidia-smi", gpu-exporter:9835). 85°C is a
    # safe warning ceiling well below the ~90°C throttle point; 95% VRAM
    # catches OOM-imminent before Ollama/image-gen allocations start failing.
    "gpu_temperature_celsius": "85",
    "gpu_vram_utilization_percent": "95",
    # How many GPUs this rig is supposed to expose. Ships "0" = rule disabled,
    # because a 1-GPU consumer install must not inherit a 2-GPU operator's
    # alert; each operator sets their own count. Guards a SILENT failure mode:
    # NVIDIA device nodes are injected at container-CREATE time, so a card the
    # gpu-exporter container was created without simply never appears — no
    # error, no gap, just one fewer series. An RTX 3090 was missing from
    # monitoring for 7+ days that way (2026-07-26) while still burning ~42W.
    "expected_gpu_count": "0",
    # Host disk space. Absolute GB thresholds (not percentage) because what
    # hurts is absolute headroom — Docker images, Postgres growth, model files.
    # min_total_gb filters out USB drives / EFI partitions that always hover
    # near the low cutoffs and have no bearing on pipeline health.
    "disk_free_warning_gb": "20",
    "disk_free_critical_gb": "10",
    "disk_min_total_gb": "200",
    # Brain DB capacity warning (poindexter#735 item 2 — replaces the Grafana
    # SQL pg_database_size() poll). Non-page-worthy: the bare `> N` expr yields
    # no series when the exporter is down, so it never fires on no-data (#581).
    "brain_db_size_warning_gb": "5",
    # Per-container RSS ceiling (2026-07-02 observability review). Calibrated
    # against 24h maxima: everything except the model servers sits under
    # ~5 GB; langfuse-clickhouse has spiked to 15 GB and unbounded cadvisor
    # growth caused a VM-OOM cascade (#2019/#2021). The model servers
    # (image-gen / wan) legitimately hold weights in RAM and are excluded in
    # the rule expr, not via this threshold.
    "container_memory_warning_gb": "8",
    # Postgres connection-pool saturation. Ratios so they scale with any
    # max_connections value (currently 300 in docker-compose.local.yml).
    "postgres_connection_warning_ratio": "0.80",
    "postgres_connection_critical_ratio": "0.95",
    # Host RAM pressure (2026-07-10 desktop-freeze investigation; thrash
    # detector reworked to PSI 2026-07-25). The worker host runs the container
    # stack, a host-native inference fleet, AND the operator desktop on shared
    # physical RAM. When available RAM approaches zero the OS stalls on
    # reclaim and the desktop compositor freezes. 4 GB is a comfortable
    # warning floor (normal idle headroom is higher). The thrash signal is
    # PSI full-stall — the fraction of wall-clock time ALL non-idle tasks
    # were simultaneously blocked on memory. The original pages/s threshold
    # (2000/s, calibrated on windows_exporter pagefile writes) did not
    # survive the Pop!_OS migration: zram swap at priority 1000 makes
    # pswpout count cheap compressed-RAM page-outs, and the rule flapped
    # ~131 criticals in its first two Linux weeks while PSI stayed under 3%.
    # 0.25 = the system stalled on memory a quarter of the time, sustained;
    # healthy baseline is <0.01.
    "host_memory_available_warning_gb": "4",
    "host_memory_psi_full_stall_critical_ratio": "0.25",
    # Mains supply quality (Glad-Labs/poindexter#924). Watches
    # psu_line_voltage_volts — true outlet voltage from the Shelly wall-plug
    # meter (see docs/operations/wall-power-metering.md), NOT a PSU-internal
    # estimate. Undervoltage is otherwise an invisible failure mode: the host
    # browns out and dies mid-instruction, so there is no journal entry, no
    # MCE, and no thermal trace to find afterwards — it reads as a mystery
    # reboot. Two such crashes hit the operator rig in five days (2026-07-23
    # 16:24 at 93.5V under 726W load; 2026-07-27 17:52 idle, on an afternoon
    # whose baseline had sagged to 104V).
    #
    # Nominal ships "0" = BOTH rules inert, same reasoning as
    # expected_gpu_count above: mains nominal is regional (120V vs 230V) and a
    # 230V operator must never inherit a 120V operator's threshold. Each
    # operator sets their own; with "0" the comparison is `volts < 0`, which
    # no sample matches. Percentages are of nominal, so they carry over to any
    # region unchanged once nominal is set.
    "psu_nominal_line_voltage_volts": "0",
    # 92% = the ANSI C84.1 Range B lower bound (110V on a 120V nominal) —
    # the standards line for "utilization voltage is out of spec", not an
    # arbitrary pick. 87% is the approach to typical ATX-PSU brownout dropout
    # (~85-90V on 120V nominal); below it a hard cut is imminent, not
    # theoretical.
    "psu_line_voltage_warning_percent": "92",
    "psu_line_voltage_critical_percent": "87",
    # UPS via NUT (Glad-Labs/poindexter#958). All Ups* rules read the
    # network_ups_tools_* series from the profile-gated nut-exporter
    # container (job="nut"), so on a host without the `ups` compose profile
    # every load/battery rule is naturally inert — no series, no fire.
    #
    # How many UPSes this host expects upsd to serve. Ships "0" = UpsCommsLost
    # disabled, same reasoning as expected_gpu_count above: a host with no UPS
    # must not page "comms lost" forever. Operators with a UPS set their count
    # (usually "1"); the rule then fires when the status series count drops
    # below it — covering a dead exporter, a dead upsd, a stale driver AND an
    # unplugged USB data cable with one signal.
    "expected_ups_count": "0",
    # Chronic-undervoltage advisory line for the voltage the UPS itself
    # measures at its input. Ships "0" = inert because mains nominal is
    # regional (120V vs 230V) — the psu_nominal_line_voltage_volts precedent.
    # A 120V operator's natural setting is "114": the ANSI C84.1 Range A
    # service-voltage lower bound, and the ceiling the operator rig's house
    # supply never exceeded across 9 metered days (93.5-114.6V, 2026-07).
    "ups_input_voltage_low_volts": "0",
    # ups.load is percent of the UPS's OWN inverter rating, so unlike the
    # voltage keys these carry over to any hardware unchanged and can ship
    # live. Calibrated against the 2026-07-31 overload trip: a 1000W-rated
    # unit transferred to battery while carrying ~1060W (PC burst 910W + ~150W
    # of other gear) and dropped its output. 85% is the "margin is gone, shed
    # load or raise caps" line; 95% is "the next burst trips the inverter".
    "ups_load_warning_percent": "85",
    "ups_load_critical_percent": "95",
    # Battery floor for the UpsLowBattery page, gated on actually being ON
    # battery (discharging). Fires well above the driver's own shutdown
    # floor (battery.charge.low, 30% on the operator rig) so the page lands
    # while there is still runtime to act in — NUT's LB/forced-shutdown line
    # is the point of no return, not a useful warning.
    "ups_battery_charge_critical_percent": "50",
}


# Each rule entry is the canonical shape. The built-in list ships the
# current Phase-D alerts as-is.
DEFAULT_RULES: dict[str, dict[str, Any]] = {
    # Infrastructure alerts (PoindexterWorkerDown, PoindexterPostgresDown,
    # PoindexterOllamaDown, PoindexterWorkerUnhealthy) live in the static
    # infrastructure/prometheus/alerts/infrastructure.yml file — they have
    # no thresholds to configure and they need to fire even when the DB
    # is down (which is exactly when dynamic-rules rendering would fail).
    # Only content + business rules live here, since those are the ones
    # with tunable numbers.
    #
    # Restart-gap policy (glad-labs-stack#2330 follow-up audit, 2026-07-11):
    # metrics from the worker's own exporter (services/metrics_exporter.py,
    # instance worker:8002) blank on every deploy restart — a failed scrape
    # writes staleness markers for ALL of the target's series, so instant
    # selectors go empty on the next evaluation (no 5m lookback grace) and
    # any in-flight `for:` clock resets. Observed 2026-07-11: 46 worker
    # restarts punched 138 series holes in one day. Consequences by rule
    # shape:
    #   - Long-`for:` instant reads on worker gauges CANNOT complete their
    #     pending window on active days → wrap the read in
    #     last_over_time(...[1h]). last (value-faithful: the newest sample
    #     wins, so genuine recoveries resolve immediately) over max (which
    #     holds stale highs for the whole lookback) unless riding through
    #     genuine dips is the point, as in QaRailFullySkipped.
    #   - Range-vector exprs (increase()/rate()) are naturally restart-proof:
    #     staleness markers don't remove raw samples from range windows.
    #   - Short-`for:` worker rules (≤5m: daily spend, OllamaNoModelsLoaded,
    #     pg connections) stay raw deliberately — a hole only delays them by
    #     minutes, and their truths genuinely change at the moments a bridge
    #     would paper over (connections release when the worker restarts;
    #     daily spend resets at midnight; the Ollama rule's `unless` guard
    #     needs both operands blanking in lockstep).
    #   - Independent exporters (node_exporter, nvidia-smi,
    #     postgres_exporter, cadvisor) don't restart with worker deploys —
    #     raw instant reads are fine there.

    # --- Content ---
    "EmbeddingsStale": {
        "enabled": True,
        "group": "poindexter-content",
        "interval": "1m",
        # Alert when the embeddings_total gauge hasn't grown at all in
        # the configured window — auto-embed is either stuck or the tap
        # runner is failing silently. The window is configured via the
        # threshold (converted from seconds to a Prometheus duration at
        # render time — see :func:`_render_rule`).
        # Previously this expression read ``time() - max(embeddings_total)``,
        # which subtracted the COUNT from the unix timestamp — always ≫ 0
        # so the alert fired permanently.
        "expr": "sum(increase(poindexter_embeddings_total[{threshold.embeddings_stale_seconds}s])) == 0",
        "for": "10m",
        "severity": "warning",
        "category": "content",
        "summary": "auto-embed hasn't updated embeddings_total gauge recently",
        "description": (
            "poindexter_embeddings_total hasn't changed in the configured window. "
            "Either the auto-embed container is stuck or the Tap runner is failing "
            "silently. Check `docker logs poindexter-auto-embed` and "
            "`tail ~/.gladlabs/auto-embed.log`."
        ),
    },
    "NoPublishedPostsRecently": {
        "enabled": True,
        "group": "poindexter-content",
        "interval": "1m",
        # last_over_time bridges deploy-restart scrape holes on BOTH operands
        # — the current read and the offset read (a hole 24h ago blanks the
        # offset side today). With raw instant reads the `for: 48h` clock
        # reset on every restart: 4,002 pending-minutes across 92 chopped
        # episodes and ZERO fires in the 14d before 2026-07-11 (a 48h rule
        # can complete at most ~7 episodes in 14d). A fresh publish still
        # resolves immediately — last_over_time returns the newest sample.
        "expr": (
            'last_over_time(poindexter_posts_total{status="published"}[1h] offset 24h)'
            ' == last_over_time(poindexter_posts_total{status="published"}[1h])'
        ),
        "for": "48h",
        "severity": "info",
        "category": "content",
        "summary": "No new posts published in 48h",
        "description": (
            "Published post count hasn't grown in 48h. Either content "
            "generation is stalled, QA is rejecting everything, or the "
            "approval queue is backed up awaiting human review."
        ),
    },
    # poindexter#553 — a QA rail that's skipping 100% of recent passes is
    # silently not contributing its signal to the gate. The graph_def QA
    # path (atom-cutover #355) keeps running without it, so this never
    # fails loud on its own — the alert is the only operator-facing signal.
    "QaRailFullySkipped": {
        "enabled": True,
        "group": "poindexter-content",
        "interval": "1m",
        # poindexter_qa_rail_skip_ratio{reviewer} is the fraction of the
        # last N QA passes the rail was skipped (exporter caps it at 1.0).
        # >= 1 means a TOTAL blackout for that rail. Per-reviewer label, so
        # the firing alert names the offending rail.
        #
        # max_over_time rides through short series gaps: the exporter lives
        # in the worker, and every deploy restart blanks the metric for a
        # scrape or two. With the raw gauge those gaps reset the `for:`
        # clock, so on active days (8-45 worker restarts/day observed
        # 2026-06/07) the alert flapped fire→resolve→fire, then sat in
        # `pending` forever and never paged during poindexter#839's 12-day
        # ragas blackout. The 1h lookback holds the last observed ratio
        # across a restart; trade-off is resolve lags up to 1h after the
        # rail recovers.
        "expr": "max_over_time(poindexter_qa_rail_skip_ratio[1h]) >= {threshold.qa_rail_skip_ratio}",
        "for": "30m",
        "severity": "warning",
        "category": "content",
        "summary": "A QA rail has skipped 100% of recent passes ({{ $labels.reviewer }})",
        "description": (
            "QA rail {{ $labels.reviewer }} was skipped in every one of the "
            "last N QA passes — its signal is missing from the gate decision "
            "while the pipeline keeps publishing. Likely causes (read the "
            "exact reason in audit_log WHERE event_type='qa_reviewer_skipped' "
            "ORDER BY \"timestamp\" DESC): (1) empty research_context — the "
            "grounding rails (ragas_eval, deepeval_faithfulness) need a "
            "retrieved corpus; check the research stage is populating it. "
            "(2) disabled master flag — ragas_enabled / deepeval_enabled / "
            "guardrails_enabled is false in app_settings. (3) unresolvable "
            "judge model — fix deepeval_judge_model or ragas_judge_model. "
            "(4) broken rail dependency import — the qa_reviewer_skipped "
            "reason in audit_log names the incompat (poindexter#839). "
            "Inspect per-rail on the QA Rails "
            "dashboard /d/qa-rails. If the rail is intentionally off, lower or "
            "disable this alert via prometheus.rule.QaRailFullySkipped."
        ),
    },
    # Gitea #238 — recovers nuances the retired brain probes measured.
    "PostsNotEmbedded": {
        "enabled": True,
        "group": "poindexter-content",
        "interval": "1m",
        # Catches the "3 new posts published, 0 got embedded" case that
        # the embeddings_total rate alone misses when overall traffic
        # keeps the rate positive.
        #
        # last_over_time rides deploy-restart scrape holes so the 10m clock
        # survives active-dev days; last (not max) so a backfill that clears
        # the queue resolves the alert with the first fresh sample instead
        # of a held high firing for up to 1h after the fix.
        "expr": (
            "last_over_time(poindexter_embeddings_missing_posts[1h]) > "
            "{threshold.embeddings_missing_posts}"
        ),
        "for": "10m",
        "severity": "warning",
        "category": "content",
        "summary": "Published posts piling up without embeddings",
        "description": (
            "Published posts are not getting embeddings. Auto-embed is "
            "either wedged or the embeddings-writer pipeline is erroring "
            "silently for new posts. Run `poindexter memory backfill-posts "
            "--since 1d --dry-run` to inspect."
        ),
    },
    # --- Infrastructure (Gitea #238) ---
    "PostgresSlowQuery": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "1m",
        # p99 latency of the SELECT 1 liveness probe. Recovers the
        # "DB up but slow" nuance the retired db_ping probe measured;
        # the 0/1 connected gauge doesn't discriminate slow from failed.
        "expr": (
            "histogram_quantile(0.99, "
            "sum(rate(poindexter_postgres_query_latency_seconds_bucket[5m])) "
            "by (le)) > {threshold.postgres_p99_latency_seconds}"
        ),
        "for": "5m",
        "severity": "warning",
        "category": "infra",
        "summary": "Postgres SELECT 1 p99 latency exceeding threshold",
        "description": (
            "SELECT 1 round-trip p99 > threshold for 5m. DB is reachable "
            "but degraded — check for long-running queries (pg_stat_activity), "
            "connection-pool saturation, or disk I/O pressure."
        ),
    },
    # GH-227 — alert on schema drift (worker on stale schema).
    "UnappliedMigrationsDrift": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "1m",
        # Catches the failure mode where a container is rebuilt with
        # new migration files but the running worker process wasn't
        # restarted, so run_migrations() never re-ran. The /api/health
        # JSON probe surfaces this but Alertmanager couldn't see it
        # until the gauge landed in metrics_exporter.
        #
        # last_over_time bridges deploy-restart scrape holes — drift arises
        # exactly during dev-heavy (= restart-heavy) sessions, and the raw
        # read reset the 30m clock on every restart (the QaRailFullySkipped
        # / poindexter#839 failure shape). last, NOT max: a restart re-runs
        # migrations on boot, i.e. the restart IS the remediation, so the
        # fresh 0 must clear the alert immediately rather than a held max
        # keeping it firing for up to 1h after the fix.
        "expr": "last_over_time(poindexter_unapplied_migrations_count[1h]) > 0",
        "for": "30m",
        "severity": "warning",
        "category": "infra",
        "summary": "Worker is on a stale schema — restart to apply pending migrations",
        "description": (
            "poindexter_unapplied_migrations_count > 0 for 30m. The container "
            "has migration files on disk that aren't in schema_migrations. "
            "Pull a fresh main + restart poindexter-worker — the migration "
            "runner applies pending migrations on boot. To inspect first: "
            "`poindexter migrate status` (or `--json` for machine-readable)."
        ),
    },
    # --- GPU hardware (audit C3, poindexter#653) ---
    # These are the SOLE GPU temperature/VRAM alert path. The former Grafana SQL
    # rules "GPU Temperature High" (#14) and "GPU Metrics Stale" (#13) were removed
    # 2026-06-18 — they read the `gpu_metrics` DB table fed by the hand-started
    # scripts/gpu-scraper.py, causing the stale-metrics rule to flap whenever the
    # scraper wasn't running. These Prometheus rules source nvidia_gpu_* directly
    # from the container-supervised nvidia-smi exporter (job="nvidia-smi"); a dead
    # exporter is caught by the static WindowsExporterDown alert in infrastructure.yml.
    "GpuTemperatureHigh": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "30s",
        "expr": "nvidia_gpu_temperature_celsius > {threshold.gpu_temperature_celsius}",
        "for": "2m",
        "severity": "critical",
        "category": "infra",
        "summary": "GPU temperature above safe ceiling",
        "description": (
            "nvidia_gpu_temperature_celsius > threshold for 2m (container "
            "exporter, job=nvidia-smi). Sustained high temp risks thermal "
            "throttling or hardware damage. Check airflow/fans, pause heavy "
            "GPU jobs (Ollama/image-gen), and confirm the card isn't dust-clogged."
        ),
    },
    "GpuCountBelowExpected": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "30s",
        # Inert until an operator sets expected_gpu_count > 0: with the "0"
        # default the expr is `nvidia_gpu_count < 0`, which never matches.
        "expr": "nvidia_gpu_count < {threshold.expected_gpu_count}",
        # 10m: long enough to ride out a gpu-exporter recreate (the fix for
        # this very condition) without paging about its own remedy.
        "for": "10m",
        "severity": "warning",
        "category": "infra",
        "summary": "Fewer GPUs visible than this host expects",
        "description": (
            "nvidia_gpu_count is below app_settings "
            "prometheus.threshold.expected_gpu_count. A card is present in the "
            "machine but invisible to monitoring — usually because the "
            "gpu-exporter container was CREATED before the card was "
            "enumerated (NVIDIA device nodes bind at create time, so a "
            "restart is NOT enough). Check `nvidia-smi -L` on the host against "
            "`docker exec poindexter-gpu-exporter ls /dev | grep nvidia`; if "
            "they disagree, recreate the container: "
            "`docker compose -f docker-compose.local.yml --profile linux-gpu "
            "up -d --force-recreate gpu-exporter`."
        ),
    },
    # VRAM *capacity*, computed from used/total — NOT
    # `nvidia_gpu_memory_utilization_percent`, which this rule used until
    # Glad-Labs/poindexter#920. That metric mirrors nvidia-smi's
    # `utilization.memory`: the percent of TIME in the sample window that memory
    # was being read or written — a bandwidth/activity measure. It is unrelated
    # to how full VRAM is, so a `> 95` capacity threshold on it was structurally
    # incapable of firing. Measured on the operator stack 2026-07-26:
    #
    #   GPU 0 (5090): 21041/32607 MiB = 64.5% FULL, utilization.memory =  1
    #   GPU 1 (3090): 20472/24576 MiB = 83.3% FULL, utilization.memory =  0
    #
    # A card at 83% capacity reported 0. The exporter already publishes the two
    # gauges that answer the real question, so this is a pure expr fix.
    "GpuVramHigh": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "30s",
        "expr": (
            "100 * nvidia_gpu_memory_used_mib / nvidia_gpu_memory_total_mib > "
            "{threshold.gpu_vram_utilization_percent}"
        ),
        "for": "5m",
        "severity": "warning",
        "category": "infra",
        "summary": "GPU VRAM capacity critically high",
        "description": (
            "GPU {{ $labels.gpu }} is {{ $value | humanize }}% FULL "
            "(nvidia_gpu_memory_used_mib / nvidia_gpu_memory_total_mib) for 5m "
            "— VRAM capacity is nearly exhausted, so the next Ollama/image-gen/"
            "wan model load may OOM. Unload idle models (the gpu_scheduler "
            "should), or reduce concurrent model residency. Tune via "
            "prometheus.threshold.gpu_vram_utilization_percent."
        ),
    },
    # --- Mains supply quality (Glad-Labs/poindexter#924) ---
    # Reads psu_line_voltage_volts from the Shelly wall-plug meter (job
    # "nvidia-smi", gpu-exporter:9835) — an independent exporter, so per the
    # restart-gap policy above a raw instant read needs no last_over_time
    # bridge.
    #
    # The two bands are deliberately DISJOINT (warning is floored at the
    # critical threshold) rather than nested. Alertmanager's inhibit rule
    # keys on `equal: ["alertname", "instance"]`, so a nested pair with
    # different alertnames would page twice for one brownout. Disjoint bands
    # page exactly once and move the notification from warning to critical as
    # the sag deepens.
    #
    # Both are inert until an operator sets psu_nominal_line_voltage_volts:
    # at the "0" default each comparison reduces to `< 0`, which no sample
    # matches.
    "MainsVoltageLow": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        "expr": (
            "psu_line_voltage_volts"
            " >= ({threshold.psu_nominal_line_voltage_volts}"
            " * {threshold.psu_line_voltage_critical_percent} / 100)"
            "\nand psu_line_voltage_volts"
            " < ({threshold.psu_nominal_line_voltage_volts}"
            " * {threshold.psu_line_voltage_warning_percent} / 100)"
        ),
        # 30m: a supply problem worth acting on persists for hours (the
        # 2026-07-27 afternoon sag ran ~4h). 5m proved too short in practice —
        # this host's OWN GPU jobs pull the line into the warning band for
        # 5-10min at a stretch, so the rule paged on ordinary work instead of
        # on supply quality: measured over the 7 days to 2026-07-28, the
        # warning band covered 18.6% of the window (~31h/week). 30m outlasts a
        # single inference run while still catching the multi-hour afternoon
        # sags that actually precede a hard cut.
        #
        # Note this only de-noises the *warning*. MainsVoltageCritical stays at
        # for: 1m — below 87% of nominal a dropout is imminent and 30m of
        # silence would be 30m too many.
        #
        # Exempt from the long-for restart-gap ratchet (see the test of the
        # same name): psu_line_voltage_volts comes from gpu-exporter, an
        # independent container that does not blank on worker deploys, so the
        # raw instant read survives a 30m pending window.
        "for": "30m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "Mains voltage below spec ({{ $value | humanize }}V)",
        "description": (
            "Measured outlet voltage is {{ $value | humanize }}V — under "
            "prometheus.threshold.psu_line_voltage_warning_percent (default "
            "92% of nominal, the ANSI C84.1 Range B lower bound). Sustained "
            "undervoltage ends in an uncommanded hard power-off that leaves "
            "NO journal entry, no MCE and no thermal trace — it reads as a "
            "mystery reboot. Check whether the sag tracks this host's own "
            "draw (compare psu_line_voltage_volts against "
            "psu_total_power_watts on the Hardware & Power board): if it "
            "does, the branch circuit or receptacle upstream of the plug has "
            "high impedance and wants an electrician — a warm plug or outlet "
            "is a fire risk, not just a crash risk. If it does not, the sag "
            "is utility-side and wants a line-interactive UPS with AVR."
        ),
    },
    "MainsVoltageCritical": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # `> 0` guard: a meter that reports a literal 0.0 on a failed read
        # would otherwise page critical every scrape. A genuinely absent
        # Shelly omits the series entirely, which yields no samples and is
        # already safe.
        "expr": (
            "psu_line_voltage_volts > 0"
            "\nand psu_line_voltage_volts"
            " < ({threshold.psu_nominal_line_voltage_volts}"
            " * {threshold.psu_line_voltage_critical_percent} / 100)"
        ),
        "for": "1m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "Mains voltage approaching brownout dropout ({{ $value | humanize }}V)",
        "description": (
            "Measured outlet voltage is {{ $value | humanize }}V — under "
            "prometheus.threshold.psu_line_voltage_critical_percent (default "
            "87% of nominal). Typical ATX PSUs drop out around 85-90% of "
            "nominal, so a hard power-off is imminent rather than "
            "theoretical: the operator rig lost power at 93.5V on "
            "2026-07-23. Shed load NOW (pause GPU jobs — draw and sag are "
            "coupled when the circuit is the problem) and stop Postgres "
            "cleanly if you can, so the crash doesn't land mid-write."
        ),
    },
    # --- UPS via NUT (Glad-Labs/poindexter#958) ---
    # All six rules read network_ups_tools_* from the profile-gated
    # nut-exporter (job="nut", host networking → the host's upsd). The
    # exporter is independent of worker deploys, but unlike the Shelly rules
    # above these DO wrap reads in last_over_time[10m]: the highest-stakes
    # firing window is a power outage, exactly when containers get restarted
    # (host reboot on power-loss recovery, operator load-shedding), and a
    # single missed scrape on a raw read would resolve-then-refire a live
    # `send_resolved` page — a false all-clear mid-outage. last (never max):
    # power-restored / load-shed / recharged truths must propagate with the
    # first fresh sample.
    "UpsOnBattery": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # OB is the transfer-relay truth from the UPS itself. `for: 1m` skips
        # the sub-minute sag transfers a line-interactive UPS exists to absorb
        # (the operator rig logged several during 2026-07 brownouts) while
        # paging ~90s into a real outage — early enough to act well before
        # NUT's own low-battery forced shutdown.
        "expr": (
            'last_over_time(network_ups_tools_ups_status{flag="OB"}[10m]) == 1'
        ),
        "for": "1m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "UPS {{ $labels.ups }} is ON BATTERY — utility power lost",
        "description": (
            "The UPS transferred to battery over a minute ago and has not "
            "returned to line power. The host is running on inverter runtime "
            "now (see the UPS row on the Hardware & Power board for charge "
            "and estimated minutes left). NUT will force a clean shutdown at "
            "its configured floor; before that: shed GPU load (pause "
            "image/video jobs) to stretch runtime, and finish anything "
            "mid-write. If other gear shares the battery bank, remember the "
            "inverter rating is shared — the 2026-07-31 trip was an overload "
            "DURING a transfer, not a dead battery."
        ),
    },
    "UpsLowBattery": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # Gated on OB (`and on(ups)` — only the status series carries the
        # flag label) so a normal post-outage recharge, which sits below any
        # sane floor for a while ON LINE, never pages. Fires only while
        # actually discharging. Threshold sits above the driver's own
        # battery.charge.low so the page precedes NUT's forced shutdown.
        "expr": (
            "last_over_time(network_ups_tools_battery_charge[10m])"
            " < {threshold.ups_battery_charge_critical_percent}"
            "\nand on(ups) "
            'last_over_time(network_ups_tools_ups_status{flag="OB"}[10m]) == 1'
        ),
        "for": "1m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "UPS {{ $labels.ups }} battery below {{ $value | humanize }}% while discharging",
        "description": (
            "Still on battery AND the charge has fallen under "
            "prometheus.threshold.ups_battery_charge_critical_percent "
            "(default 50%). NUT's forced shutdown fires at its own lower "
            "floor (battery.charge.low / battery.runtime.low — drawn on the "
            "Hardware & Power UPS panels), so this is the last comfortable "
            "window to act: stop Postgres-heavy work, let the pipeline "
            "drain, and if the outage looks long, shut down non-essential "
            "gear sharing the battery bank."
        ),
    },
    "UpsCommsLost": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # Count-vs-expected (the GpuCountBelowExpected pattern) rather than
        # absent(): count() yields NO sample when the series family vanishes,
        # so `or vector(0)` supplies the zero — and the "0" default renders
        # the comparison `< 0`, which never matches, keeping UPS-less hosts
        # silent. Deliberately a RAW read (no last_over_time): absence IS the
        # signal here, and bridging would delay it; `for: 10m` rides out
        # exporter restarts/recreates instead (the GpuCountBelowExpected
        # rationale — don't page about the remedy).
        "expr": (
            '(count(network_ups_tools_ups_status{flag="OB"}) or vector(0))'
            " < {threshold.expected_ups_count}"
        ),
        "for": "10m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "UPS telemetry gone — fewer UPSes visible than expected",
        "description": (
            "network_ups_tools_ups_status has reported fewer UPSes than "
            "prometheus.threshold.expected_ups_count for 10m. Every UPS "
            "alert (on-battery, low-battery, overload) is blind until this "
            "recovers — and outages are exactly when it must not be. Check "
            "in order: `docker ps | grep nut-exporter` (is the `ups` compose "
            "profile active?), `upsc <name>@localhost` on the host (upsd / "
            "driver health), `systemctl status nut-server "
            "nut-driver@<name>`, and the USB data cable to the UPS — a "
            "power-strip-only connection powers the load fine while "
            "reporting nothing."
        ),
    },
    "UpsInputVoltageLow": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # Chronic-undervoltage ADVISORY from the UPS's own input tap —
        # complements the Shelly-based MainsVoltage* pair with a second,
        # independent meter that keeps reading even if the plug meter moves
        # or dies. The `> 0` guard keeps outages out of it: on battery the
        # UPS reports input.voltage 0, which is UpsOnBattery's story, not a
        # sag. for: 30m because the condition worth an advisory is the
        # multi-hour afternoon sag (the operator house ran 93-114V for DAYS
        # against a 120V nominal, 2026-07), not a passing dip — and 30m
        # requires the last_over_time bridge per the restart-gap ratchet.
        "expr": (
            "last_over_time(network_ups_tools_input_voltage[10m]) > 0"
            "\nand last_over_time(network_ups_tools_input_voltage[10m])"
            " < {threshold.ups_input_voltage_low_volts}"
        ),
        "for": "30m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "UPS input voltage chronically low ({{ $value | humanize }}V)",
        "description": (
            "The UPS has measured its input under "
            "prometheus.threshold.ups_input_voltage_low_volts for 30+ "
            "minutes — chronic supply undervoltage, the precursor condition "
            "to sag-transfers and brownout crashes. The AVR is absorbing it "
            "for now (compare input vs output voltage on the Hardware & "
            "Power UPS row; BOOST in the status timeline = the relay doing "
            "work). Log the reading for the utility complaint, and expect "
            "transfer events if it deepens. On a 120V service, 114V is the "
            "ANSI C84.1 Range A lower bound — below it the utility is out "
            "of spec, not you."
        ),
    },
    # The two load bands are DISJOINT (warning is ceilinged at the critical
    # threshold) for the same Alertmanager-inhibit reason as MainsVoltage*
    # above: one overload episode should page once, escalating warning →
    # critical as it deepens, never both at once.
    "UpsLoadHigh": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        "expr": (
            "last_over_time(network_ups_tools_ups_load[10m])"
            " >= {threshold.ups_load_warning_percent}"
            "\nand last_over_time(network_ups_tools_ups_load[10m])"
            " < {threshold.ups_load_critical_percent}"
        ),
        # 5m: a GPU render burst legitimately visits the 80s for a few
        # minutes; a SUSTAINED sit in the band means the standing load has
        # grown into the inverter's margin.
        "for": "5m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "UPS load {{ $value | humanize }}% of inverter rating for 5m",
        "description": (
            "Sustained load between "
            "prometheus.threshold.ups_load_warning_percent (default 85%) and "
            "the critical band. The margin that absorbs GPU bursts during a "
            "transfer is nearly spent — the 2026-07-31 trip happened at "
            "~106% DURING a sag transfer. Move non-essential gear off the "
            "battery-backed outlets to the surge-only bank, or cap GPU power "
            "draw (`nvidia-smi -pl`). The watts behind the percent are on "
            "the Hardware & Power UPS row (load × the unit's rated W)."
        ),
    },
    "UpsLoadCritical": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        "expr": (
            "last_over_time(network_ups_tools_ups_load[10m])"
            " >= {threshold.ups_load_critical_percent}"
        ),
        # 1m: at ≥95% of rating the next burst or transfer trips the
        # inverter — the 30m patience of the advisory tier would be 30m
        # too long (the MainsVoltageCritical asymmetry).
        "for": "1m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "UPS load {{ $value | humanize }}% — inverter trip imminent",
        "description": (
            "Load has held at or above "
            "prometheus.threshold.ups_load_critical_percent (default 95%) of "
            "the inverter rating for a minute. If the line sags NOW, the "
            "transfer lands on an overloaded inverter and output drops — "
            "that is precisely the 2026-07-31 outage (910W PC burst + ~150W "
            "shared gear on a 1000W unit). Shed load immediately: pause GPU "
            "jobs, and get anything non-essential off the battery-backed "
            "outlets."
        ),
    },
    # Host disk space. Previously static in infrastructure.yml — moved here so
    # operators can tune the 20/10 GB and 200 GB total-size cutoffs via DB.
    # Binary up/down rules (Worker, Postgres, Ollama) remain static because
    # they have no operator-tunable numbers and must fire even when the DB is
    # down (which is when dynamic rendering would fail).
    "PoindexterDiskSpaceLow": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # absent() guard: fires (with no mountpoint label) when node_exporter dies
        # and the metric family disappears entirely — belt-and-suspenders alongside
        # the static NodeExporterDown rule in infrastructure.yml. poindexter#705.
        # fstype filter = real local filesystems only (skips tmpfs/squashfs/ntfs
        # rescue mounts); was volume=~"[A-Z]:" on windows_exporter pre-migration.
        "expr": (
            '(node_filesystem_free_bytes{fstype=~"ext4|xfs|btrfs"} / (1024*1024*1024)'
            " < {threshold.disk_free_warning_gb})"
            " and on(mountpoint) "
            '(node_filesystem_size_bytes{fstype=~"ext4|xfs|btrfs"} / (1024*1024*1024)'
            " > {threshold.disk_min_total_gb})"
            "\nor absent(node_filesystem_free_bytes)"
        ),
        "for": "10m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "Host disk {{ $labels.mountpoint }} has under 20 GB free",
        "description": (
            "Filesystem {{ $labels.mountpoint }} on the worker host has "
            "{{ $value | humanize }} GB free (warning threshold: "
            "prometheus.threshold.disk_free_warning_gb, default 20 GB). Run "
            "`docker builder prune -af && docker image prune -af`, clear old "
            "model files, or archive cost_logs / page_views."
        ),
    },
    "PoindexterDiskSpaceCritical": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        "expr": (
            '(node_filesystem_free_bytes{fstype=~"ext4|xfs|btrfs"} / (1024*1024*1024)'
            " < {threshold.disk_free_critical_gb})"
            " and on(mountpoint) "
            '(node_filesystem_size_bytes{fstype=~"ext4|xfs|btrfs"} / (1024*1024*1024)'
            " > {threshold.disk_min_total_gb})"
            "\nor absent(node_filesystem_free_bytes)"
        ),
        "for": "5m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "Host disk {{ $labels.mountpoint }} has under 10 GB free — imminent",
        "description": (
            "Filesystem {{ $labels.mountpoint }} on the worker host is down to "
            "{{ $value | humanize }} GB free. Postgres writes, Docker pulls, "
            "and image generation will start failing. Free space now: "
            "`docker builder prune -af && docker image prune -af` (the 2026-07-23 "
            "root-fill shut Postgres down mid-WAL-redo — act immediately)."
        ),
    },
    # Brain database capacity warning. Replaces the Grafana-managed SQL alert
    # (brain-db-size-warning) that polled pg_database_size('poindexter_brain')
    # every minute — postgres_exporter already exports pg_database_size_bytes
    # (poindexter#735 item 2). No absent() guard: db-size is the documented
    # non-page-worthy capacity warning (#581), so a missing metric must NOT
    # fire — a bare `> N` naturally yields no series when the exporter is down.
    "PoindexterBrainDbSizeWarning": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "1m",
        "expr": (
            'pg_database_size_bytes{datname="poindexter_brain",job="postgres-exporter"}'
            " / (1024*1024*1024) > {threshold.brain_db_size_warning_gb}"
        ),
        "for": "5m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "Brain database exceeds 5 GB",
        "description": (
            "poindexter_brain database size is {{ $value | humanize }} GB "
            "(warning threshold: prometheus.threshold.brain_db_size_warning_gb, "
            "default 5 GB). Consider archiving old data or pruning "
            "retention_policies rows (sensor_samples, cost_logs, embeddings)."
        ),
    },
    # Per-container memory ceiling (2026-07-02 observability review — the
    # langfuse-clickhouse 15 GB spikes and the cadvisor VM-OOM cascade
    # (#2019/#2021) had cAdvisor *panels* but no alert anywhere). Sustained
    # 30m so model loads / build bursts don't page. The model-serving
    # containers (image-gen-server holds ~19 GB of diffusion weights, wan
    # holds the i2v model) are excluded by name — their large RSS is the
    # workload, not a leak. No absent() guard: capacity warning, not a
    # liveness page (#581 no-data rule).
    # Working set, NOT `container_memory_usage_bytes` — see #920's sibling
    # finding. `usage_bytes` includes reclaimable page cache, so it overstates
    # memory pressure badly for any container that reads large files: on the
    # operator stack 2026-07-26, image-gen-server measured 10.72 GB usage vs
    # 3.63 GB working set (3x), purely because it had streamed multi-GB model
    # weights off disk. `working_set_bytes` (usage minus inactive file cache) is
    # what the kernel OOM killer actually acts on, and it's what the
    # description already claimed the rule measured ("GB RSS").
    #
    # That overstatement is also why the two model-loading containers were
    # excluded here: at 10.72 GB, image-gen-server sat permanently above the
    # 8 GB threshold. The exclusion silenced the false page but cost real
    # coverage — image-gen and wan are the containers most likely to genuinely
    # OOM (they're the ones loading models; cf. poindexter#907 on wan-server's
    # OOM behaviour). On working_set they measure 3.63 GB and 0.02 GB, so the
    # exclusion is no longer needed and the coverage comes back. Max working
    # set across the whole stack at restore time was 3.63 GB against an 8 GB
    # threshold, so this does not arm a new false page.
    "PoindexterContainerMemoryHigh": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "1m",
        "expr": (
            'container_memory_working_set_bytes{job="cadvisor",image!="",'
            'name=~"poindexter-.*"}'
            " / (1024*1024*1024) > {threshold.container_memory_warning_gb}"
        ),
        "for": "30m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "Container {{ $labels.name }} memory high for 30m",
        "description": (
            "{{ $labels.name }} has held {{ $value | humanize }} GB working set "
            "(non-reclaimable — what the OOM killer counts) for 30 minutes "
            "(warning threshold: "
            "prometheus.threshold.container_memory_warning_gb, default 8 GB). "
            "Known offenders: langfuse-clickhouse merge spikes, cadvisor "
            "unbounded growth (VM-OOM cascade, glad-labs-stack#2019). "
            "Check the System Health 'Memory Usage by Container' panel for "
            "the growth shape, then `docker restart {{ $labels.name }}` and "
            "consider a compose mem_limit if it recurs."
        ),
    },
    # Host RAM pressure (2026-07-10 desktop-freeze investigation). The worker
    # host oversubscribes RAM — the container stack, the host inference fleet,
    # and the operator desktop share one pool. There was no host-memory alert
    # before this (only per-container cAdvisor + GPU-VRAM rules), so the
    # pressure that actually froze the desktop was invisible to Alertmanager.
    # Both source node_exporter (job="node"; windows_exporter pre-migration).
    # No absent() guard on either: a bare comparison yields no series when the
    # exporter is down, so neither false-fires on no-data — NodeExporterDown
    # owns exporter death (#581).
    "PoindexterHostMemoryLow": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # available = truly-free + reclaimable-standby. Sustained below the
        # floor means the desktop + WSL + inference have crowded out headroom
        # and the next allocation spike will page.
        "expr": (
            "node_memory_MemAvailable_bytes / (1024*1024*1024) < "
            "{threshold.host_memory_available_warning_gb}"
        ),
        "for": "10m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "Host available RAM below the warning floor for 10m",
        "description": (
            "node_memory_MemAvailable_bytes has stayed under the warning floor "
            "(prometheus.threshold.host_memory_available_warning_gb, default "
            "4 GB) for 10m. The host is oversubscribed: the container stack, "
            "the host Ollama/inference fleet, and the desktop are competing "
            "for RAM, and when available reaches ~0 the desktop compositor "
            "stalls. Levers: close idle browser/Electron apps, `docker "
            "restart` a bloated container (see the Database + Hardware & "
            "Power boards for the growth shape), or unpin an idle "
            "KEEP_ALIVE=-1 model. Durable fix is de-oversubscription (move "
            "the container tier off the box, or add RAM)."
        ),
    },
    "PoindexterHostMemoryThrashing": {
        "enabled": True,
        "group": "poindexter-infrastructure",
        "interval": "30s",
        # PSI full-stall: rate() over the kernel's cumulative "all non-idle
        # tasks blocked on memory" clock = fraction of wall-clock the whole
        # system spent stalled on reclaim — the desktop-freeze mechanism
        # itself, and indifferent to WHERE swapped pages land. The previous
        # expr (rate(node_vmstat_pswpout[5m]) > 2000, calibrated on
        # windows_exporter pagefile writes) broke on Pop!_OS: zram at
        # priority 1000 absorbs swap first, so pswpout counts cheap
        # compressed-RAM page-outs and the rule flapped ~131 criticals in
        # its first two Linux weeks while PSI full-stall stayed under 3%.
        # Healthy baseline is <0.01; a genuine episode climbs past 0.25 in
        # the minutes before the desktop locks — the lead time this rule
        # exists to give. Critical → Telegram: rare by construction again.
        "expr": (
            "rate(node_pressure_memory_stalled_seconds_total[5m]) > "
            "{threshold.host_memory_psi_full_stall_critical_ratio}"
        ),
        "for": "2m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "Host is thrashing — sustained full memory-pressure stall",
        "description": (
            "PSI full memory pressure has exceeded "
            "prometheus.threshold.host_memory_psi_full_stall_critical_ratio "
            "(default 0.25 = all runnable tasks stalled on memory reclaim "
            "25% of wall-clock) for 2m — the precursor to a desktop freeze / "
            "hard reset. Physical RAM is exhausted; compressed-swap headroom "
            "is no longer keeping up. "
            "Intervene now: close browser/Electron apps to free RAM, or pause "
            "heavy pipeline work (image/video generation). If this recurs, the "
            "host is structurally oversubscribed — reduce resident load or move "
            "the container tier onto separate hardware."
        ),
    },
    # Postgres connection-pool saturation. Previously static in
    # postgres-connections.yml — moved here so operators can tune the
    # 0.80/0.95 ratio thresholds via DB.
    "PoindexterPostgresConnectionsHigh": {
        "enabled": True,
        "group": "poindexter-postgres-connections",
        "interval": "30s",
        "expr": "(pg_connections_used / pg_connections_max) >= {threshold.postgres_connection_warning_ratio}",
        "for": "5m",
        "severity": "warning",
        "category": "infrastructure",
        "summary": "Postgres connection utilization over 80%",
        "description": (
            "Postgres is using {{ $value | humanizePercentage }} of max_connections. "
            "The stress test that motivated GH-92 failed at 100% of the old "
            "100-connection cap; firing at 80% gives a 5-minute buffer. "
            "Check: `docker exec poindexter-postgres-local psql -U poindexter "
            "-d poindexter_brain -c \"SELECT application_name, COUNT(*) FROM "
            "pg_stat_activity GROUP BY application_name ORDER BY 2 DESC;\"`"
        ),
    },
    "PoindexterPostgresConnectionsCritical": {
        "enabled": True,
        "group": "poindexter-postgres-connections",
        "interval": "30s",
        "expr": "(pg_connections_used / pg_connections_max) >= {threshold.postgres_connection_critical_ratio}",
        "for": "2m",
        "severity": "critical",
        "category": "infrastructure",
        "summary": "Postgres connection utilization over 95% — imminent exhaustion",
        "description": (
            "Postgres is at {{ $value | humanizePercentage }} of max_connections. "
            "At this level new asyncpg acquires will start failing within minutes. "
            "Shed load or restart offending services immediately."
        ),
    },
    "OllamaNoModelsLoaded": {
        "enabled": True,
        "group": "poindexter-infra",
        "interval": "1m",
        # "Up but empty" — /api/tags returns 200 with []. The existing
        # OllamaReachable gauge passes this case, which is why it needs
        # a dedicated alert.
        #
        # The ``unless poindexter_ollama_reachable == 0`` guard prevents a
        # FALSE critical when a transient /api/tags timeout (e.g. under heavy
        # GPU render load) trips metrics_exporter's except-branch, which zeroes
        # BOTH gauges at once. That case is "Ollama unreachable" — already owned
        # by the static PoindexterOllamaDown (reachable==0) alert — not "up but
        # no models". Without the guard it double-fired a spurious "up but no
        # models" critical during a media render (observed 2026-06-21 18:21).
        # The guard keeps ONLY the genuine up-but-empty signal (reachable=1,
        # count=0).
        "expr": (
            "poindexter_ollama_model_count == 0 "
            "unless poindexter_ollama_reachable == 0"
        ),
        "for": "2m",
        "severity": "critical",
        "category": "infra",
        "summary": "Ollama is up but has no models loaded",
        "description": (
            "Ollama /api/tags returns 200 but the models list is empty. "
            "Pipeline can't generate anything. Run `ollama pull gemma3:27b` "
            "(and the configured embed model) on the GPU host."
        ),
    },
    # --- Business / cost ---
    # Cost alerts include an ``unless approval_queue_length > 0`` cross-check
    # so they don't fire while the pipeline is throttling on pending human
    # approvals (Gitea #238 — matches the retired cost_freshness probe's
    # ``expected_idle`` logic).
    "DailySpendApproachingLimit": {
        "enabled": True,
        "group": "poindexter-business",
        "interval": "1m",
        "expr": (
            "poindexter_daily_spend_usd > {threshold.daily_spend_warning_usd} "
            "unless poindexter_approval_queue_length > 0"
        ),
        "for": "5m",
        "severity": "warning",
        "category": "business",
        "summary": "Daily TOTAL spend (API + electricity) running high",
        "description": (
            "poindexter_daily_spend_usd is the TOTAL axis — paid cloud API "
            "PLUS measured electricity — against "
            "prometheus.threshold.daily_spend_warning_usd (default $4). "
            "Measured electricity alone runs ~$1.50-1.90/day, so this can trip "
            "with ZERO paid calls; check the Cost dashboard's api-vs-electricity "
            "split before assuming a provider flip. If the API axis did move, "
            "check plugin.llm_provider.primary.* for a workflow that flipped to "
            "a paid provider. The API-axis hard cap (daily_spend_limit_usd) is "
            "enforced separately by cost_guard and is NOT what fired here."
        ),
    },
    "DailySpendOverBudget": {
        "enabled": True,
        "group": "poindexter-business",
        "interval": "1m",
        "expr": (
            "poindexter_daily_spend_usd > {threshold.daily_spend_critical_usd} "
            "unless poindexter_approval_queue_length > 0"
        ),
        "for": "2m",
        "severity": "critical",
        "category": "business",
        "summary": "Daily TOTAL spend (API + electricity) over the critical line",
        "description": (
            "poindexter_daily_spend_usd (TOTAL axis — paid cloud API PLUS "
            "measured electricity) crossed "
            "prometheus.threshold.daily_spend_critical_usd (default $5). "
            "This does NOT mean cost_guard failed: cost_guard's hard cap gates "
            "the API axis against daily_spend_limit_usd ($2) and never gates "
            "this quantity. The total axis is governed by the soft throttle "
            "(cost_throttle_daily_budget_usd, $3), which fails OPEN by design — "
            "so the escalation ladder is throttle $3 → warn $4 → page $5. "
            "Read the Cost dashboard's api-vs-electricity split FIRST: if "
            "electricity dominates, this is a power/rate story, not a runaway "
            "pipeline. Only if the API axis is high should you suspect the gate."
        ),
    },
    "MonthlySpendHigh": {
        "enabled": True,
        "group": "poindexter-business",
        "interval": "1m",
        # last_over_time bridges deploy-restart scrape holes so the 10m clock
        # doesn't reset mid-ramp (353 pending-minutes of chopped ramps in the
        # 14d before 2026-07-11 — it still fired eventually because monthly
        # spend is cumulative, but detection lagged by up to the deploy-burst
        # length). last, NOT max: the gauge legitimately drops to ~0 at month
        # rollover, and a held max would false-fire for up to 1h into the new
        # month (for: 10m < 1h lookback).
        "expr": (
            "last_over_time(poindexter_monthly_spend_usd[1h]) > "
            "{threshold.monthly_spend_warning_usd}"
        ),
        "for": "10m",
        "severity": "warning",
        "category": "business",
        "summary": "Monthly AI spend running high",
        "description": (
            "Running high for the expected Ollama-only defaults. Review "
            "cost_logs to see which provider is driving spend."
        ),
    },
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


THRESHOLD_PREFIX = "prometheus.threshold."
RULE_PREFIX = "prometheus.rule."


async def load_thresholds(pool_or_conn: Any) -> dict[str, str]:
    """Return ``threshold_name -> value`` with defaults filled in.

    Reads every ``prometheus.threshold.*`` row and overlays onto
    :data:`DEFAULT_THRESHOLDS`, so any key the operator hasn't touched
    still has a sane default at render time.
    """
    out = dict(DEFAULT_THRESHOLDS)
    rows = await _fetch_prefix(pool_or_conn, THRESHOLD_PREFIX)
    for key, raw in rows:
        name = key[len(THRESHOLD_PREFIX):]
        if raw is None:
            continue
        out[name] = str(raw).strip()
    return out


async def load_rules(pool_or_conn: Any) -> dict[str, dict[str, Any]]:
    """Return ``alert_name -> rule-dict`` with defaults + DB overrides merged.

    Unknown rule names (rows without a corresponding default) are
    accepted as-is — the DB can define entirely new alerts. Partial
    overrides merge shallowly on top of the default.
    """
    import json

    merged: dict[str, dict[str, Any]] = {}
    for name, default in DEFAULT_RULES.items():
        merged[name] = dict(default)

    rows = await _fetch_prefix(pool_or_conn, RULE_PREFIX)
    for key, raw in rows:
        name = key[len(RULE_PREFIX):]
        if not raw:
            continue
        try:
            override = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "prometheus.rule.%s has malformed JSON; ignoring override", name
            )
            continue
        if not isinstance(override, dict):
            logger.warning(
                "prometheus.rule.%s is not a JSON object; ignoring override", name
            )
            continue
        base = merged.get(name) or {}
        base.update(override)
        merged[name] = base

    return merged


async def _fetch_prefix(
    pool_or_conn: Any, prefix: str
) -> list[tuple[str, str | None]]:
    """SELECT key, value FROM app_settings WHERE key LIKE prefix||'%'."""
    # Escape SQL LIKE wildcards from the prefix just in case.
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await pool_or_conn.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE $1 ESCAPE '\\'",
        f"{escaped}%",
    )
    return [(r["key"], r["value"]) for r in rows]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _substitute_thresholds(expr: str, thresholds: dict[str, str]) -> str:
    """Replace ``{threshold.X}`` tokens in an expr with the numeric value."""
    out = expr
    # Cheap literal substitution — there are never enough thresholds to
    # make a regex worth it, and {a} style placeholders would conflict
    # with PromQL's braces.
    for name, value in thresholds.items():
        out = out.replace("{threshold." + name + "}", value)
    return out


def render_yaml(
    thresholds: dict[str, str],
    rules: dict[str, dict[str, Any]],
) -> str:
    """Render rules to the YAML format Prometheus's ``rule_files`` expects.

    Groups rules by their ``group`` field, preserving the interval of
    the first rule seen per group. Disabled rules are omitted entirely
    (Prometheus has no "disabled" concept — the rule just doesn't exist).
    """
    groups: dict[str, dict[str, Any]] = {}
    for name, rule in rules.items():
        if not rule.get("enabled", True):
            continue
        group_name = rule.get("group") or "poindexter-default"
        group = groups.setdefault(
            group_name,
            {"name": group_name, "interval": rule.get("interval", "1m"), "rules": []},
        )
        expr = _substitute_thresholds(str(rule.get("expr", "vector(0)")), thresholds)
        group["rules"].append(
            {
                "alert": name,
                "expr": expr,
                "for": str(rule.get("for", "5m")),
                "severity": str(rule.get("severity", "info")),
                "category": str(rule.get("category", "content")),
                "summary": str(rule.get("summary", "")),
                "description": str(rule.get("description", "")),
            }
        )

    lines: list[str] = [
        "# Rendered by RenderPrometheusRulesJob — do not edit by hand.",
        "# Source of truth: app_settings.prometheus.rule.* + prometheus.threshold.*",
        "groups:",
    ]
    for group_name in sorted(groups):
        group = groups[group_name]
        lines.append(f"  - name: {_yaml_scalar(group['name'])}")
        lines.append(f"    interval: {_yaml_scalar(group['interval'])}")
        lines.append("    rules:")
        for rule in group["rules"]:
            lines.append(f"      - alert: {_yaml_scalar(rule['alert'])}")
            lines.append(f"        expr: {_yaml_quoted(rule['expr'])}")
            lines.append(f"        for: {_yaml_scalar(rule['for'])}")
            lines.append("        labels:")
            lines.append(f"          severity: {_yaml_scalar(rule['severity'])}")
            lines.append(f"          category: {_yaml_scalar(rule['category'])}")
            lines.append("        annotations:")
            lines.append(f"          summary: {_yaml_quoted(rule['summary'])}")
            lines.append(f"          description: {_yaml_quoted(rule['description'])}")
    lines.append("")  # trailing newline
    return "\n".join(lines)


def _yaml_scalar(value: str) -> str:
    """Emit a plain YAML scalar — quote only if needed for safety."""
    text = str(value)
    if not text:
        return '""'
    if text[0] in "!&*[]{},|>'\"#%@`" or text.strip() != text:
        return _yaml_quoted(text)
    return text


def _yaml_quoted(value: str) -> str:
    """Emit a double-quoted YAML string with escaping."""
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{text}"'


async def build_current(pool_or_conn: Any) -> str:
    """Convenience: load thresholds + rules and render the YAML."""
    thresholds = await load_thresholds(pool_or_conn)
    rules = await load_rules(pool_or_conn)
    return render_yaml(thresholds, rules)
