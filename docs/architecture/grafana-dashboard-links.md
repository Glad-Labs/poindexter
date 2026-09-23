# Grafana dashboard links — why the host is rendered, not written

A dashboard link that points at a **sibling service** (Prefect `:4200`,
Langfuse `:3010`, GlitchTip `:8080`, pgAdmin `:18443`, Prometheus `:9091`,
worker API `:8002`) needs a hostname, and for a long time it had the wrong one.

## The three link classes

Not every link has the same problem, and conflating them is how the fix stayed
partial for months.

| Class                   | Correct form                                | Why                                                                                          |
| ----------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **Grafana's own pages** | relative — `/d/<uid>`, `/explore?...`       | Inherits whatever origin the reader already reached Grafana on. Needs no host and no render. |
| **Sibling service UIs** | `http://__POINDEXTER_SERVICE_HOST__:<port>` | Different port, same machine — so it needs a host the reader's _browser_ can resolve.        |
| **External sites**      | the literal URL                             | `github.com`, `grafana.com`, `vercel.com` are public and fine as-is.                         |

## Why a render step exists at all

Grafana **does not interpolate environment variables inside dashboard JSON**.
`${__env.POINDEXTER_SERVICE_HOST}` is not a thing there — verified on
`grafana-oss:13.0.1`, where the anchor came back with the literal
`http://${__env.POINDEXTER_SERVICE_HOST}:4200` as its `href`. Dashboard
_variables_ (`$var`) don't help either: provisioned dashboards are file-backed,
so a `constant` variable's value reverts on every re-provision.

So the host cannot be configuration unless something rewrites the file before
Grafana reads it. The two alternatives were both worse:

- **`http://localhost:<port>`** — what shipped. Resolves only for a browser on
  the Docker host. Dead from a phone, which is the normal way this system is
  operated.
- **`http://<operator-host>:<port>`** — works for exactly one operator, and
  puts a private hostname into a publicly-mirrored repo. The mirror leak guard
  refuses it, and the last time it went in anyway, the remedy was to strip the
  entire dashboard out of the OSS mirror (`mission-control.json` lived in
  `_STRIP_FILES` for that reason until 2026-09-23).

## How it works

1. Dashboards in `infrastructure/grafana/dashboards/*.json` carry the literal
   `__POINDEXTER_SERVICE_HOST__` placeholder. No `$`, so Grafana never tries to
   interpolate it, and it is obviously not a hostname if it ever leaks through.
2. Compose mounts that directory **read-only** at `/etc/grafana/dashboards-src`.
3. The `grafana` service's **entrypoint** renders src → `/var/lib/grafana/dashboards`,
   substituting `${POINDEXTER_SERVICE_HOST:-localhost}`, then `exec`s Grafana's
   own `/run.sh`.
4. The file provisioning provider reads the **rendered** directory.
5. `scripts/start-stack.sh` resolves `app_settings.operator_service_host` (via
   `scripts/_grafana_service_host.py`) and writes it into
   `.poindexter-grafana.env`, which the grafana service loads as an `env_file` —
   so a plain `docker compose up -d` outside `start-stack.sh` still picks it up.
   Same mechanism, and same rationale, as `GRAFANA_WEBHOOK_TOKEN`.

### Three details that are load-bearing

- **The render target is inside `grafana-data`**, not a second named volume. A
  fresh named volume is created **root-owned** and Grafana runs as uid 472, so
  the first render dies with `Permission denied` and the container exits 1.
- **It renders in the existing service's entrypoint, not an init container.**
  Adding a compose service changes the set that brain's `compose_drift_probe`
  audits, and an init container that exits reads as "down".
- **A 30-second re-sync loop runs in the background.** The bind mount used to
  give hot-reload (edit JSON → provider rescans in 30s → board updates). Rendering
  once at start would have quietly removed that. Files are compared and only
  replaced when they differ, so an unchanged board is never rewritten — a
  needless mtime bump would make Grafana re-provision every cycle. Rendered
  copies whose source disappears are pruned.

## Setting it

```bash
poindexter settings set operator_service_host my-workstation.example.ts.net
bash scripts/start-stack.sh up -d
```

Host **only** — no scheme, no port, no path. `_grafana_service_host.py` refuses
anything else and falls back, because a malformed value would be substituted
into every dashboard URL and produce silently broken links rather than an error.

Default is `localhost`, which reproduces the historical behaviour exactly: right
for a browser on the Docker host, and the value a fresh install wants.

## Guards

| Guard                                                              | Catches                                                                                                                                                                                                            |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `scripts/ci/grafana_dashboard_host_lint.py`                        | A link hardcoded back to `localhost`/`127.0.0.1`. Loopback **only** — operator-host leaks belong to `check_public_mirror_safety.py`, and a lint that also reddened on `github.com` would get ignored then deleted. |
| `tests/unit/scripts/test_grafana_dashboard_render_wiring.py`       | The mount, the entrypoint, the fallback, the provider path, and bare placeholders in prose.                                                                                                                        |
| `tests/unit/scripts/test_check_public_mirror_safety_strip_list.py` | `mission-control.json` re-acquiring an operator hostname or the voice URL.                                                                                                                                         |
| `scripts/system-health-check.sh`                                   | An **unrendered placeholder surviving in the container** — i.e. the render silently no-opping, which would otherwise look healthy while every cross-service link is a dead hostname.                               |

## Related

- The **Explore** links (Tempo, Loki) are a different bug with the same symptom:
  they are `/explore` deep-links, and Grafana gates Explore behind
  `datasources:explore`, which the Viewer role does not carry by default. Fixed
  with `GF_USERS_VIEWERS_CAN_EDIT=true`. The legacy `left={...}` URL format is
  _not_ deprecated to the point of breaking — it still loads the query on 13.0.1.
- `mission-control.json` now **ships to the OSS mirror** for the first time, a
  side effect of having no operator literal left in it.
