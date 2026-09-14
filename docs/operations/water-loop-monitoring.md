# Water-loop monitoring — pump, coolant, fans

The custom loop was the last subsystem on the operator rig with **no instrument
in Prometheus**. A failing D5 surfaced only as CPU and GPU temperatures
climbing, which is a badly lagging indicator precisely _because_ the loop's
thermal mass is so large — roughly 1500 mm of radiator and a reservoir buy many
minutes of looking fine while there is no flow at all.

This page covers what is measured, what is deliberately not, and how to operate
it.

## What produces the metrics

```
iCUE LINK hubs (USB HID)
        │
        ▼
OpenLinkHub daemon ──── binds 127.0.0.1:27003 (control API — fan curves, RGB, pump duty)
        │
        ▼  openlinkhub-docker-bridge.service (socat, 172.17.0.1 only)
        │
        ▼
gpu-exporter container → scripts/nvidia-smi-exporter.py → openlinkhub_* on :9835
        │
        ▼
Prometheus (job="nvidia-smi") → Grafana "Hardware & Power" → alerts
```

`liquidctl` cannot do this job — it has no iCUE LINK driver — and the older
direct-HID readers fought iCUE for the USB. OpenLinkHub owns the HID
exclusively and re-serves it over HTTP, so reading it contends with nothing.

## The metrics

| Metric                        | Meaning                                                                                                   |
| ----------------------------- | --------------------------------------------------------------------------------------------------------- |
| `openlinkhub_pump_rpm`        | Pump speed. The number that matters.                                                                      |
| `openlinkhub_coolant_celsius` | Loop liquid. `source="pump_res"` = reservoir (cool side), `source="cpu_block"` = block outlet (hot side). |
| `openlinkhub_fan_rpm`         | Every fan on the iCUE LINK bus.                                                                           |
| `openlinkhub_probe_celsius`   | Fan-hub **air** probes — case air, not coolant.                                                           |
| `openlinkhub_device_critical` | The hub's own self-reported critical flag.                                                                |
| `openlinkhub_up`              | 1 = API answered _and_ exposed at least one telemetry device.                                             |

### Two distinctions that are load-bearing

**Coolant is not air.** QX fans carry their own temperature probes, and those
read air at the fan hub. Only the pump/reservoir probe and the CPU block report
liquid. Labelling fan air as coolant would leave the telemetry looking perfectly
healthy while every loop threshold measured the wrong fluid, so the exporter
splits them into two metric names and a test pins the split.

**`openlinkhub_up` is emitted on every path, including failure.** Without it, a
dead exporter and a dead pump are the same observation — no series — and the
pump alert is unwritable. With it the alert can say
`pump_rpm == 0 AND up == 1`, which means _the hub is talking to us and
reporting zero_. This is also why "no data" on the pump panel must never be read
as "pump fine": see `LoopTelemetryDown` below.

## What is NOT measured

**Flow rate.** The inline impeller meter is a passive G1/4 part and is not on
the iCUE LINK bus — it appears nowhere in the device tree, so there is no
reading to publish. Emitting a fabricated flow series would be worse than the
gap.

The partial substitute is the **delta-T panel** (block minus reservoir), which
is a genuine flow proxy: for a given heat load, slower flow means each unit of
coolant absorbs more heat on its way through the block, so delta rises before
any absolute temperature threshold trips. It is **charted but not alerted on**,
because delta also rises legitimately with CPU load — a threshold needs a
calibration window across idle, gaming and render loads first. Measured
baseline on the operator rig (2026-09-14): **~4.5 °C at light load**, with the
reservoir at 33.0 °C and the block at 37.5 °C.

## Alerts

All five are DB-sourced defaults in `services/prometheus_rule_builder.py`, so
thresholds are tunable per install via `app_settings`.

| Alert                        | Fires when                                               | Severity |
| ---------------------------- | -------------------------------------------------------- | -------- |
| `CoolantPumpStopped`         | `pump_rpm == 0` for 1 m **and** telemetry up             | critical |
| `CoolantPumpSlow`            | `0 < pump_rpm < coolant_pump_rpm_warning` for 10 m       | warning  |
| `CoolantTemperatureHigh`     | coolant > `coolant_temperature_warning_celsius` for 10 m | warning  |
| `CoolantTemperatureCritical` | coolant > `coolant_temperature_critical_celsius` for 2 m | critical |
| `LoopTelemetryDown`          | `up == 0` for 15 m, having been up in the last 6 h       | warning  |

Tunables (`prometheus.threshold.*`): `coolant_pump_rpm_warning` (1200),
`coolant_temperature_warning_celsius` (45), `coolant_temperature_critical_celsius` (55).

