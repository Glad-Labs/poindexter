# Secret Rotation Runbook

**Last reviewed:** 2026-04-30
**Audience:** solo operator (Matt) at 2am during an incident, or doing scheduled rotation
**Prereqs:** Local PC online, Docker running, gh CLI authed, poindexter CLI installed, `~/.poindexter/bootstrap.toml` accessible, `POINDEXTER_SECRET_KEY` set

Every secret in Poindexter has a rotation procedure. Most live in `app_settings` with `is_secret=true` and are encrypted at rest via `pgcrypto` — see [`src/cofounder_agent/plugins/secrets.py`](../../src/cofounder_agent/plugins/secrets.py) for how that works. Two secrets (`database_url` and `POINDEXTER_SECRET_KEY` itself) are bootstrap-only and live in `~/.poindexter/bootstrap.toml`.

This runbook lists every known secret, where to obtain a fresh value, how to set the new value, and what to restart to pick it up.

---

## Scenarios this covers

- Routine 90-day rotation of API keys
- Emergency rotation after suspected compromise
- Re-seeding after the encryption key was lost (see [`disaster-recovery.md`](./disaster-recovery.md) CONFIG-2)
- Updating a third-party token that the provider expired / regenerated

For initial provisioning, see [`local-development-setup.md`](./local-development-setup.md). For incident triage, see [`incident-response.md`](./incident-response.md).

---

## Quick triage flowchart

```
What kind of rotation is this?

  Routine (calendar / proactive)
    -> Pick the secret, follow its procedure below.

  Forced by provider (key expired, vendor rotated)
    -> Same procedure but you don't get to control timing —
       the integration is already broken.

  Suspected compromise (logs show unexpected access, key leaked)
    -> Rotate ALL of: api_token, api_auth_token, jwt_secret_key,
       revalidate_secret, every webhook secret. Run "Re-seed all" below.

  Lost POINDEXTER_SECRET_KEY (encrypted blobs unreadable)
    -> See disaster-recovery.md CONFIG-2, then come back here and
       follow "Re-seeding from scratch" for every secret you actually use.
```

---

## How rotation works (quick model)

Every encrypted secret follows this lifecycle:

