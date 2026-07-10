# Wall-power metering (Shelly smart plug)

The Hardware & Power dashboard's `psu_total_power_watts` panel and the brain's
electricity-cost calc want **true wall power** — the AC watts actually pulled
from the outlet. The Corsair HX1500i reports this over USB, but only iCUE can
own that USB HID (it drives the pump and fans), and any second reader (HWiNFO,
AIDA64, liquidctl) contends with iCUE and crashes it. The robust,
contention-free source is a smart plug that meters the outlet directly.

## Source priority

`brain/psu_power.py::select_power_source` picks, best → worst:

1. **`psu_total_power_watts`** — real metered wall power (a Shelly outlet plug,
   or HWiNFO reading the HXi where that happens to coexist with iCUE). Primary.
2. **iCUE CSV tap** — `sensor_samples.psu_power_in`, the always-on fallback
   (`tap.corsair_csv`). Used when the primary metric is absent.
3. **`system_total_power_estimate_watts`** — CPU + GPU + overhead software
   estimate. Degraded; the brain pages the operator when it falls to this.

## Why a smart plug over the PSU's own USB reading

Only one process can own the HX1500i's USB HID, and it must be iCUE (cooling
control). A Shelly plug sits on the outlet as a separate device, so it cannot
fight iCUE, cannot be crashed by it, and measures the true metered draw
(including PSU conversion loss) — the number the utility actually bills. It is
also reboot-proof: no software toggle can silently stop it the way iCUE's CSV
logging does.

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
