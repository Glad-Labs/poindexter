# Wall-power metering (Shelly smart plug)

The Hardware & Power dashboard's `psu_total_power_watts` panel and the brain's
electricity-cost calc want **true wall power** — the AC watts actually pulled
from the outlet. The robust source is a smart plug that meters the outlet
directly: it reads the wall side of the UPS, includes every conversion loss the
utility actually bills, and no host software can silently stop it. (The
Windows-era USB-HID contention that originally forced this choice — iCUE owning
the HX1500i's HID, HWiNFO crashing it — is preserved under
[Historical](#historical-windows-era-sensor-split-pre-linux-migration); on
Linux the `corsair-psu` kernel driver reads the PSU without contention, but it
measures a different node — see the chain below.)

## Source priority

`brain/psu_power.py::select_power_source` picks, best → worst:

1. **`psu_total_power_watts`** — real metered wall power from a Shelly outlet
   plug (`shelly_psu_url`). Primary. _(Before the HWiNFO64 retirement this metric
   could also come from HWiNFO reading the HXi; that path is gone — see above.)_
2. **iCUE CSV tap** — `sensor_samples.psu_power_in` (`tap.corsair_csv`).
   **Dead since the Linux migration** — iCUE was Windows software, so nothing
   produces the CSV anymore; the chain skips straight past it. Listed because
   `select_power_source` still knows the source id (and a Windows operator
   could still light it up).
3. **`system_total_power_estimate_watts`** — CPU + GPU + overhead software
   estimate. Degraded; the brain pages the operator when it falls to this.

## Why a smart plug over the PSU's own USB reading

On Windows, only one process could own the HX1500i's USB HID, and it had to be
iCUE (cooling control) — the original reason the Shelly exists. On Linux that
contention is gone (see the next section), but the Shelly stays the wall-power
source of truth for reasons that survived the migration: it sits on the wall
side of the UPS (the PSU's own AC-in reads the UPS _output_ node and misses UPS
overhead and charging), it measures the true metered draw the utility bills,
and it is reboot-proof — a separate device no host software toggle can silently
stop.

## Sensor sources on Linux (current)

The metering chain, wall to silicon:

```
wall receptacle ─[Shelly plug]─▶ UPS ─[HX1500i AC-in]─▶ PSU ─▶ DC rails
                       │              (NUT reads the UPS's own wall-side
                       │               line register + battery/output)
```

| Signal                                                                         | Source                            | Path                                                                                                                 | Dashboard panel (Hardware & Power)                                      |
| ------------------------------------------------------------------------------ | --------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Wall** voltage / power / current (billed)                                    | Shelly smart plug, wall side      | Prometheus `psu_line_voltage_volts` / `psu_total_power_watts` / `psu_line_current_amps` (polled by the gpu-exporter) | Wall Power · Mains Voltage · AC voltage — full path                     |
| Wall voltage (2nd meter, 1 V steps) + battery/load                             | UPS via NUT                       | Prometheus `network_ups_tools_*` (job `nut`)                                                                         | UPS row                                                                 |
| PSU **AC-in** (UPS-output side), **DC rails**, VRM/case **temps**, PSU **fan** | `corsair-psu` kernel hwmon driver | Prometheus `node_hwmon_*` with `chip=~".*1b1c:1c1f.*"` (job `node`)                                                  | PSU DC Output · PSU rails — deviation · PSU Temperatures · Fans (hwmon) |
| CPU / board temps, board fans, board rails                                     | `k10temp` / `asusec` hwmon        | Prometheus `node_hwmon_*` (job `node`)                                                                               | Sensors — hwmon row                                                     |
| GPU temps / power / VRAM                                                       | nvidia-smi gpu-exporter           | Prometheus `nvidia_gpu_*` (job `nvidia-smi`)                                                                         | GPU rows                                                                |

The board's "How this board is metered" text panel carries this same map, so an
operator reading a chart can always tell which instrument and which physical
node produced it.

### HX1500i via corsair-psu (no more HID war)

With no iCUE on Linux, the kernel's `corsair-psu` driver auto-binds to the PSU
(USB id `1b1c:1c1f`) and node_exporter's hwmon collector picks it up with zero
configuration — AC-in voltage, 12/5/3.3 V rails with firmware crit bands,
per-rail wattage, VRM/case temperatures, and fan RPM (0 RPM = zero-RPM mode,
normal below roughly 40% load). Two DB-rendered alert rules watch it:
`PsuVrmTempHigh` (`prometheus.threshold.psu_vrm_temp_warning_celsius`, default 85) and `PsuRailVoltageOutOfBand`
(`prometheus.threshold.psu_rail_voltage_tolerance_percent`, default 5 — the ATX
tolerance), both warning-severity → Discord.

**Gotcha:** the hwmon chip label embeds the USB path + HID instance and
**rotates on every boot** (`…_0005` → `…_0006` → `…_000c` observed). Panels and
rules must match `chip=~".*1b1c:1c1f.*"` — a pinned literal label goes silent
at the next reboot. A userspace HID reader (e.g. OpenLinkHub with a PSU entry,
or liquidctl) would reintroduce the dual-reader contention — leave the PSU to
the kernel driver.

### GPU 12V-2x6 per-pin monitoring (ASUS Astral)

ROG Astral cards carry an ITE IT8915FN that reports per-pin voltage and
current for all six 12V pins of the GPU power connector — the data that
predicts the connector-melt failure mode (current concentrating on a few pins
while total board power looks normal; balanced pins share within ~1 A, the
Micro-Fit+ pin rating is 9.5 A). The chip answers at I2C address `0x2B` on one
of the card's NVIDIA I2C buses; `get_astral_pin_metrics()` in
`scripts/nvidia-smi-exporter.py` auto-detects it (read-only SMBus probe,
plausibility-checked so a stray device ACKing `0x2B` is rejected) and emits
`gpu_12vhpwr_pin_volts` / `gpu_12vhpwr_pin_current_amps` labelled
`pin="0".."5"`. No hardware → no series → the alert rules stay naturally
inert.

Three DB-rendered alert rules watch it: `GpuPowerPinCurrentHigh`
(`prometheus.threshold.gpu_pin_current_warning_amps`, default 8.0, Discord),
`GpuPowerPinCurrentCritical` (`…gpu_pin_current_critical_amps`, default 9.2 —
the community vhpwr-guard shutdown line — Telegram), and `GpuPowerPinImbalance`
(spread > `…gpu_pin_imbalance_spread_amps` (3 A) while average load >
`…gpu_pin_imbalance_min_load_amps` (2 A), Discord) — the spread is the
contact-degradation precursor that moves long before any absolute trips.
Panels live in the Hardware & Power GPU row.

Two gotchas: the I2C **bus number rotates per boot** (the reader re-scans on
any failure rather than pinning), and the containerized gpu-exporter needs the
i2c character class granted (`device_cgroup_rules: c 89:* rmw` + `/dev` bind
in `docker-compose.local.yml`) — a pinned `devices:` entry would silently
break at the next reboot. If a hard-shutdown backstop is ever wanted on top of
alerting, the open-source watchdogs (eugeneoh04/vhpwr-guard,
humza-khalid/12vhpwr-guard) read the same chip and power the machine off at
9.2–9.5 A/pin; reads through the kernel are per-transaction locked, so they
coexist with this exporter.

### Historical: Windows-era sensor split (pre-Linux migration)

Until 2026-07 the split was: AIDA64 shared memory for CPU/GPU/board temps,
voltages and power (`aida64_*`); the iCUE CSV tap (`sensor_samples`,
`source=corsair_csv`) for PSU rails and case/PSU fan RPMs — the only
contention-free HX1500i reader while iCUE owned the HID; HWiNFO retired
2026-07-10 for crashing iCUE's PSU sensor. All of these died with the OS
migration: `aida64_*` series no longer exist and the corsair_csv tap has no
producer. Case-fan RPMs (the LINK controller) are the one signal that did not
come back — OpenLinkHub owns the LINK hubs now and does not feed Prometheus.

## Setup

1. Plug **only the PC** into the Shelly (leave monitors on another outlet) for
   clean box-level draw.
2. Set the plug up **local** (AP mode → join Wi-Fi) and give it a reserved LAN
   IP. No cloud account is required — Shelly Gen2+ exposes a local RPC API.
3. Add the plug's base URL to `~/.poindexter/bootstrap.toml`:

   ```toml
   shelly_psu_url = "http://192.168.1.50"
   ```

4. Re-up the containerized exporter so the new URL rides in:
   `bash scripts/start-stack.sh up -d gpu-exporter` (start-stack exports
   `SHELLY_PSU_URL` from that bootstrap key into the container's env). If you
   instead run `scripts/nvidia-smi-exporter.py` directly on the host, there is
   nothing to restart — `_read_shelly_url_from_bootstrap()` re-parses the TOML
   on every scrape.

The exporter (`scripts/nvidia-smi-exporter.py`) polls
`http://<plug>/rpc/Switch.GetStatus?id=0`, reads `apower`, and emits
`psu_total_power_watts` (plus `psu_line_voltage_volts` and
`psu_line_current_amps`). Collection runs in a **background thread** that
refreshes a cached snapshot every ~10s; every `:9835/metrics` scrape serves that
snapshot in O(1), so a slow `nvidia-smi`/AIDA read can never blow a scraper's
timeout (see [Alert debounce](#alert-debounce) below). Prometheus scrapes
`:9835`, the Grafana panel populates, and the brain promotes the source from
`estimate` to primary.

## Alert debounce

The brain scrapes `:9835` every 5-min cycle with a 3s timeout to pick the
electricity-cost power source. Two guards keep a momentary miss from paging a
false "No real PSU data" alert:

1. **Constant-time scrapes.** The exporter collects off the request path (see
   above), so producer latency never becomes a scraper timeout. Before this the
   exporter collected synchronously per request — measured 2.5-6.7s+, with
   multi-minute wedges — so a slow `nvidia-smi`/AIDA read would exceed the 3s
   budget and the brain would lose the Shelly reading **and** the software
   estimate at once, fall to the 150W static floor, and page.
2. **Watchdog debounce.** A degraded source (software estimate / static floor)
   must persist for `psu_watchdog_degraded_cycles_before_page` **consecutive**
   brain cycles (default `3` ≈ 15 min) before it pages Telegram; a one-cycle
   miss self-heals silently. Logic is in
   `brain/psu_power.py::psu_watchdog_transition`; the streak is persisted in
   `brain_knowledge (entity='psu_watchdog', attribute='degraded_streak')` and
   the threshold is tunable via `app_settings`.

Together these fixed a 2026-07-12 incident where the alert fired ~15×/day (each
a false alarm) while the plug read ~330W the whole time.

## Verifying

```bash
curl -s http://localhost:9835/metrics | grep psu_total_power_watts
```

A non-zero value means the plug is live and the whole chain is wired. If it is
absent, the URL is unset/unreachable and the system is on the software estimate
— check `shelly_psu_url` and that the plug answers directly:

```bash
curl http://<plug-ip>/rpc/Switch.GetStatus?id=0
```

## Undervoltage alerting

The plug also meters **line voltage** (`psu_line_voltage_volts`), and that is a
health signal, not just trivia: when mains sags far enough the PSU drops out and
the host dies mid-instruction. There is no journal entry, no MCE and no thermal
trace afterwards — it reads as a mystery reboot. The only evidence is the
voltage trend leading up to it, which is why this is monitored.

Two rules in `services/prometheus_rule_builder.py` watch it
(Glad-Labs/poindexter#924):

| Alert                  | Fires below                                                                                      | Severity            |
| ---------------------- | ------------------------------------------------------------------------------------------------ | ------------------- |
| `MainsVoltageLow`      | `psu_line_voltage_warning_percent` (default 92% of nominal — the ANSI C84.1 Range B lower bound) | warning → Discord   |
| `MainsVoltageCritical` | `psu_line_voltage_critical_percent` (default 87% — approaching ATX brownout dropout)             | critical → Telegram |

The bands are **disjoint**, not nested: the warning is floored at the critical
threshold so a single brownout pages once, not twice (Alertmanager's inhibit
rule keys on `alertname`, which differs between the two).

**Both ship inert and you must opt in.** Mains nominal is regional, so there is
no safe shipped default — a 230V operator must not inherit a 120V operator's
threshold. Set your nominal and the rules activate:

```bash
poindexter settings set prometheus.threshold.psu_nominal_line_voltage_volts 120 --allow-new
```

`--allow-new` is required the first time: the shipped value lives in
`DEFAULT_THRESHOLDS` and is merged at render time, so there is no `app_settings`
row until you create one (same as `prometheus.threshold.expected_gpu_count`).

Use `230` (or your local nominal) outside North America; the thresholds are
percentages, so the absolute bounds follow automatically. `0` (the default)
disables both. `RenderPrometheusRulesJob` picks the change up within 5 minutes —
no restart needed.

### Reading the result

If the sag **tracks your own draw** — compare `psu_line_voltage_volts` against
`psu_total_power_watts` on the Hardware & Power board — the branch circuit or
receptacle upstream of the plug has high impedance. That is an electrician's
job, and a plug or outlet that is warm to the touch is a fire risk, not just a
crash risk. If the sag is **independent of your draw**, it is utility-side and
the fix is a line-interactive UPS with AVR (pure sine wave — a PFC PSU will not
accept a simulated-sine unit).

Worked example from the operator rig, 5 days of samples:

| Host draw | Mean mains V | Min    |
| --------- | ------------ | ------ |
| 200–299W  | 112.5V       | 100.0V |
| 700–799W  | 106.1V       | 93.5V  |

Pearson r = −0.33 between voltage and draw, ~1.5 Ω implied source impedance —
a circuit problem, and it hard-crashed the host twice in five days.

### What these rules cannot catch

They fire on **sustained** sag (`for: 30m` / `1m`). A sub-second utility blip is
invisible at a 30s scrape interval, and no Prometheus rule can catch one — that
is a UPS's job, not monitoring's. A quiet board means "no sustained
undervoltage", not "power is clean".

## Staleness watchdog

`brain/data_freshness_probe.py` watches the feeds behind these panels and emits
an edge-triggered `data_feed_stale` finding (→ Discord) when a producer goes
dark, so a stale wall-power number can never silently masquerade as a live one.

### iCUE CSV feed — fast detection, best-effort recovery

The `corsair_csv` feed is the one that goes dark on a reboot: iCUE's CSV
data-logging is a manual toggle that does **not** auto-resume after an iCUE
restart / PC reboot, and there is no clean programmatic re-enable — the iCUE SDK
is lighting-only (no sensor telemetry), and the only direct-sensor path contends
with iCUE the way HWiNFO did. So the watchdog is tuned to surface a drop fast:

- **Ingest cadence.** `IngestCorsairCsvJob` (`services/jobs/ingest_corsair_csv.py`)
  re-ingests `corsair_csv` every **5 min** via `tap_runner.run_all(only_names=…)`.
  Without it the feed rode only the hourly `RunTapsJob`, so `sensor_samples` sat
  up to ~60 min stale even while iCUE logging was healthy. Other taps stay on the
  hourly walk; the corsair handler is idempotent, so the two paths don't collide.
- **Threshold.** `data_freshness_feeds` sets the `corsair_csv` threshold to
  **30 min** (was 120 min under hourly ingest). With the feed now ~5-10 min
  fresh, 30 min pages on a genuinely dead sampler while still clearing a missed
  tick or a short worker restart — net detection latency ~30-35 min.

**Detection ≠ recovery.** This is a Discord heads-up (routine), not a Telegram
page: the alerting-critical wall-power number comes from the Shelly plug, which
is reboot-proof and never needs this feed. A drop that happens overnight still
stays dark until the operator re-enables iCUE logging by hand — only auto-recovery
would close that gap, and iCUE offers no reliable hook for it. Tune or disable the
feed via `app_settings.data_freshness_feeds`.
