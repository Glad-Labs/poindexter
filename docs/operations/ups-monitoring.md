# UPS monitoring (NUT → Prometheus → Grafana/alerts)

A line-interactive UPS keeps the host alive through sags and outages — but
until it is monitored, everything it knows (line voltage, transfers, load
margin, battery state) is invisible, and the first sign of trouble is the
machine going dark anyway. The operator rig learned this the hard way in July
2026: four utility power cuts in five days on a house line that never exceeded
114.6V against a 120V nominal, capped by a 2026-07-31 overload trip — the UPS
transferred to battery during a sag while its single load, the PC, was
bursting past 90% of the 1000W inverter rating (840→910W sustained, ~1008W
burst peak over 9 metered days), and the inverter dropped its output.
Glad-Labs/poindexter#958 wired the whole story into the stack.

## Architecture

```
UPS ──USB──▶ NUT on the HOST                nut-exporter container      Prometheus
             (usbhid-ups driver,     ◀──── (network_mode: host,   ◀── job "nut" via
              upsd on 127.0.0.1:3493)       ghcr.io/druggeri/          host.docker.internal:9199
                                            nut_exporter, :9199)            │
                                                                            ▼
                                                          Hardware & Power UPS row (Grafana)
                                                          + Ups* DB-rendered alert rules
                                                            (prometheus_rule_builder.py)
```

- **NUT stays a host service, not a container.** The driver needs the USB
  device and udev rules, and — more importantly — NUT's `upsmon` is what
  performs the clean OS shutdown when the battery runs out. That duty cannot
  live inside the container stack it would be shutting down.
