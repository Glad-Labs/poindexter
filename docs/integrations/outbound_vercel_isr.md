# Handler: `outbound.vercel_isr`

Triggers Next.js ISR cache revalidation on a Vercel-hosted frontend. Equivalent to calling `trigger_nextjs_revalidation()` from `services.revalidation_service`, but driven by a declarative row.

## Payload

```python
{}                                                    # use row defaults
{"paths": ["/posts/foo"]}                              # selective revalidation
{"tags": ["post:foo"]}                                 # tag-based revalidation
{"paths": ["/"], "tags": ["posts"]}                    # both
```

Empty/missing keys fall back to `row.config.default_paths` / `row.config.default_tags`, then to built-in defaults (`["/", "/archive"]` and `["posts", "post-index"]`).

## Row configuration

```
name:               vercel_isr  (or any slug)
direction:          outbound
handler_name:       vercel_isr
url:                https://<your-domain>          (handler appends /api/revalidate)
signing_algorithm:  none                            (Vercel uses a header-based secret, not an HMAC signature)
secret_key_ref:     revalidate_secret               (app_settings key for the shared secret)
config:             {
                      "default_paths": ["/", "/archive"],
                      "default_tags": ["posts", "post-index"],
                      "timeout_seconds": 10
                    }
```

## Caller usage

```python
from services.integrations.outbound_dispatcher import deliver

# After publishing a post
await deliver("vercel_isr",
              {"paths": [f"/posts/{slug}"], "tags": [f"post:{post_id}"]},
              db_service=db, site_config=site_config)
```

## Operator runbook

### First-time setup

1. Generate a shared secret (any random string). Store it on both sides:
   - Poindexter: `poindexter set-secret revalidate_secret '<paste>'`
   - Next.js `.env`: `REVALIDATE_SECRET=<same value>`
2. Ensure the Next.js app has a route at `/api/revalidate` that reads `x-revalidate-secret` and calls `revalidatePath()` / `revalidateTag()` accordingly.
3. Enable the row:
   ```sql
   UPDATE webhook_endpoints SET enabled = TRUE WHERE name = 'vercel_isr';
   ```
4. Test:
   ```python
   await deliver("vercel_isr", {"paths": ["/"]}, db_service=db, site_config=sc)
   ```

### Changing the site URL

```sql
UPDATE webhook_endpoints SET url = 'https://newdomain.example' WHERE name = 'vercel_isr';
```

### Rotating the secret

1. Update both sides: `poindexter set-secret revalidate_secret '<new>'` AND Next.js env.
2. Redeploy Next.js so it picks up the new env.

### Disabling

```sql
UPDATE webhook_endpoints SET enabled = FALSE WHERE name = 'vercel_isr';
```

The caller (typically the publish pipeline) should catch `OutboundWebhookError` and log without blocking the publish — the post still lives in the DB, just stale on the cached edge until next SSG/ISR window.

## Response contract

- 200 → success, returns `{"status_code": 200, "paths": [...], "tags": [...]}`
- Non-200 → `RuntimeError` with body (truncated)

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Sibling handlers: `outbound.discord_post`, `outbound.telegram_post`
- Legacy call site being migrated: `services.revalidation_service.trigger_nextjs_revalidation`
- GH-114 (cache_invalidation_backends — the eventual more-general table for SvelteKit/Nuxt/Cloudflare-purge destinations)
