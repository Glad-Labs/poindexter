# Port Assignments — Single Source of Truth

This file is the canonical reference for which port to use when something on
the host (your browser, the brain probe, a CLI tool, an OpenClaw plugin)
needs to reach a service in the local Docker stack.

The actual mapping is owned by `docker-compose.local.yml`. **If you change a
port there, update this table.** A CI lint job (`scripts/ci/ports_lint.py`,
wired in `.github/workflows/ports-lint.yml`) is the belt-and-suspenders that
keeps them in sync: it fails the build if the host-port column here and the
`ports:` publishes in the compose file disagree in either direction (and it
also asserts `setup.py::_DEFAULT_LOCAL_DB_PORT` matches the Postgres publish).
Run it locally with `python scripts/ci/ports_lint.py`.

## Why this matters

Several services don't run 1:1 host:container. Calling the wrong side
produces silent failures:

- The brain port-forward probe historically false-fired on Prometheus
  every cycle because it probed `host.docker.internal:9090` (container
  port) instead of `host.docker.internal:9091` (host port). Fixed in
  poindexter#222 follow-up via the `host_port` watch-list field.
- Operators hitting `localhost:9090` for Prometheus or `localhost:3001`
  for Uptime Kuma get connection-refused — their browser can only reach
  the host port, not the container's internal port.

If something says "down" but `docker ps` shows it healthy, **check this
table first.**

## Table

| Service             | Container                   | Host port | Container port | URL                                                                                        |
| ------------------- | --------------------------- | --------- | -------------- | ------------------------------------------------------------------------------------------ |
| Grafana             | poindexter-grafana          | **3000**  | 3000           | <http://localhost:3000>                                                                    |
| Uptime Kuma         | poindexter-uptime-kuma      | **3002**  | 3001           | <http://localhost:3002>                                                                    |
| Langfuse web        | poindexter-langfuse-web     | **3010**  | 3000           | <http://localhost:3010>                                                                    |
| Loki                | poindexter-loki             | **3100**  | 3100           | <http://localhost:3100> (Grafana datasource)                                               |
| Tempo HTTP          | poindexter-tempo            | **3200**  | 3200           | <http://localhost:3200> (Grafana datasource)                                               |
| Tempo OTLP gRPC     | poindexter-tempo            | **4317**  | 4317           | gRPC — worker exports traces here                                                          |
| Tempo OTLP HTTP     | poindexter-tempo            | **4318**  | 4318           | HTTP alt OTLP path                                                                         |
| Pyroscope           | poindexter-pyroscope        | **4040**  | 4040           | <http://localhost:4040>                                                                    |
| Prefect server      | poindexter-prefect-server   | **4200**  | 4200           | <http://localhost:4200>                                                                    |
| LiveKit signalling  | poindexter-livekit          | **7880**  | 7880           | WebSocket signalling                                                                       |
| LiveKit RTC TCP     | poindexter-livekit          | **7881**  | 7881           | TCP RTC fallback                                                                           |
| LiveKit RTC TCP alt | poindexter-livekit          | **7882**  | 7882           |                                                                                            |
| Speaches (STT/TTS)  | poindexter-speaches         | **8001**  | 8000           | Warm STT + TTS sidecar (OpenAI-compatible)                                                 |
| Chatterbox (TTS-HQ) | poindexter-chatterbox       | **8011**  | 8000           | Live podcast narration — ResembleAI Chatterbox, voice-cloning (opt-in: `--profile tts-hq`) |
| Worker (FastAPI)    | poindexter-worker           | **8002**  | 8002           | <http://localhost:8002>                                                                    |
| GlitchTip web       | poindexter-glitchtip-web    | **8080**  | 8000           | <http://localhost:8080>                                                                    |
| Prometheus          | poindexter-prometheus       | **9091**  | 9090           | <http://localhost:9091>                                                                    |
| Alertmanager        | poindexter-alertmanager     | **9093**  | 9093           | <http://localhost:9093>                                                                    |
| GPU exporter        | poindexter-gpu-exporter     | **9835**  | 9835           | Prometheus scrape target                                                                   |
| image-gen server    | poindexter-image-gen-server | **9836**  | 9836           | On-demand image generation                                                                 |
| Postiz              | poindexter-postiz           | **5003**  | 5000           | Social distribution hub (opt-in: `--profile postiz`)                                       |
| Wan server          | poindexter-wan-server       | **9840**  | 9840           | On-demand video generation                                                                 |
| ComfyUI             | poindexter-comfyui          | **8188**  | 8188           | Wan 2.2 14B hero renderer, localhost-bound (opt-in: `--profile comfyui`)                   |
| Stable Audio server | poindexter-stable-audio     | **9839**  | 9839           | Ambient music bed + intro/outro stings (Stable Audio Open 1.0; 5-min idle self-unload)     |
| Postgres            | poindexter-postgres-local   | **5433**  | 5432           | `postgresql://...@localhost:5433/poindexter_brain`                                         |
| pgAdmin             | poindexter-pgadmin          | **18443** | 80             | <http://localhost:18443>                                                                   |

**Bolded host-port column is what the host (your browser, host-CLI, or
`host.docker.internal`) reaches the service on.** From inside another
Docker container, use the container's hostname + container port (e.g.
`prometheus:9090`, `postgres-local:5432`).

## Inside Docker vs from the host

```
              ┌────────────────────────┐
              │ Your browser / host    │
              │ ▶ localhost:9091       │  <-- host port
              └──────────┬─────────────┘
                         │
                  Docker NAT
                         │
              ┌──────────▼─────────────┐
              │ poindexter-prometheus  │
              │ ▶ :9090                │  <-- container port
              └────────────────────────┘
                         ▲
                         │
              ┌──────────┴─────────────┐
              │ Sibling container      │
              │ (worker, brain, etc.)  │
              │ ▶ prometheus:9090      │  <-- container port
              │   via Docker DNS       │
              └────────────────────────┘
```

Brain probes that need to verify "this service is reachable from the host
side" specifically should use `host.docker.internal:<host_port>`. (On
docker-ce this needs `extra_hosts: host-gateway`; Docker Desktop provides it
natively.)

The original motivation was Docker Desktop's port-forward getting stuck — a
**Windows/WSL2** failure mode that `brain/docker_port_forward_probe.py`
exists to self-heal. On bare-metal Linux there is no port-forwarding proxy
in the path, so that wedge class cannot occur and the probe is dead weight:
turn it off with `docker_port_forward_probe_enabled=false`. It ships enabled
because Docker Desktop installs are the ones that need it.

## When to add a row here

- New service added to `docker-compose.local.yml` with a `ports:` mapping
- Existing service's host port changes
- Existing service's container port changes (rare — usually upstream image
  decision)

## When this table can mislead you

- Compose `network_mode: container:postgres-local` services share another
  container's network namespace; they reach each other on `localhost:CONTAINER_PORT`,
  not via the host port. The legacy `poindexter-backup-*` sidecars do this.
- `nut-exporter` (profile `ups`) runs with `network_mode: host`, so it has no
  `ports:` mapping for the lint to see — its listener directly occupies
  **host port 9199** (like node_exporter's host-side 9100). Prometheus
  scrapes it at `host.docker.internal:9199`; the NUT server it reads is the
  host's `upsd` on `127.0.0.1:3493`. See
  [ups-monitoring.md](ups-monitoring.md).
- Tailscale-routed access doesn't use these ports — it's fronted by
  Tailscale Serve (tailnet-only), configured via the `app_settings.tailscale_*`
  keys. (The `/voice/join` surface moved off the public Funnel to Serve on
  2026-06-02.)
