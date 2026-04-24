# GH-107 — Encrypted app_settings audit (2026-04-24)

Three production incidents on 2026-04-23 (Alertmanager dispatch, Vercel
revalidation, auto-Telegram post-publish) all fired the same root cause:
a caller used `site_config.get("foo_secret", "")` on a row whose
`is_secret=true`, received the `enc:v1:<base64-ciphertext>` blob, and
shipped that ciphertext into the wire (Authorization header,
`x-revalidate-secret` header, bot-API URL path). The receiving service
saw an opaque garbage token and 401'd.

This audit walks every callsite of `site_config.get(...)` in
`src/cofounder_agent/` whose key is flagged `is_secret=true` in
`app_settings`, and migrates those that live under an `async def` to
`await site_config.get_secret(...)`. Sites whose containing function is
synchronous are deferred to a follow-up async-color refactor — they're
listed at the bottom.

## Inventory — secret keys in `app_settings`

Pulled `2026-04-24` via:

```
docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain \
  -c "SELECT key FROM app_settings WHERE is_secret = TRUE ORDER BY key;"
```

31 rows:

```
alertmanager_webhook_token
anthropic_api_key
api_auth_token
api_token
bluesky_app_password
bluesky_identifier
cloudinary_api_key
cloudinary_api_secret
devto_api_key
discord_bot_token
discord_voice_bot_token
elevenlabs_api_key
gemini_api_key
gitea_password
google_api_key
grafana_api_key
grafana_api_token
jwt_secret_key
mastodon_access_token
mercury_api_token
notion_api_key
openai_api_key
openclaw_webhook_token
pexels_api_key
redis_url
resend_api_key
revalidate_secret
secret_key
storage_secret_key
storage_token
telegram_bot_token
```

## Callsites — fixed in this PR

All sites below already lived inside `async def` functions, so the
migration is a one-line swap from `site_config.get(...)` to
`await site_config.get_secret(...)`. No function-color changes.

| File:line                                                         | Key                      | Containing function                     | Notes                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------------------------------------------- | ------------------------ | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/cofounder_agent/middleware/api_token_auth.py:86`             | `api_token`              | `verify_api_token` (async)              | Bearer-token check; previously compared the request token against `enc:v1:...` ciphertext via `hmac.compare_digest`, which always returned False for a real, non-encrypted bearer token. This was the silent 401-everything failure mode.                                                                                                                                                                                 |
| `src/cofounder_agent/middleware/api_token_auth.py:148`            | `api_token`              | `verify_api_token_optional` (async)     | Same pattern as above; optional variant.                                                                                                                                                                                                                                                                                                                                                                                  |
| `src/cofounder_agent/services/redis_cache.py:163`                 | `redis_url`              | `RedisCache.create` (async classmethod) | A `redis://user:pass@host` URL with an AUTH password is itself a secret. With ciphertext, `aioredis.from_url` would fail to parse and quietly degrade to no-cache mode.                                                                                                                                                                                                                                                   |
| `src/cofounder_agent/services/image_providers/sdxl.py:786`        | `cloudinary_api_key`     | `_upload_to_cloudinary` (async)         | `cloudinary.config(...)` rejects ciphertext as an invalid key.                                                                                                                                                                                                                                                                                                                                                            |
| `src/cofounder_agent/services/image_providers/sdxl.py:787`        | `cloudinary_api_secret`  | `_upload_to_cloudinary` (async)         | Same path.                                                                                                                                                                                                                                                                                                                                                                                                                |
| `src/cofounder_agent/services/task_executor.py:66`                | `openclaw_webhook_token` | `_notify_openclaw` (async)              | Authorization header to OpenClaw.                                                                                                                                                                                                                                                                                                                                                                                         |
| `src/cofounder_agent/services/revalidation_service.py:51-66`      | `revalidate_secret`      | `trigger_nextjs_revalidation` (async)   | This site had a _second_ bug — a raw `pool.fetchrow("SELECT value FROM app_settings WHERE key = 'revalidate_secret'")` that bypassed decryption entirely. Both the raw fetchrow and the `.get()` fallback below it shipped the `enc:v1:...` blob as the `x-revalidate-secret` header, which is the Vercel revalidation 401 incident. Replaced both with a single `await site_config.get_secret("revalidate_secret", "")`. |
| `src/cofounder_agent/services/social_poster.py:202`               | `openclaw_webhook_token` | `_notify` (async)                       | Authorization header to OpenClaw.                                                                                                                                                                                                                                                                                                                                                                                         |
| `src/cofounder_agent/services/social_poster.py:204`               | `telegram_bot_token`     | `_notify` (async)                       | The auto-Telegram post-publish 401 incident. The bot token is interpolated into `https://api.telegram.org/bot{token}/sendMessage` — ciphertext made the URL path 404 / 401. Fixed in-place via `await site_config.get_secret("telegram_bot_token")` rather than going through the still-sync `services/telegram_config.get_telegram_bot_token` helper (see deferred section below).                                       |
| `src/cofounder_agent/utils/gitea_issues.py:58`                    | `gitea_password`         | `create_gitea_issue` (async)            | Basic auth — ciphertext makes the API call 401.                                                                                                                                                                                                                                                                                                                                                                           |
| `src/cofounder_agent/services/jobs/regenerate_stock_images.py:93` | `cloudinary_api_key`     | `RegenerateStockImagesJob.run` (async)  | Cloudinary client config.                                                                                                                                                                                                                                                                                                                                                                                                 |
| `src/cofounder_agent/services/jobs/regenerate_stock_images.py:94` | `cloudinary_api_secret`  | `RegenerateStockImagesJob.run` (async)  | Cloudinary client config.                                                                                                                                                                                                                                                                                                                                                                                                 |

