# Handler: `outbound.apprise_notify`

Generic notification delivery via [Apprise](https://github.com/caronc/apprise). One handler for every push destination — the channel is described entirely by the row's `config.apprise_url`, so adding Slack / ntfy / Pushover / email / SMS is a **row insert, not a new handler module**.

## Payload

```python
"plain string"               # delivered as-is
{"content": "message"}        # Discord-style
{"text": "message"}           # Telegram-style
{"body": "message"}           # also accepted ("message" too)
```

The handler reduces any of these to a single body string (first non-empty of `content` / `text` / `body` / `message`). The notification title is always empty, matching the plain-content posts the legacy Discord/Telegram handlers produced.

## The `apprise_url` template

`config.apprise_url` is an [Apprise URL](https://github.com/caronc/apprise/wiki) with optional placeholders, substituted at dispatch time:

- `{secret}` → the value resolved from `secret_key_ref` (an `app_settings` key, decrypted live each call)
- `{<config-key>}` → any other key in the row's `config` (e.g. `{chat_id}`)

The secret stays in `app_settings`; only the non-secret template lives on the row. Rotation works (the secret is resolved per call) and the token never lands in the `config` column.

## Seeded rows

| Row            | `secret_key_ref`          | `config.apprise_url`          | other config |
| -------------- | ------------------------- | ----------------------------- | ------------ |
| `discord_ops`  | `discord_ops_webhook_url` | `{secret}`                    | —            |
| `telegram_ops` | `telegram_bot_token`      | `tgram://{secret}/{chat_id}/` | `chat_id`    |

Apprise accepts the native Discord webhook URL (`https://discord.com/api/webhooks/<id>/<token>`) directly, so the Discord secret is passed straight through.

## Caller usage

```python
from services.integrations.outbound_dispatcher import deliver

await deliver("discord_ops", "Ops alert — pipeline stalled",
              db_service=db, site_config=site_config)
await deliver("telegram_ops", {"text": "Critical: worker offline"},
              db_service=db, site_config=site_config)
```

Most internal callers go through the operator-notify shim, which routes by urgency:

```python
from services.integrations.operator_notify import notify_operator

await notify_operator("routine note")                # -> discord_ops
await notify_operator("paging you", critical=True)   # -> telegram_ops
```

## Operator runbook

### Adding a new channel (no code)

Example: an ntfy push channel.

1. Store the secret (if the service needs one):
   `poindexter settings set ntfy_token '<token>' --secret`
2. Insert a row:
   ```sql
   INSERT INTO webhook_endpoints
     (name, direction, handler_name, secret_key_ref, enabled, config, metadata)
   VALUES (
     'ntfy_ops', 'outbound', 'apprise_notify', 'ntfy_token', TRUE,
     '{"apprise_url": "ntfy://{secret}@ntfy.sh/poindexter"}'::jsonb,
     jsonb_build_object('description', 'ntfy push channel')
   );
   ```
3. Call it: `await deliver("ntfy_ops", "hello from poindexter", db_service=db, site_config=sc)`

The [Apprise wiki](https://github.com/caronc/apprise/wiki) lists the URL format for each of the ~100 supported services. `config.apprise_url` is the only field that changes per service.

### Rotating a secret

```
poindexter settings set <secret_key_ref> '<new value>' --secret
```

Takes effect on the next call — the secret is resolved live each dispatch.

### Disabling

```sql
UPDATE webhook_endpoints SET enabled = FALSE WHERE name = 'discord_ops';
```

`deliver()` then raises `OutboundWebhookError`; `notify_operator` treats that as "operator turned this channel off" and moves on.

### Discord rendering note

If a Discord destination renders as an embed rather than plain content, append `?format=text` to its `apprise_url`:

```sql
UPDATE webhook_endpoints
   SET config = config || '{"apprise_url": "{secret}?format=text"}'::jsonb
 WHERE name = 'discord_ops';
```

## Response contract

- Success → the dispatcher returns `{"ok": true, "name": "<row>", "delivered": true}`
- Failure (malformed URL, delivery failure, missing `apprise_url`, unresolved `{secret}`, or unknown placeholder) → the handler raises; the dispatcher records `last_error` on the row and re-raises.

## Telegram streaming note

Only the fire-and-forget _notify_ path uses Apprise. The pipeline edit-in-place _streaming_ path (`services/pipeline_streaming.py`) still uses the Bot API helpers retained in `services/integrations/handlers/outbound_telegram.py` (`send_telegram_message` / `edit_telegram_message`) — Apprise cannot edit a message after sending.

## Related

- Framework overview: [Integrations](/docs/integrations/index)
- Sibling handler: `outbound.vercel_isr`
- Operator-notify shim: `services.integrations.operator_notify.notify_operator`
