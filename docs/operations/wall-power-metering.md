# Wall-power metering (Shelly smart plug)

The Hardware & Power dashboard's `psu_total_power_watts` panel and the brain's
electricity-cost calc want **true wall power** — the AC watts actually pulled
from the outlet. The Corsair HX1500i reports this over USB, but only iCUE can
own that USB HID (it drives the pump and fans). Any second process that reaches
for the **HX1500i itself** contends with iCUE and crashes its PSU sensor — that's
why HWiNFO64 was retired (2026-07-10): it read the HXi over the same HID and
destabilised iCUE unless the Corsair integration was disabled. **AIDA64 is safe
to run alongside iCUE** precisely because it does _not_ read the HX1500i — it
reads the CPU/GPU/motherboard sensors over other interfaces and never touches the
contended HID. (The flip side: AIDA64 exposes no Corsair PSU sensor, so it cannot
populate `psu_total_power_watts` either.) The robust, contention-free source of
true wall power is a smart plug that meters the outlet directly.

## Source priority

`brain/psu_power.py::select_power_source` picks, best → worst:

1. **`psu_total_power_watts`** — real metered wall power from a Shelly outlet
   plug (`shelly_psu_url`). Primary. _(Before the HWiNFO64 retirement this metric
   could also come from HWiNFO reading the HXi; that path is gone — see above.)_
2. **iCUE CSV tap** — `sensor_samples.psu_power_in`, the always-on fallback
   (`tap.corsair_csv`). This is the live PSU source today until a Shelly plug is
   wired, and the same tap now also feeds the Wall/DC Output and Fan panels. Used
   when the primary metric is absent.
3. **`system_total_power_estimate_watts`** — CPU + GPU + overhead software
   estimate. Degraded; the brain pages the operator when it falls to this.

## Why a smart plug over the PSU's own USB reading

Only one process can own the HX1500i's USB HID, and it must be iCUE (cooling
control). A Shelly plug sits on the outlet as a separate device, so it cannot
fight iCUE, cannot be crashed by it, and measures the true metered draw
(including PSU conversion loss) — the number the utility actually bills. It is
also reboot-proof: no software toggle can silently stop it the way iCUE's CSV
logging does.

## Sensor sources after the HWiNFO64 retirement

With HWiNFO64 dropped (2026-07-10), the live hardware telemetry splits cleanly by
interface and nothing contends with iCUE for the HX1500i:

| Signal                       | Source               | Path                                                                    | Dashboard panel                    |
| ---------------------------- | -------------------- | ----------------------------------------------------------------------- | ---------------------------------- |
| CPU / GPU / board **temps**  | AIDA64 shared memory | Prometheus `aida64_temperature_celsius`                                 | Temperatures (AIDA64)              |
| Rail + core **voltages**     | AIDA64 shared memory | Prometheus `aida64_voltage_volts`                                       | Voltages (AIDA64)                  |
| CPU package + GPU **power**  | AIDA64 shared memory | Prometheus `aida64_power_watts`                                         | Component Power — live (AIDA64)    |
| **PSU** wall / DC power      | Corsair iCUE CSV     | `sensor_samples` (`source=corsair_csv`, `psu_power_in`/`psu_power_out`) | Wall Power / DC Output (HX1500i)   |
| Case + PSU **fan RPMs**      | Corsair iCUE CSV     | `sensor_samples` (`source=corsair_csv`, `fan_*`)                        | Fans (iCUE)                        |
| True **wall power** (billed) | Shelly smart plug    | Prometheus `psu_total_power_watts`                                      | Power Over Time / Electricity Cost |

AIDA64 owns everything it can read over SMBus/PCIe; the iCUE CSV owns everything
behind the Corsair USB HID (the PSU rails and the LINK fan controller — neither
of which AIDA64 can see). The exporter's HWiNFO reader (`get_hwinfo_metrics` in
`scripts/nvidia-smi-exporter.py`) stays as a no-op when the shared memory is
absent, so an operator who runs HWiNFO _without_ the Corsair integration can opt
back in — it just isn't part of the default Glad Labs sensor set anymore.

## Setup

1. Plug **only the PC** into the Shelly (leave monitors on another outlet) for
   clean box-level draw.
2. Set the plug up **local** (AP mode → join Wi-Fi) and give it a reserved LAN
   IP. No cloud account is required — Shelly Gen2+ exposes a local RPC API.
3. Add the plug's base URL to `~/.poindexter/bootstrap.toml`:

   ```toml
   shelly_psu_url = "http://192.168.1.50"
   ```

4. Restart the exporter task so it re-reads bootstrap:
   `scripts/background-services.ps1` service `poindexter-nvidia-smi-exporter`.

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
