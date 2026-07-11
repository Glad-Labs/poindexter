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

The exporter (`scripts/nvidia-smi-exporter.py`) then polls
`http://<plug>/rpc/Switch.GetStatus?id=0` on every scrape, reads `apower`, and
emits `psu_total_power_watts` (plus `psu_line_voltage_volts` and
`psu_line_current_amps`). Prometheus scrapes `:9835`, the Grafana panel
populates, and the brain promotes the source from `estimate` to primary.

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
a `data_feed_stale` finding (→ Discord) when a producer goes dark, so a stale
wall-power number can never silently masquerade as a live one.
