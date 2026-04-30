# Handler: `outbound.discord_post`

POST to a Discord webhook URL. One row per destination webhook (you can seed multiple rows for different Discord channels).

## Payload

```python
# Any of:
"plain string"                          # wrapped as {"content": "..."}
{"content": "message"}                   # minimum
{"content": "x", "embeds": [...]}        # Discord embeds supported
```

## Row configuration

```
name:               discord_ops  (or any slug)
direction:          outbound
handler_name:       discord_post
url:                https://discord.com/api/webhooks/<id>/<token>
signing_algorithm:  none  (Discord webhooks use the URL itself as auth)
secret_key_ref:     (leave NULL)
```

## Caller usage

```python
from services.integrations.outbound_dispatcher import deliver

await deliver("discord_ops", "Ops alert — pipeline stalled",
              db_service=db, site_config=site_config)

# Or with embeds
await deliver("discord_ops",
              {"content": "Release pushed",
               "embeds": [{"title": "poindexter v1.4.0", "color": 0x00FF00}]},
              db_service=db, site_config=site_config)
```

## Operator runbook

### Adding a new Discord destination

1. Create a new webhook in Discord (Server Settings → Integrations → Webhooks → New Webhook).
2. Copy the webhook URL.
3. Insert a row:
   ```sql
   INSERT INTO webhook_endpoints
     (name, direction, handler_name, url, signing_algorithm, enabled, metadata)
   VALUES (
     'discord_releases',
     'outbound',
     'discord_post',
     'https://discord.com/api/webhooks/.../...',
     'none',
     TRUE,
     jsonb_build_object('description', 'Release announcements channel')
   );
   ```
4. Call it: `await deliver("discord_releases", "Released!", db_service=db, site_config=sc)`.

### Rotating a webhook

Regenerate the webhook URL in Discord, then:

```sql
UPDATE webhook_endpoints SET url = '<new URL>' WHERE name = 'discord_ops';
```

### Disabling

```sql
UPDATE webhook_endpoints SET enabled = FALSE WHERE name = 'discord_ops';
```

The call to `deliver()` will raise `OutboundWebhookError`; callers should catch and fall back silently (as the current `_notify_discord` does).

## Response contract

- 200 or 204 → success (Discord returns 204 No Content for webhook posts)
- Any other status → `RuntimeError` with the response body (truncated to 200 chars)

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Sibling handlers: `outbound.telegram_post`, `outbound.vercel_isr`
- Legacy call site being migrated: `services.task_executor._notify_discord`