**Total: 12 callsites fixed across 7 production files.**

In addition, a stub fallback class (`_EnvSiteConfig` in
`middleware/api_token_auth.py`) used by minimal-app tests grew an
`async def get_secret` mirror so callers that migrate to `await
get_secret(...)` keep working when no real `SiteConfig` is wired up.

## Callsites — deferred (sync container, follow-up needed)

These callsites read encrypted keys but live inside `def` (sync)
functions or methods. Per the audit policy, function-color changes
exceed the scope of this PR; they're tracked here for the next cleanup.

| File:line                                                     | Key                      | Containing function                      | Why deferred                                                                                                                                                                                                                                                                                                                                                                               | Follow-up shape                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------- | ------------------------ | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/cofounder_agent/services/webhook_delivery_service.py:35` | `openclaw_webhook_token` | `WebhookDeliveryService.__init__` (sync) | Constructor is called from sync wiring (`main.py` lifespan, DI container). Cannot `await` here.                                                                                                                                                                                                                                                                                            | Stop reading the token in `__init__`; either lazy-load it inside `_deliver_event` (already async), or read it via `await site_config.get_secret(...)` once during `start()` (already async) before entering the delivery loop.                                               |
| `src/cofounder_agent/services/telegram_config.py:34`          | `telegram_bot_token`     | `get_telegram_bot_token` (sync)          | Helper is plain `def`. Used to be called from `social_poster._notify` — this PR replaced that callsite with an inline `await site_config.get_secret("telegram_bot_token")` and dropped the import, so the helper no longer has any production callers. The helper is still publicly exported and would re-introduce the bug if any future code grabs it.                                   | Convert `get_telegram_bot_token` and `telegram_configured` to `async def` (they now both touch a secret), update the test suite (`test_telegram_config.py`) to await them. Or delete `get_telegram_bot_token` outright since its only consumer now reads the token directly. |
| `src/cofounder_agent/services/newsletter_service.py:34`       | `resend_api_key`         | `_cfg` (sync)                            | The newsletter config aggregator builds a flat `dict` of ~13 keys. Caller (`send_newsletter_for_post`) is async, so converting `_cfg` to `async def` is mechanical, but the same `_cfg` dict shape is consumed in three deeper sync helpers (`_send_via_resend`, `_send_via_smtp`, `_build_html`) — the change is well-contained but still touches more than the audit was scoped for.     | Convert `_cfg` to `async def`, replace `cfg["resend_api_key"]` reads with awaited fetches, and bump the test suite (`test_newsletter_service.py`) to `await _cfg(...)`.                                                                                                      |
| `src/cofounder_agent/services/newsletter_service.py:38`       | `smtp_password`          | `_cfg` (sync)                            | Same as above. **Note**: `smtp_password` is _not currently_ `is_secret=true` in `app_settings` — there's no such row at all in the live DB as of 2026-04-24. The site is still in the same shape because anyone who _adds_ the row encrypted would hit the same bug. The follow-up should both (a) flip the encrypted flag when the row gets seeded and (b) move the read to `get_secret`. |

**Total deferred: 4 callsites in 3 files.**

## Out-of-audit observations (related but not in scope)

While walking the codebase the heuristic `_secret|_token|_api_key|_password`
search surfaced four additional keys that are read via `site_config.get()`
but are **not currently** flagged `is_secret=true` in `app_settings`:

- `lemon_squeezy_webhook_secret` — `routes/external_webhooks.py:61`
- `resend_webhook_secret` — `routes/external_webhooks.py:177`
- `smtp_password` — `services/newsletter_service.py:38`
- `eia_api_key` — `services/jobs/update_utility_rates.py:208`

Today these don't return ciphertext (the rows aren't encrypted), so the
audit treats them as out-of-scope for the migration. They _should_
become `is_secret=true` rows the next time the secrets schema is
reviewed — at which point a second sweep with the same methodology will
need to migrate these callsites too. Filing as a heads-up rather than
fixing eagerly, because converting them now would change behaviour
(force a `get_secret` query that returns `None` for a non-encrypted row,
falling through to env vars) without addressing the underlying schema
gap.

## Did NOT touch

- `services/integrations/secret_resolver.py` — explicitly called out in
  the audit instructions as the canonical helper. Already correct.
- `services/site_config.py` — also explicitly out of scope.
- Test files that pass `site_config.get(...)` to a mock — those don't
  hit real encryption. Test mocks were updated only where the mocked
  surface needed to grow `get_secret` to satisfy migrated production
  code.

## Tests

```
cd src/cofounder_agent
python -m pytest \
  tests/unit/middleware/test_api_token_auth.py \
  tests/unit/services/test_redis_cache.py \
  tests/unit/services/test_social_poster.py \
  tests/unit/utils/test_gitea_issues.py \
  tests/unit/services/test_image_providers_sdxl.py \
  tests/unit/routes/test_revalidate_routes.py \
  tests/unit/services/jobs/test_regenerate_stock_images_job.py \
  tests/unit/services/test_task_executor.py \
  tests/unit/services/test_telegram_config.py \
  tests/unit/services/test_webhook_delivery_service.py \
  tests/unit/services/test_newsletter_service.py
```

→ 308 passed.

The originally suggested test paths
(`test_integrations_framework.py`, `test_webhook_dispatcher.py`,
`test_outbound_dispatcher.py`) do not yet exist in this tree —
flagging in case the audit was expected to find them as part of a
landed-but-unmerged feature branch.