Three design notes worth keeping:

- **Every read is wrapped in `last_over_time(...[10m])`.** The exporter lives in
  the `gpu-exporter` container, so a worker deploy or image rebuild restarts it.
  A raw read across that gap would page "PUMP STOPPED" on every deploy. `last`,
  never `max`, so a real stop still propagates on the first fresh sample instead
  of being masked for ten minutes by the last healthy reading.
- **`CoolantPumpStopped` is 1 minute, not the usual advisory patience.** With no
  flow the block goes from fine to throttling in a couple of minutes, and the
  coolant-temperature alerts are _lagging confirmations_ of this one. It should
  arrive first.
- **`LoopTelemetryDown` self-inerts.** A build with no iCUE LINK hardware still
  emits `openlinkhub_up 0`, which would page forever — so the rule also requires
  the series to have been 1 within the last 6 h. It means "the loop telemetry I
  had has gone away", never "this machine has no water cooling".

## The bridge relay, and why it exists

OpenLinkHub binds **loopback only**, and its API is a _control_ surface: fan
curves, RGB, and pump duty, with no authentication. The exporter runs in a
container, whose `host.docker.internal` route therefore gets `ECONNREFUSED`.

Setting `listenAddress = "0.0.0.0"` would fix the container and put
unauthenticated pump control on the LAN — a materially worse posture than the
problem being solved, especially as this host runs no firewall (`ufw` inactive).

`infrastructure/systemd/openlinkhub-docker-bridge.service` relays **the Docker
bridge gateway address only** (`172.17.0.1`, the `docker0` interface) to the
hub's loopback listener. `docker0` is a host-local virtual network and is not
routed to the LAN, so containers gain access and the network does not. Verified
after install:

```bash
ss -ltn | grep 27003          # expect 127.0.0.1 and 172.17.0.1 — never 0.0.0.0
```

**Stated tradeoff:** every container on the default bridge can now reach the
control API, not just the exporter. On this stack they are all Poindexter's own.
If that stops being true, replace the relay with a reverse proxy that allows
`GET /api/devices` and nothing else.

### Install

```bash
sudo cp infrastructure/systemd/openlinkhub-docker-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now openlinkhub-docker-bridge
```

The bind address must match your own `docker0` gateway — check with
`ip -o addr show docker0`. Skip the unit entirely if you have no iCUE LINK
hardware; the exporter then reports `openlinkhub_up 0` and the loop alerts
self-inert.

## Configuration

Base URL resolves: `OPENLINKHUB_URL` env → `openlinkhub_url` in
`~/.poindexter/bootstrap.toml` → `http://localhost:27003`.

The default serves host runs. The container **cannot** use `localhost` (that is
the container), so `docker-compose.local.yml` passes
`http://host.docker.internal:27003` via env.

## Troubleshooting

**All `openlinkhub_*` series missing.** The source is not being collected at
all. Confirm the exporter has the code (`get_openlinkhub_metrics` wired into
`_collect_all_metrics`) and remember the container mounts the script as a
**single file** — Docker binds the inode, so a `git pull` that replaces the file
leaves the container serving the old copy. A restart is not enough:

```bash
bash scripts/start-stack.sh up -d --force-recreate gpu-exporter
```

**`openlinkhub_up 0`.** The hub is unreachable or exposed no telemetry device.
In order: is `OpenLinkHub.service` running; is
`openlinkhub-docker-bridge.service` running; can the container reach it —

```bash
docker exec poindexter-gpu-exporter python -c "import urllib.request;print(urllib.request.urlopen('http://host.docker.internal:27003/api/devices',timeout=5).status)"
```

A lost USB HID also reads as `up 0`, because OpenLinkHub keeps answering while
reporting no devices — that is deliberate, since an empty device tree is a fault
and not an idle state.

**Pump panel reads "No data".** That is _not_ a healthy pump. Check
`openlinkhub_up` first — while telemetry is down the pump alerts cannot fire,
by design.

## Hardware notes discovered while building this

- The pump reports as **XD6 ELITE**, not the XD5 recorded in older notes. The
  exporter keys pump detection off the semantic `description` field
  ("Pump/Res"), not the product name, so an XD5/XD6/AIO swap cannot silently
  reclassify the loop's only pump as a fan.
- The bus enumerates **14 fans across two hubs** (11 QX, 2 LX, 1 RX). Build
  notes list 16 (10 QX140 / 3 RX120 / 3 LX120). The exporter reports what the
  bus reports; the discrepancy is a documentation question, and now a visible
  one — a fan that drops off the chain shows up as a vanished series.