1. **Generate** a new value (via the provider's UI / API, or `openssl rand`)
2. **Set** the new value into `app_settings` via `poindexter set` or `set_secret(...)`. This encrypts under the current `POINDEXTER_SECRET_KEY`.
3. **Restart** any in-process consumers so they re-fetch from `app_settings`. Most async consumers call `await site_config.get_secret(...)` per-request and don't need a restart; some sync constructors cache at startup and DO.
4. **Verify** the rotation by triggering a real downstream call (test webhook, test alert, etc.).
5. **Revoke** the old value at the provider (when applicable).

The `poindexter set` CLI (or direct DB UPDATE, or the `set_secret` Python helper) is your write surface. The `poindexter settings get <key>` command will show `(encrypted)` for `is_secret=true` rows — you cannot read the plaintext back, by design.

---

## Inventory — every known secret

Pulled `2026-04-30` from `app_settings WHERE is_secret = TRUE`, plus the two bootstrap secrets and a few that are still being migrated to encrypted-at-rest.

| Key                                            | Where it lives                                      | Used for                              | Rotate via                                                      |
| ---------------------------------------------- | --------------------------------------------------- | ------------------------------------- | --------------------------------------------------------------- |
| `database_url`                                 | `bootstrap.toml`                                    | DB connection                         | [§ database_url](#database_url)                                 |
| `POINDEXTER_SECRET_KEY`                        | `bootstrap.toml` (env)                              | Encrypts every other secret           | [§ POINDEXTER_SECRET_KEY](#poindexter_secret_key)               |
| `api_token`                                    | `app_settings` (encrypted)                          | Worker Bearer auth (API)              | [§ api_token](#api_token)                                       |
| `api_auth_token`                               | `app_settings` (encrypted)                          | Legacy alias for `api_token`          | [§ api_auth_token](#api_auth_token)                             |
| `revalidate_secret`                            | `app_settings` (encrypted)                          | Vercel ISR revalidation header        | [§ revalidate_secret](#revalidate_secret)                       |
| `jwt_secret_key`                               | `app_settings` (encrypted)                          | JWT signing                           | [§ jwt_secret_key](#jwt_secret_key)                             |
| `secret_key`                                   | `app_settings` (encrypted)                          | App-wide signing                      | [§ secret_key](#secret_key)                                     |
| `openclaw_webhook_token`                       | `app_settings` (encrypted)                          | OpenClaw → worker auth                | [§ openclaw_webhook_token](#openclaw_webhook_token)             |
| `alertmanager_webhook_token`                   | `app_settings` (encrypted)                          | Alertmanager → worker auth            | [§ alertmanager_webhook_token](#alertmanager_webhook_token)     |
| `telegram_bot_token`                           | `app_settings` (encrypted)                          | Telegram bot API                      | [§ telegram_bot_token](#telegram_bot_token)                     |
| `discord_bot_token`                            | `app_settings` (encrypted)                          | Discord bot API                       | [§ discord_bot_token](#discord_bot_token)                       |
| `discord_voice_bot_token`                      | `app_settings` (encrypted)                          | Discord voice bot                     | [§ discord_voice_bot_token](#discord_voice_bot_token)           |
| `discord_*_webhook`                            | `app_settings` (encrypted)                          | Per-channel webhooks                  | [§ discord webhooks](#discord-webhooks)                         |
| `lemon_squeezy_webhook_secret`                 | `app_settings` (encrypted)                          | Lemon Squeezy webhook HMAC            | [§ lemon_squeezy_webhook_secret](#lemon_squeezy_webhook_secret) |
| `resend_api_key`                               | `app_settings` (encrypted)                          | Newsletter email send                 | [§ resend_api_key](#resend_api_key)                             |
| `resend_webhook_secret`                        | `app_settings` (encrypted)                          | Resend webhook HMAC                   | [§ resend_webhook_secret](#resend_webhook_secret)               |
| `smtp_password`                                | `app_settings` (NOT yet encrypted as of 2026-04-30) | SMTP fallback                         | [§ smtp_password](#smtp_password)                               |
| `uptime_kuma_api_key`                          | `app_settings` (encrypted)                          | Uptime Kuma push                      | [§ uptime_kuma_api_key](#uptime_kuma_api_key)                   |
| `openai_api_key`                               | `app_settings` (encrypted)                          | OpenAI fallback                       | [§ openai_api_key](#openai_api_key)                             |
| `anthropic_api_key`                            | `app_settings` (encrypted)                          | Claude Haiku critic                   | [§ anthropic_api_key](#anthropic_api_key)                       |
| `gemini_api_key`                               | `app_settings` (encrypted)                          | Gemini fallback                       | [§ gemini_api_key](#gemini_api_key)                             |
| `pexels_api_key`                               | `app_settings` (encrypted)                          | Stock image fallback                  | [§ pexels_api_key](#pexels_api_key)                             |
| `cloudinary_api_key` + `cloudinary_api_secret` | `app_settings` (encrypted)                          | Image CDN                             | [§ cloudinary keys](#cloudinary-keys)                           |
| `storage_secret_key` + `storage_token`         | `app_settings` (encrypted)                          | S3-compatible object storage (R2)     | [§ storage keys](#storage-keys)                                 |
| `redis_url`                                    | `app_settings` (encrypted)                          | Redis connection (with AUTH password) | [§ redis_url](#redis_url)                                       |
| `gitea_password`                               | `app_settings` (encrypted)                          | Gitea (decommissioning 2026-04-30)    | [§ gitea_password](#gitea_password)                             |
| `bluesky_app_password` + `bluesky_identifier`  | `app_settings` (encrypted)                          | Bluesky cross-post                    | [§ bluesky keys](#bluesky-keys)                                 |
| `mastodon_access_token`                        | `app_settings` (encrypted)                          | Mastodon cross-post                   | [§ mastodon_access_token](#mastodon_access_token)               |
| `devto_api_key`                                | `app_settings` (encrypted)                          | Dev.to cross-post                     | [§ devto_api_key](#devto_api_key)                               |
| `notion_api_key`                               | `app_settings` (encrypted)                          | Notion integration                    | [§ notion_api_key](#notion_api_key)                             |
| `mercury_api_token`                            | `app_settings` (encrypted)                          | Mercury banking                       | [§ mercury_api_token](#mercury_api_token)                       |
| `grafana_api_key` + `grafana_api_token`        | `app_settings` (encrypted)                          | Grafana Cloud API                     | [§ grafana keys](#grafana-keys)                                 |
| `google_api_key`                               | `app_settings` (encrypted)                          | Google APIs                           | [§ google_api_key](#google_api_key)                             |
| `elevenlabs_api_key`                           | `app_settings` (encrypted)                          | TTS provider                          | [§ elevenlabs_api_key](#elevenlabs_api_key)                     |

---

## Generic procedure (covers ~90% of the keys)

Most rotations follow this exact recipe:

```bash
# 1. Generate at the provider — copy the new value into a secure clipboard
#    (or generate locally for self-issued tokens)

# 2. Save to a 0600 file so we don't echo it
NEWVAL_FILE=$(mktemp)
chmod 600 "$NEWVAL_FILE"
# Paste the new value into $NEWVAL_FILE; save and exit.
# (or:  echo -n "<new value>" > "$NEWVAL_FILE"  --  but this exposes it in shell history)

# 3. Set the new value via plugins.secrets (encrypts under current key)
docker exec -i poindexter-worker python -c "
import asyncio, asyncpg, os, sys
sys.path.insert(0, '/app/src/cofounder_agent')
from plugins.secrets import set_secret
async def main():
    val = open('/tmp/newval').read().strip()
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    await set_secret(conn, '<KEY>', val)
    await conn.close()
asyncio.run(main())
" </tmp/newval

# Or: poindexter set <key> "<value>"   if you accept the value being on the cmdline.
# Prefer the helper above for true secrets.

# 4. Restart any consumer that caches at startup
docker restart poindexter-worker     # most secrets — async-fetched per request
# (some specific ones need additional restarts — see the per-key sections)

# 5. Verify with a real downstream call (see per-key sections)

# 6. Revoke the old value at the provider
```

**Why not always `poindexter set`?** It works, but it puts the secret value in your shell history and the click argv. `set_secret(...)` via the helper above keeps the secret in a 0600 file that you delete after.

---

## Per-key rotation procedures

### `database_url`

**Lives in.** `~/.poindexter/bootstrap.toml`, key `database_url`.

**When to rotate.** Postgres password compromised, or you're moving the DB to a new host.

**Procedure.**

```bash
# 1. Change the postgres password
docker exec -it poindexter-postgres-local psql -U poindexter -d postgres
# In psql:
ALTER USER poindexter WITH PASSWORD '<new password>';
\q

# 2. Edit ~/.poindexter/bootstrap.toml — update the connection string
chmod 600 ~/.poindexter/bootstrap.toml

# 3. Restart everything that connects to the DB
docker restart poindexter-worker poindexter-brain-daemon poindexter-grafana
```

**Verify.**

```bash
curl -s http://localhost:8002/api/health | python -m json.tool
# database.connected should be true
```

---

### `POINDEXTER_SECRET_KEY`

**Lives in.** `~/.poindexter/bootstrap.toml` (env var name `POINDEXTER_SECRET_KEY`).

**When to rotate.** Annually, or after a suspected compromise. **THIS IS THE DOOMSDAY KEY.** Losing it without rotating means every encrypted `app_settings` row becomes garbage forever (see [`disaster-recovery.md`](./disaster-recovery.md) CONFIG-2).

**Procedure.** Use the `rotate_key` helper from `plugins.secrets` — it decrypts every secret with the OLD key and re-encrypts with the NEW one in a single transaction.

```bash
# 1. Generate a new key (save it RIGHT NOW to 1Password)
NEW_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
echo "$NEW_KEY"
# Paste this into 1Password. DO NOT lose it.

# 2. Capture the current key
OLD_KEY="$POINDEXTER_SECRET_KEY"

# 3. Run the rotation (re-encrypts every is_secret=true row)
docker exec -i poindexter-worker python -c "
import asyncio, asyncpg, os, sys
sys.path.insert(0, '/app/src/cofounder_agent')
from plugins.secrets import rotate_key
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    n = await rotate_key(conn, old_key='$OLD_KEY', new_key='$NEW_KEY')
    print(f'Rotated {n} secret(s)')
    await conn.close()
asyncio.run(main())
"
# Expected: "Rotated <N> secret(s)" (matches your is_secret=true count)

# 4. Update bootstrap.toml with the new key
# Edit POINDEXTER_SECRET_KEY = "<new key>"  in ~/.poindexter/bootstrap.toml

# 5. Restart everything that decrypts secrets
docker restart poindexter-worker poindexter-brain-daemon
```

**Verify.**

```bash
# Trigger a decrypt round-trip
curl -s http://localhost:8002/api/health
docker logs poindexter-worker 2>&1 | grep -i "SecretsError"
# Expected: no SecretsError lines after the restart.
```

**Revoke.** N/A — the old key isn't held anywhere external. Just remove it from your password manager once you've confirmed the rotation took.

---

### `api_token`

**Lives in.** `app_settings.api_token` (encrypted, `is_secret=true`).

**Used for.** Bearer auth on the worker REST API. Every MCP / OpenClaw call uses this.

**Procedure.** Use the rotation script that already exists.

```bash
# 1. Generate
NEWTOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
TOKENFILE=$(mktemp); chmod 600 "$TOKENFILE"
echo -n "$NEWTOKEN" > "$TOKENFILE"

# 2. Set in DB (encrypted) AND flip development_mode off
POINDEXTER_SECRET_KEY="$POINDEXTER_SECRET_KEY" \
DATABASE_URL="$(grep ^database_url ~/.poindexter/bootstrap.toml | cut -d'"' -f2)" \
  python scripts/_rotate_api_token.py "$TOKENFILE"

# 3. Push the new token to every consumer (~/.claude.json, openclaw.json, bootstrap.toml)
python scripts/_rotate_consumer_configs.py "$TOKENFILE"

# 4. Cleanup
shred -u "$TOKENFILE" 2>/dev/null || rm -f "$TOKENFILE"

# 5. Restart consumers
docker restart poindexter-worker
# Restart Claude Desktop and OpenClaw gateway so they re-read their JSON configs.
```

**Verify.**

```bash
# Old token should now 401
curl -sH "Authorization: Bearer <OLD_TOKEN>" http://localhost:8002/api/health -o /dev/null -w "%{http_code}\n"
# Expected: 401

# New token works
curl -sH "Authorization: Bearer $NEWTOKEN" http://localhost:8002/api/health -o /dev/null -w "%{http_code}\n"
# Expected: 200
```

**Reference.** See `scripts/_rotate_api_token.py` and `scripts/_rotate_consumer_configs.py` for the underlying mechanics.

---

### `api_auth_token`

**Lives in.** `app_settings.api_auth_token` (encrypted). **Legacy alias** for `api_token` — `0106_drop_duplicate_api_auth_token.py` migration is collapsing this to a single key. If both exist, they MUST hold the same value.

**Procedure.** Same as `api_token` above. After rotating `api_token`, also:

```bash
poindexter set api_auth_token "$NEWTOKEN"
# Or via set_secret in a Python one-liner if encrypted.
```

Verify both rows hold matching encrypted ciphertext. Long-term: this row gets dropped — see `0106_drop_duplicate_api_auth_token.py`.

---

### `revalidate_secret`

**Lives in.** `app_settings.revalidate_secret` (encrypted).

**Used for.** Worker → Vercel ISR revalidation. Sent as `x-revalidate-secret` header. The Vercel function compares against an env var on the Vercel side.

**Procedure.**

```bash
# 1. Generate
NEW=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# 2. Set in DB
poindexter set revalidate_secret "$NEW"   # OR use set_secret helper

# 3. Update on Vercel — this MUST happen within seconds of step 2,
#    otherwise revalidation 401s until you do.
vercel env rm REVALIDATE_SECRET production
echo "$NEW" | vercel env add REVALIDATE_SECRET production

# 4. Trigger a Vercel redeploy so the new env var lands in the runtime
vercel --prod

# 5. Restart worker so it picks up the new value
docker restart poindexter-worker
```

**Verify.**

```bash
# Trigger a revalidation against a real post slug
curl -s -X POST http://localhost:8002/api/revalidate \
  -H "Authorization: Bearer $(grep ^api_token ~/.poindexter/bootstrap.toml | cut -d'"' -f2)" \
  -d '{"path": "/posts/<some-slug>"}'
# Expected: {"revalidated": true}

# Check Vercel logs for the revalidation hit
vercel logs --follow | grep revalidate
```

---

### `jwt_secret_key`

**Lives in.** `app_settings.jwt_secret_key` (encrypted).

**When to rotate.** Suspected compromise, or after major code change to JWT issuance. **Rotation invalidates every existing JWT** — anyone holding one will need to re-auth.

**Procedure.** Generic procedure. Restart `poindexter-worker` so the new key is picked up before issuing the next JWT.

---

### `secret_key`

**Lives in.** `app_settings.secret_key` (encrypted).

**Used for.** General-purpose app signing (CSRF tokens, signed cookies). **TBD — needs operator confirmation:** the exact callsites for this key aren't fully audited; treat as `jwt_secret_key`-equivalent.

---

### `openclaw_webhook_token`

**Lives in.** `app_settings.openclaw_webhook_token` (encrypted).

**Used for.** Worker → OpenClaw gateway auth (`Authorization` header).

**Procedure.**

```bash
# 1. Generate
NEW=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# 2. Set in DB
poindexter set openclaw_webhook_token "$NEW"   # OR use set_secret helper

# 3. Update OpenClaw config
# Edit ~/.openclaw/openclaw.json — set the gateway's expected token.

# 4. Restart both
docker restart poindexter-worker
# Restart OpenClaw (Windows shortcut or:)
start "OpenClaw Gateway" cmd /d /c "%USERPROFILE%\.openclaw\gateway.cmd"
```

---

### `alertmanager_webhook_token`

**Lives in.** `app_settings.alertmanager_webhook_token` (encrypted).

**Used for.** Alertmanager → worker webhook auth.

**Procedure.** Generic. After updating the DB, also update `infrastructure/prometheus/alertmanager.yml` (the matching value lives there) and restart Alertmanager:

```bash
docker restart poindexter-alertmanager
```

---

### `telegram_bot_token`

**Lives in.** `app_settings.telegram_bot_token` (encrypted) AND optionally `bootstrap.toml` (operator notification fallback).

**When to rotate.** Suspected leak (the token is in the URL path of every Telegram API call — it's surprisingly leaky). Or @BotFather forced rotation.

**Procedure.**

```bash
# 1. Open Telegram, message @BotFather:
#    /mybots -> select your bot -> API Token -> Revoke current token
#    BotFather hands you the new token. Save it.

# 2. Set in DB
poindexter set telegram_bot_token "<new-token>"

# 3. ALSO update bootstrap.toml fallback (for operator notifications when DB is down)
# Edit ~/.poindexter/bootstrap.toml -> telegram_bot_token = "<new-token>"
chmod 600 ~/.poindexter/bootstrap.toml

# 4. Restart consumers
docker restart poindexter-worker poindexter-brain-daemon
# Telegram bot daemon (if running standalone):
# Windows: pythonw scripts/telegram-bot.py
```

**Verify.**

```bash
# Trigger a test alert to your chat
curl -s -X POST http://localhost:8002/api/test/notify-operator \
  -H "Authorization: Bearer $(grep ^api_token ~/.poindexter/bootstrap.toml | cut -d'"' -f2)"
# Expected: a Telegram message arrives within seconds.
```

---

### `discord_bot_token`

**Lives in.** `app_settings.discord_bot_token` (encrypted).

**Procedure.**

```bash
# 1. Open https://discord.com/developers/applications
#    -> select app -> Bot -> Reset Token (this revokes the old one immediately)
#    Copy the new token.

# 2. Set in DB
poindexter set discord_bot_token "<new-token>"

# 3. Restart Discord bot consumer
docker restart poindexter-worker
# If voice bot is separate: docker restart poindexter-voice-bot
```

---

### `discord_voice_bot_token`

**Lives in.** `app_settings.discord_voice_bot_token` (encrypted).

**Procedure.** Same as `discord_bot_token` but for the voice-bot Discord application. Restart `poindexter-voice-bot`.

---

### Discord webhooks

Keys: `discord_ops_webhook_url`, `discord_alerts_webhook_url`, etc. (per-channel webhook URLs that include a secret token in the path).

**Procedure.**

```bash
# 1. In Discord: Server Settings -> Integrations -> Webhooks
#    -> select webhook -> Copy Webhook URL (regenerates if needed)

# 2. Set in DB
poindexter set discord_ops_webhook_url "https://discord.com/api/webhooks/.../...token"

# 3. ALSO update bootstrap.toml fallback if this is the ops webhook
# Edit ~/.poindexter/bootstrap.toml -> discord_ops_webhook_url

# 4. Restart consumers
docker restart poindexter-worker poindexter-brain-daemon
```

---

### `lemon_squeezy_webhook_secret`

**Lives in.** `app_settings.lemon_squeezy_webhook_secret` (encrypted as of 2026-04-27).

**Used for.** HMAC verification of Lemon Squeezy webhooks (subscription created / cancelled / order paid).

**Procedure.**

```bash
# 1. In Lemon Squeezy dashboard: Settings -> Webhooks -> select your webhook
#    -> "Reveal Signing Secret" / "Regenerate"
#    Copy the new value.

# 2. Set in DB
poindexter set lemon_squeezy_webhook_secret "<new-secret>"

# 3. Restart worker so the verifier reads the new value on next request
#    (Actually, since the verifier is async and reads per-request, this
#     might not strictly require a restart — but do it anyway for safety.)
docker restart poindexter-worker
```

**Verify.** Trigger a test webhook from Lemon Squeezy dashboard. Worker logs should show a 200 response, not 401.

---

### `resend_api_key`

**Lives in.** `app_settings.resend_api_key` (encrypted).

**Used for.** Newsletter emails via Resend.

**Procedure.**

```bash
# 1. https://resend.com/api-keys -> Delete old key, Create new key
# 2. poindexter set resend_api_key "<new-key>"
# 3. docker restart poindexter-worker
```

**Verify.** Send a test newsletter to one address — see `poindexter newsletter --help`.

---

### `resend_webhook_secret`

**Lives in.** `app_settings.resend_webhook_secret` (encrypted as of 2026-04-27).

**Used for.** HMAC verification of Resend delivery webhooks.

**Procedure.** Same shape as Lemon Squeezy. New secret comes from Resend dashboard → Webhooks → endpoint → Signing Secret.

---

### `smtp_password`

**Lives in.** `app_settings.smtp_password` — **NOT YET encrypted as of 2026-04-30** (per `gh-107-secret-keys-audit-2026-04-24.md`). Migration `0121_flip_smtp_password_secret.py` flips the row to `is_secret=true` once seeded.

**TBD — needs operator confirmation:** verify with `psql -c "SELECT is_secret FROM app_settings WHERE key='smtp_password'"`. If `is_secret=false`, set as plain `poindexter set`. If `is_secret=true`, use the encrypted helper. The newsletter `_cfg` callsite is documented as needing migration to `await get_secret(...)` once this row is encrypted.

**Procedure.**

```bash
# 1. Generate / obtain new SMTP password from your email host
# 2. (If is_secret=true:) use the encrypted helper.
#    (If is_secret=false:) poindexter set smtp_password "<new-pw>"
# 3. docker restart poindexter-worker
```

---

### `uptime_kuma_api_key`

**Lives in.** `app_settings.uptime_kuma_api_key` (encrypted, seeded by `0120_seed_uptime_kuma_api_key.py`).

**Used for.** Pushing health beacons to Uptime Kuma.

**Procedure.**

```bash
# 1. In Uptime Kuma: Settings -> API Keys -> Create / Regenerate
#    Copy the new key.

# 2. poindexter set uptime_kuma_api_key "<new-key>"

# 3. docker restart poindexter-worker poindexter-brain-daemon
```

**Verify.** Watch Uptime Kuma — heartbeats should resume within 60 seconds.

---

### `openai_api_key`

**Lives in.** `app_settings.openai_api_key` (encrypted).

**Used for.** OpenAI fallback in the model fallback chain (only used when explicitly enabled in `app_settings` AND gated by `cost_guard`).

**Procedure.**

```bash
# 1. https://platform.openai.com/api-keys -> Revoke old, Create new
# 2. poindexter set openai_api_key "sk-..."
# 3. docker restart poindexter-worker
```

**Cost guardrails.** Confirm `cost_guard_enabled=true` and `openai_enabled=true` only if you intend to actually use it. Per Matt's policy, local Ollama is the default — paid providers are opt-in fallbacks ONLY.

---

### `anthropic_api_key`

**Lives in.** `app_settings.anthropic_api_key` (encrypted).

**Used for.** Claude Haiku adversarial QA review (cross-model_qa stage).

**Procedure.**

```bash
# 1. https://console.anthropic.com/settings/keys -> Revoke old, Create new
# 2. poindexter set anthropic_api_key "sk-ant-..."
# 3. docker restart poindexter-worker
```

**Verify.** Run a content task end-to-end; check that the cross-model QA stage logs a Claude Haiku call rather than skipping.

---

### `gemini_api_key`

**Lives in.** `app_settings.gemini_api_key` (encrypted).

**Used for.** Gemini fallback. **High risk** — Matt previously spent $300 in one night via Gemini before cost guards. Keep `cost_guard_enabled=true` and `gemini_enabled=false` unless you actively need it.

**Procedure.** Generic. Same as OpenAI.

---

### `pexels_api_key`

**Lives in.** `app_settings.pexels_api_key` (encrypted).

**Used for.** Stock image fallback when SDXL is degraded.

**Procedure.**

```bash
# 1. https://www.pexels.com/api/new -> regenerate / new key
# 2. poindexter set pexels_api_key "<new-key>"
# 3. docker restart poindexter-worker
```

---

### Cloudinary keys

Keys: `cloudinary_api_key` + `cloudinary_api_secret` (both encrypted).

**Procedure.**

```bash
# 1. https://console.cloudinary.com/settings/api-keys -> regenerate
# 2. poindexter set cloudinary_api_key "<new-key>"
#    poindexter set cloudinary_api_secret "<new-secret>"
# 3. docker restart poindexter-worker
```

**Verify.** Trigger an image upload (e.g., re-run `RegenerateStockImagesJob`). Watch worker logs for Cloudinary 200 responses.

---

### Storage keys

Keys: `storage_secret_key` + `storage_token` (both encrypted). Provider-agnostic — works with R2, S3, B2, MinIO. Old `cloudflare_r2_*` keys are deprecated fallbacks.

**Procedure.**

```bash
# 1. At your storage provider (R2/S3/etc.) -> create new access key, deactivate old
# 2. poindexter set storage_secret_key "<new-key>"
#    poindexter set storage_token "<new-token>"
# 3. docker restart poindexter-worker
```

**Verify.** Trigger a static export rebuild — watch for object writes to land in the bucket.

---

### `redis_url`

**Lives in.** `app_settings.redis_url` (encrypted — the URL contains the AUTH password).

**Procedure.**

```bash
# 1. Update Redis password
# Edit redis.conf or run:
docker exec poindexter-prefect-redis redis-cli CONFIG SET requirepass "<new-password>"

# 2. Build new URL: redis://default:<new-password>@<host>:<port>
# 3. poindexter set redis_url "redis://default:<new-password>@..."
# 4. docker restart poindexter-worker
```

**Verify.** `RedisCache` initialization in worker logs should not show "degraded to no-cache mode."

---

### `gitea_password`

**Lives in.** `app_settings.gitea_password` (encrypted).

**Note.** Gitea is being decommissioned 2026-04-30 per the two-remote model in `CLAUDE.md`. This row may already be stale. Verify whether anything still references it before rotating.

---

### Bluesky keys

Keys: `bluesky_app_password` + `bluesky_identifier` (both encrypted).

**Procedure.**

```bash
# 1. Bluesky app: Settings -> App Passwords -> Add (or regenerate)
# 2. poindexter set bluesky_app_password "<new-app-password>"
# (bluesky_identifier is your handle — only changes if you change handles)
# 3. docker restart poindexter-worker
```

---

### `mastodon_access_token`

**Procedure.** New token from your Mastodon instance: Settings → Development → New Application (or revoke + recreate). Generic flow afterwards.

---

### `devto_api_key`

**Procedure.** https://dev.to/settings/extensions → API Keys → Generate new. Generic flow.

---

### `notion_api_key`

**Procedure.** https://www.notion.so/my-integrations → select integration → Refresh / Reset. Generic flow.

---

### `mercury_api_token`

**Procedure.** Mercury dashboard → Settings → API Tokens → Revoke + create. Generic flow.

---

### Grafana keys

Keys: `grafana_api_key` + `grafana_api_token` (both encrypted; some duplication for legacy reasons).

**Procedure.**

```bash
# 1. https://gladlabs.grafana.net -> Configuration -> API keys -> Add new
# 2. poindexter set grafana_api_key "<new-key>"
#    poindexter set grafana_api_token "<new-token>"   # if both rows exist
# 3. docker restart poindexter-worker
```

---

### `google_api_key`

**Procedure.** https://console.cloud.google.com → APIs & Services → Credentials → Regenerate. Generic flow. Note: this single key may grant access to multiple Google APIs depending on which APIs are enabled — review the API restrictions before rotating.

---

### `elevenlabs_api_key`

**Procedure.** https://elevenlabs.io → Profile → API Keys → Generate / Revoke. Generic flow.

---

## Re-seeding from scratch (after losing `POINDEXTER_SECRET_KEY`)

After a CONFIG-2 disaster recovery (lost encryption key), you have an empty `app_settings` for every secret. Walk through this checklist:

```text
[ ] api_token                       (#critical — worker auth)
[ ] revalidate_secret               (#critical — Vercel ISR)
[ ] telegram_bot_token              (#critical — alerts)
[ ] discord_bot_token               (if used)
[ ] discord_*_webhook               (per channel)
[ ] openclaw_webhook_token          (if OpenClaw is wired)
[ ] alertmanager_webhook_token      (if Alertmanager is wired)
[ ] lemon_squeezy_webhook_secret    (if accepting payments)
[ ] resend_api_key + resend_webhook_secret  (if sending newsletters)
[ ] uptime_kuma_api_key             (if pushing to Kuma)
[ ] openai_api_key                  (only if you actively use it)
[ ] anthropic_api_key               (cross-model QA)
[ ] gemini_api_key                  (only if you actively use it)
[ ] pexels_api_key                  (image fallback)
[ ] cloudinary_api_key + secret     (image CDN)
[ ] storage_secret_key + token      (R2/S3 — required for static export)
[ ] redis_url                       (if Redis is wired)
[ ] bluesky / mastodon / devto      (cross-post adapters)
[ ] grafana_api_key + token         (if calling Grafana API from worker)
```

For each one: go to the section above, generate a new value at the provider, set it in DB, restart the consumer.

---

## Verification — was the rotation actually applied?

```bash
# Confirm the row is encrypted (starts with enc:v1:)
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -c \
  "SELECT key, value LIKE 'enc:v1:%%' AS encrypted, length(value) AS len, updated_at
   FROM app_settings WHERE key = '<KEY>';"

# Confirm the worker can decrypt it (no SecretsError in logs)
docker logs poindexter-worker --since=2m 2>&1 | grep -i "SecretsError\|<KEY>"

# Trigger a downstream call that uses the secret. Look for HTTP 2xx, not 401.
```

---

## Rotation cadence — calendar

**TBD — needs operator to confirm cadence preferences:**

- `POINDEXTER_SECRET_KEY` — annually (low risk if undisturbed; high blast radius if leaked)
- `api_token`, `revalidate_secret`, `openclaw_webhook_token` — every 90 days
- Bot tokens (Telegram, Discord) — only on suspected compromise (rotation invalidates active sessions)
- Paid API keys (OpenAI, Anthropic, Gemini, Resend, Cloudinary) — every 90 days, or on bill anomaly
- Webhook secrets (Lemon Squeezy, Resend) — only when the provider rotates them or on suspected compromise
- Cross-post adapter tokens (Bluesky, Mastodon, Dev.to) — yearly

A scheduled agent should be set up to remind on this cadence (see `/schedule` skill).

---

## See also

- [`disaster-recovery.md`](./disaster-recovery.md) — recovery from lost key (CONFIG-2)
- [`incident-response.md`](./incident-response.md) — alert routing
- `src/cofounder_agent/plugins/secrets.py` — encryption module reference
- `scripts/_rotate_api_token.py` — `api_token` rotation helper
- `scripts/_rotate_consumer_configs.py` — pushes new `api_token` to consumer JSONs
- `docs/architecture/gh-107-secret-keys-audit-2026-04-24.md` — full audit of every secret callsite
