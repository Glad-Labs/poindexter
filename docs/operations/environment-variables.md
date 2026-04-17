# Environment variables

> **Important:** Poindexter runs virtually ALL configuration out of the
> `app_settings` Postgres table, not environment variables. Env vars
> and `~/.poindexter/bootstrap.toml` exist only to **bootstrap** the
> stack (enough config to connect to the database). Once the worker
> connects, everything — model thresholds, prompt templates, QA
> weights, auto-publish rules, algorithm windows, SEO pinging
> endpoints — is changed with SQL against `app_settings` (200+ keys
> as of April 2026), no redeploy needed.
>
> **Preferred path:** run `poindexter setup` once on first install.
> It writes `~/.poindexter/bootstrap.toml` with the single value you
> need to reach the database. Env vars below are for Docker / CI
> deployments where bootstrap.toml isn't ideal.
>
> See [reference/app-settings.md](../reference/app-settings.md) for
> the full list of DB-backed settings.

This document enumerates the environment variables consumed by the
stock `docker-compose.local.yml` deployment. If you're running a
custom configuration, start from this list.

---

## Required

These variables must be set in `.env.local` (or exported in your
shell) before `docker compose up`. Missing any of them causes the
stack to fail fast with a clear error — no silent fallbacks.

| Variable                  | Purpose                                   | Example                     |
| ------------------------- | ----------------------------------------- | --------------------------- |
| `API_TOKEN`               | Bearer token for the worker's REST API    | `gladlabs-a9f3...` (random) |
| `LOCAL_POSTGRES_PASSWORD` | Password for the local Postgres superuser | random 32-char hex          |
| `GRAFANA_PASSWORD`        | Grafana admin password                    | random 32-char hex          |
| `PGADMIN_PASSWORD`        | pgAdmin admin password                    | random 32-char hex          |

All four are generated automatically by `poindexter setup` on
first run and written to `.env.local`. Do not commit that file.

## Optional (with sensible defaults)

| Variable               | Default                             | Purpose                                                                                                               |
| ---------------------- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `API_BASE_URL`         | `http://localhost:8002`             | Base URL clients use to reach the worker                                                                              |
| `COMPANY_NAME`         | `Glad Labs`                         | Publisher name used in generated content's `<author>` tag                                                             |
| `DEFAULT_OLLAMA_MODEL` | `auto`                              | Ollama model the router picks when no override                                                                        |
| `DEVELOPMENT_MODE`     | `true`                              | Enables dev bypasses (token, CORS). **Never set in prod.**                                                            |
| `ENVIRONMENT`          | `production`                        | Log tag + Sentry environment                                                                                          |
| `GRAFANA_USER`         | `admin`                             | Grafana admin username                                                                                                |
| `LOCAL_POSTGRES_USER`  | `poindexter`                        | Local Postgres user                                                                                                   |
| `LOCAL_POSTGRES_DB`    | `poindexter_brain`                  | Local Postgres database name                                                                                          |
| `OLLAMA_BASE_URL`      | `http://host.docker.internal:11434` | Where `OllamaClient` reaches Ollama (pipeline generation)                                                             |
| `OLLAMA_URL`           | `http://host.docker.internal:11434` | Where `MemoryClient` / `auto-embed.py` reaches Ollama (embeddings). Must be set to the same value as OLLAMA_BASE_URL. |
| `PORT`                 | `8002`                              | Worker HTTP port                                                                                                      |
| `SITE_DOMAIN`          | `gladlabs.io`                       | The operator's public site domain                                                                                     |
| `SITE_NAME`            | `Glad Labs`                         | Site name in feeds + metadata                                                                                         |
| `SITE_URL`             | `https://www.gladlabs.io`           | Canonical site URL                                                                                                    |

## Optional (no default — feature off unless set)

| Variable                 | Feature gated on it                                  |
| ------------------------ | ---------------------------------------------------- |
| `PEXELS_API_KEY`         | Fallback stock-photo search when SDXL is unavailable |
| `TELEGRAM_BOT_TOKEN`     | Brain daemon alerts via Telegram                     |
| `TELEGRAM_CHAT_ID`       | Destination chat for Telegram alerts                 |
| `OPENCLAW_GATEWAY_URL`   | Discord + Telegram bridge via OpenClaw               |
| `OPENCLAW_GATEWAY_TOKEN` | Bearer token for OpenClaw Tools Invoke API           |

> **No cloud LLM keys by default.** Poindexter runs entirely on
> local Ollama out of the box. If you want to add cloud fallback,
> the model router reads keys from `app_settings` at runtime — do
> NOT inject `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` /
> `GOOGLE_API_KEY` as env vars. Set them via the settings API:
>
> ```bash
> curl -X PUT http://localhost:8002/api/settings/openai_api_key \
>   -H "Authorization: Bearer $API_TOKEN" \
>   -d '{"value": "sk-proj-..."}'
> ```

## `.env.local` template

A fully-populated `.env.local` for a standard local install looks
like this (the bootstrap script writes something equivalent):

```env
# ---- Required ----
API_TOKEN=gladlabs-REDACTED-generated-by-bootstrap
LOCAL_POSTGRES_PASSWORD=REDACTED
GRAFANA_PASSWORD=REDACTED
PGADMIN_PASSWORD=REDACTED

# ---- Optional overrides ----
COMPANY_NAME=Your Brand Here
SITE_NAME=Your Brand Here
SITE_DOMAIN=yourbrand.com
SITE_URL=https://www.yourbrand.com

# ---- Feature flags ----
DEVELOPMENT_MODE=true
ENVIRONMENT=production
PORT=8002

# ---- Optional integrations (leave empty if you don't use them) ----
PEXELS_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## How the worker actually reads these

The worker resolves configuration in this order:

1. **Environment variable** — for bootstrap-only values (DATABASE_URL,
   API_TOKEN, passwords). Read once at startup.
2. **`app_settings` table** — for runtime-mutable values (model
   thresholds, prompt overrides, cost guards, feature flags).
   Consulted on every call via `site_config.get(key, default)`.
3. **Hardcoded fallback** — the `_DEFAULTS` dict in
   `services/site_config.py`. Used only when the DB value is absent
   AND no env var provides a value.

Never edit `_DEFAULTS` to change runtime behavior — that requires a
rebuild. Use the settings API or SQL against `app_settings` instead.

## Where to go next

- **Full settings catalog:** [reference/app-settings.md](../reference/app-settings.md)
- **Local dev setup:** [local-development-setup.md](local-development-setup.md)
- **Troubleshooting:** [troubleshooting.md](troubleshooting.md)