- **The exporter runs with `network_mode: host`** because `upsd` binds
  `127.0.0.1:3493` (NUT's shipped default) and re-binding it to the docker
  bridge would be a host security-posture change the stack must not require.
  Consequence: the exporter's listener occupies **host port 9199** with no
  compose `ports:` mapping (see the note in [ports.md](ports.md)), and
  Prometheus scrapes it the way it scrapes node_exporter — via
  `host.docker.internal`.
- **No NUT credentials anywhere.** `upsd` grants anonymous read of UPS
  variables (`upsc` itself connects without auth); `upsd.users` accounts are
  only for instant commands and `upsmon` registration, which the exporter
  never performs. If you harden `upsd` to require auth for reads, add a
  dedicated read-only user in `/etc/nut/upsd.users` for the exporter (do not
  reuse the `upsmon` primary account) and pass it via `NUT_EXPORTER_USERNAME`
  / `NUT_EXPORTER_PASSWORD` on the compose service.

## Host prerequisite (once)

The operator rig's install (Pop!_OS, NUT 2.8.1, 2026-07-31) as the worked
example — any NUT-supported UPS follows the same shape:

```text
MODE=standalone                      # /etc/nut/nut.conf
[cyberpower]                         # /etc/nut/ups.conf
    driver = usbhid-ups
    port = auto
    vendorid = 0764
    override.battery.charge.low = 30      # force clean shutdown at 30%…
    override.battery.runtime.low = 300    # …or 300s runtime, whichever first
```

Sanity check from the host: `upsc cyberpower@localhost` must answer with
`battery.charge`, `input.voltage`, `ups.load`, `ups.status`, etc. The name in
brackets (`cyberpower`) is the name Prometheus targets — see below.

## Enabling the stack side

1. **Compose profile.** The `nut-exporter` service is profile-gated (same
   posture as `gpu-exporter`): add `ups` to `compose_profiles` in
   `~/.poindexter/bootstrap.toml` and start the stack, or one-off:

   ```bash
   docker compose -f docker-compose.local.yml --profile ups up -d nut-exporter
   ```

2. **Prometheus target name.** The `nut` scrape job's `targets` list in
   `infrastructure/prometheus/config/prometheus.yml` holds **UPS names**, not
   addresses (`cyberpower` as shipped — the relabel block turns each name into
   the exporter's `?ups=` query param and a `ups` label). If your UPS is named
   differently in `ups.conf`, change the target to match.

3. **Arm the gated alert rules.** Two of the six `Ups*` rules ship inert so a
   UPS-less host never pages (the `expected_gpu_count` /
   `psu_nominal_line_voltage_volts` precedent):

   ```bash
   poindexter settings set prometheus.threshold.expected_ups_count 1
   poindexter settings set prometheus.threshold.ups_input_voltage_low_volts 114
   ```

   114V is the right undervoltage line for a 120V service (ANSI C84.1 Range A
   lower bound); a 230V operator would set ~216. RenderPrometheusRulesJob
   re-renders within 5 minutes; no restarts needed.

## What you get

**Dashboard** — a "UPS — line power & battery (NUT)" row on Hardware & Power
(`/d/hardware-power`): status / charge / runtime / load% / estimated-watts
stats, input-vs-output voltage (90–130V range, dashed 114V advisory line, AVR
divergence visible), load% history against the 85/95% alert bands, battery
charge/runtime with the driver's own shutdown floors overlaid live, battery
voltage vs nominal (pack health), and a status-flag timeline (OB / LB / OVER /
BOOST / TRIM / CHRG / DISCHRG / FSD / BYPASS) that reconstructs an incident
after the fact: sag → BOOST → OB → OVER reads left to right.

**Alerts** — all DB-rendered (`prometheus_rule_builder.py` `DEFAULT_RULES`,
thresholds under `prometheus.threshold.*`), routed by severity exactly like
everything else (critical → Telegram, warning → Discord):

| Rule               | Fires when                                                 | Severity | Ships       |
| ------------------ | ---------------------------------------------------------- | -------- | ----------- |
| UpsOnBattery       | OB flag held 1m (sub-minute sag transfers don't page)      | critical | live        |
| UpsLowBattery      | charge < 50% **while discharging** (recharge never pages)  | critical | live        |
| UpsCommsLost       | fewer UPSes visible than `expected_ups_count`, 10m         | critical | inert ("0") |
| UpsInputVoltageLow | input under the volts floor for 30m (0V = outage, ignored) | warning  | inert ("0") |
| UpsLoadHigh        | load in the 85–95% band, sustained 5m                      | warning  | live        |
| UpsLoadCritical    | load ≥ 95% of rating for 1m — next burst trips it          | critical | live        |

The short-`for:` rules bridge scrape holes with `last_over_time[10m]` — their
firing window IS the outage, exactly when containers get restarted, and a raw
read losing one scrape would send a false "resolved" mid-incident. The load
bands are disjoint (warning ceilinged at 95%) so one overload episode pages
once, escalating rather than doubling.

## Verifying

```bash
# Exporter answering, from the host:
curl -s "http://127.0.0.1:9199/ups_metrics?ups=cyberpower" | grep network_ups_tools_ups_status
# Prometheus sees the target + series:
curl -s "http://localhost:9091/api/v1/query?query=network_ups_tools_input_voltage" | python3 -m json.tool
# Rendered rules include the Ups* family:
curl -s "http://localhost:9091/api/v1/rules" | python3 -c "import json,sys; print([r['name'] for g in json.load(sys.stdin)['data']['groups'] for r in g['rules'] if r['name'].startswith('Ups')])"
```

## Relationship to the Shelly wall meter

The Shelly plug ([wall-power-metering.md](wall-power-metering.md)) and the UPS
input tap are **two independent meters on the same supply story** — the
`MainsVoltage*` rules read the Shelly, `UpsInputVoltageLow` reads the UPS.
Keep both: they cross-check each other, and the plug can move (or die) without
blinding the undervoltage watch. The UPS adds what no plug can see — transfer
events, inverter load margin, and battery state.

## Outlet guard — the brain turns our own outlet back on

Earned 2026-09-06. The Shelly plug feeding the UPS opened its relay at
03:04 EDT with 121 V still on its input. The UPS carried the PC on battery
for 18 minutes, `UpsOnBattery` + `UpsLowBattery` paged Telegram, NUT then
shut the host down cleanly — and it sat dark for 12 hours because the plug's
`initial_state` was `off`. Every probe was green throughout: at 03:21 the
brain reported 0 issues with the battery at 21%, because nothing looked at
whether _our own outlet was switched on_. Two fixes:

1. **The plug's `initial_state` is now `on`** (`Switch.SetConfig`, config rev
   17), so a plug reboot (crash, firmware update, momentary outlet contact
   loss) brings the outlet straight back instead of parking it off.
2. **`brain/outlet_guard_probe.py`** runs every brain cycle and issues
   `Switch.Set on` over the plug's local RPC when all three hold:
   - the relay is off (`Switch.GetStatus.output == false`);
   - mains is present on the plug's INPUT (`voltage >=
outlet_guard_min_line_voltage_volts`, default 90 V) — a relay open at
     0 V is a real outage, nothing to restore;
   - the UPS is on battery (NUT exporter `ups_status{flag="OB"} 1`, read from
     `outlet_guard_ups_metrics_url`) — that is what proves _we_ are the load
     behind that outlet. On line + outlet off = the plug meters something
     else, so it pages (warning) and leaves it alone. Exporter unreachable or
     no `ups_status` series = **unknown, not "on line"**: pages, does not act.

   Restores are capped (`outlet_guard_restore_cap_per_window`, default 3 per
   60 min); past the cap the guard stops switching and pages critical — a
   relay that keeps dropping is hardware. Every restore pages **critical**
   (Telegram + Discord) carrying the plug's pre-restore `source` (`button` /
   `cloud` / `matter` / `init` …), `errors` and temperature, because
   `Switch.Set` overwrites `source` and that field is the only record of
   _why_ the outlet opened. The 2026-09-06 event lost it to the operator's
   button press.

   The plug address is `SHELLY_PSU_URL` — the same env compose hands the
   `gpu-exporter`, exported by `start-stack.sh` from the bootstrap
   `shelly_psu_url` key. Unset = the guard no-ops. Kill-switch
   `outlet_guard_enabled` is fail-closed. The exporter also emits
   `psu_outlet_output_on` now, so "relay open, line voltage present" is a
   first-class series on the Hardware & Power board rather than an
   `apower` that merely reads 0.

   Latency is one brain cycle (5 min) worst case against ~18 min of battery
   at the PC's idle load. A plug-only install with no UPS can set
   `outlet_guard_require_ups_on_battery=false` and the guard restores on
   relay-off + mains-present alone.
