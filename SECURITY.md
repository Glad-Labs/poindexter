# Security Policy

**Last Updated:** 2026-05-23

Poindexter is built by [Glad Labs LLC](https://www.gladlabs.io) and licensed under Apache 2.0. This document covers how to report vulnerabilities, how to handle secrets, and the security architecture of a default Poindexter install.

---

## Reporting Vulnerabilities

**Preferred — GitHub Security Advisories:** <https://github.com/Glad-Labs/poindexter/security/advisories/new>

GitHub's private vulnerability reporting keeps the conversation private, triages more reliably than email, and lets the maintainer + reporter collaborate on a fix before public disclosure. This is the fastest route to a response.

**Fallback — email:** `security@gladlabs.io` with subject `[SECURITY] Vulnerability Report`. Use this only if GitHub Advisories aren't workable for you; the maintainer's email inbox is noisy so the response time on that channel is slower.

Do **not** create public GitHub issues for security vulnerabilities.

Expected response timeline:

- **Initial response:** within 48–72 hours of report (faster via Advisories than email).
- **Fix deadline before public disclosure:** 30 days.

If a vulnerability requires longer than 30 days to fix responsibly we will tell you and agree on a longer disclosure window.

---

## Authentication

All protected Poindexter API endpoints require an **OAuth 2.1 Client
Credentials** grant. Every consumer (CLI, MCP, brain daemon, ad-hoc
scripts, OpenClaw, the operator-only MCP server) mints short-lived
scoped JWTs through the worker's `POST /token` endpoint against a
registered row in the `oauth_clients` table:

```
Authorization: Bearer <JWT>
```

The legacy static-Bearer fallback — the `API_TOKEN` environment
variable, the in-DB `app_settings.api_token` /
`app_settings.api_auth_token` rows, and the `POINDEXTER_KEY` /
`GLADLABS_KEY` shortcuts — was removed when Phase 3 of the OAuth
cutover landed (Glad-Labs/poindexter#249, 2026-05-05). There is no
static API token plumbing left in the codebase; do not look for one.

### Provisioning a consumer

`poindexter setup` provisions the initial **CLI** client on fresh
installs (writes the `client_id` + `client_secret` into
`~/.poindexter/bootstrap.toml`). Other consumers register their own
clients via the per-consumer migrate commands:

```bash
poindexter auth migrate-cli            # CLI (re-provision / rotate)
poindexter auth migrate-mcp            # public MCP server
poindexter auth migrate-mcp-gladlabs   # operator-only MCP server
poindexter auth migrate-brain          # brain daemon
poindexter auth migrate-scripts        # ad-hoc scripts client
poindexter auth migrate-openclaw       # OpenClaw bridge
```

Each command inserts (or rotates) the consumer's row in `oauth_clients`
and pushes the credentials into that consumer's config file
(`~/.poindexter/bootstrap.toml`, `~/.claude.json`,
`~/.openclaw/openclaw.json`, etc.) with safe permissions.

### Minting a JWT

For day-to-day curl tests, mint a JWT via the CLI:

```bash
JWT=$(poindexter auth mint-token --client cli)
curl -H "Authorization: Bearer $JWT" http://localhost:8002/api/health
```

Or hit the token endpoint directly (handy from scripts that already
have their `client_id` + `client_secret`):

```bash
curl -X POST http://localhost:8002/token \
  -d grant_type=client_credentials \
  -d client_id=<pdx_xxx> \
  -d client_secret=<secret>
# → {"access_token": "<JWT>", "token_type": "Bearer", "expires_in": 3600, ...}
```

Tokens are short-lived (default 1 hour) and per-consumer-scoped — a
brain-scoped JWT cannot mint posts; a CLI-scoped JWT cannot reach
operator-only routes. Consumer wiring lives in
`src/cofounder_agent/services/oauth_client_service.py` and
`src/cofounder_agent/cli/auth_commands.py`.

### Production-mode enforcement

Production startup validates that `development_mode` is **not** `true`
in `app_settings` and refuses to boot if it is. Do not flip
`development_mode=true` in any environment that's reachable from
outside your machine.

---

## Secrets Management

### Never commit to git

The following are gitignored and must never appear in commits, public dashboards, or screenshots:

- `oauth_clients.client_secret` rows — per-consumer OAuth credentials (encrypted at rest, but treat the plaintext you copy into config files as a high-value secret)
- `POINDEXTER_SECRET_KEY` — the encryption key that wraps every other `is_secret=true` row
- `LOCAL_POSTGRES_PASSWORD` — local Postgres password
- `GRAFANA_PASSWORD`, `PGADMIN_PASSWORD` — admin UI passwords
- `jwt_secret_key` — JWT signing key for the OAuth token endpoint
- `revalidate_secret` — Vercel ISR revalidation header
- `PEXELS_API_KEY` — optional stock-image search key
- Community-plugin API keys (OpenAI-compat, Anthropic, etc.) if you install
  paid-provider plugins. The core stack is Ollama-only — paid keys never
  need to be set for a stock install.
- Any cloud `DATABASE_URL` if you connect Poindexter to an external Postgres
- `SENTRY_DSN` — error tracking endpoint (not strictly secret, but treat it as private)

### Where secrets live in a default install

- **Bootstrap secrets (infrastructure):** `~/.poindexter/bootstrap.toml` — database URL, the encryption key (`POINDEXTER_SECRET_KEY`), Docker stack passwords, and the initial CLI OAuth `client_id` / `client_secret`. Written once by `poindexter setup` with safe file permissions. Never committed to git.
- **Runtime secrets (API keys, webhooks, OAuth client_secrets for non-CLI consumers):** `app_settings` database table with `is_secret=true`, encrypted at rest via `pgcrypto` under `POINDEXTER_SECRET_KEY`. Read via `site_config.get_secret()` which bypasses the in-memory cache and queries the DB directly on each call.
- **No `.env` file is used.** The bootstrap.toml + app_settings pattern replaces all env-var-based secret management.

The canonical inventory + per-key rotation procedures live in
[`docs/operations/secret-rotation.md`](docs/operations/secret-rotation.md).

### Rotation

- **OAuth client secrets:** rotate every 90 days, or immediately on suspected compromise. Re-run the matching `poindexter auth migrate-<consumer>` command — it rotates the row atomically and rewrites the consumer's config file. Restart the consumer so it re-reads the file.
- **`POINDEXTER_SECRET_KEY`:** rotate annually via the `plugins.secrets.rotate_key` helper (see `docs/operations/secret-rotation.md`). This is the doomsday key — losing it without rotation orphans every encrypted row.
- **`LOCAL_POSTGRES_PASSWORD`:** rotate by running `ALTER USER poindexter PASSWORD '<new>'` then updating `~/.poindexter/bootstrap.toml` and restarting the stack.
- **LLM provider keys:** rotate at the provider, then `poindexter set <key> "<value>"` (or the `set_secret` Python helper for true secrets). No restart needed — `get_secret()` reads from DB on every call.

### If a secret is exposed

1. Immediately rotate the compromised credential at the source (provider dashboard, or `ALTER USER` for Postgres, or `poindexter auth migrate-<consumer>` for an OAuth pair).
2. For bootstrap secrets: update `~/.poindexter/bootstrap.toml`, then `bash scripts/start-stack.sh down && bash scripts/start-stack.sh`.
3. For runtime secrets in `app_settings`: `JWT=$(poindexter auth mint-token --client cli); curl -X PUT localhost:8002/api/settings/<key> -H "Authorization: Bearer $JWT" -d '{"value": "<new>"}'`. No restart needed.
4. Verify with: `JWT=$(poindexter auth mint-token --client cli); curl -H "Authorization: Bearer $JWT" http://localhost:8002/api/health`
5. Audit recent activity in the database (`audit_log` table) for any actions taken with the leaked credential before rotation. If an OAuth client_secret leaked, also check `oauth_token_issuances` for unexpected mints.

---

## Security Architecture

The default Poindexter install ships with these protections:

- **Input validation:** every request body is validated by FastAPI Pydantic schemas before any business logic runs.
- **SQL injection prevention:** every query uses parameterized placeholders (`$1, $2, …`). f-string SQL is forbidden by code review and a project-wide grep at lint time.
- **Rate limiting:** 100 requests/minute per IP by default, configurable via the `app_settings.rate_limit_per_minute` row (DB-first config — no env var).
- **CORS:** explicit origin whitelist via `allowed_origins` in `app_settings`. No wildcards.
- **Error responses:** internal stack traces and error details are stripped from HTTP responses and logged server-side only. The client gets a generic `{"error": "internal error"}` plus a request ID for support correlation.
- **HTTPS:** enforced by your reverse proxy of choice (Caddy, nginx, Cloudflare Tunnel, Tailscale, your hosting provider). Poindexter does not terminate TLS itself.
- **Container isolation:** every service runs in its own docker container with no privileged escalation, no host network mode, and read-only root filesystems where the underlying image supports it.
- **Default-deny posture:** every settings access fails loud if the value is missing rather than silently using a hardcoded fallback. This means a misconfigured deploy crashes early, before it can run with surprising defaults.

---

## Dependency Audits

Run regularly to catch known CVEs in upstream packages:

```bash
# JavaScript dependencies (frontend)
npm audit

# Python dependencies (backend)
poetry show --outdated
poetry export | pip-audit
```

There are no automated dependency updates in the public repo. Self-hosted installs are responsible for their own update cadence.

---

## Threat Model — what Poindexter is and isn't designed to handle

Poindexter is designed to run on a single host (physical or VM) controlled by a single operator, optionally with a cloud-hosted public site that pulls static JSON from an S3-compatible bucket. The default install assumes:

- The operator has physical or admin access to the machine running the worker.
- The operator's own network is trusted.
- External access to the worker API is gated by the operator's reverse proxy and per-consumer OAuth 2.1 JWTs.

Out of scope for the default install:

- Multi-tenant SaaS isolation (one Poindexter install = one operator).
- Hardening against a malicious operator with shell access on the same machine.
- Automatic key rotation (you do it manually on schedule).
- WAF / DDoS mitigation (delegate to your reverse proxy or CDN).

If you deploy Poindexter in a way that doesn't match these assumptions, the security model needs review.

---

## Contact

- Vulnerability reports: **security@gladlabs.io**
- General code-of-conduct concerns: **conduct@gladlabs.io**
- Commercial licensing or support inquiries: **sales@gladlabs.io**

---

Apache License 2.0 — see [LICENSE](LICENSE).
