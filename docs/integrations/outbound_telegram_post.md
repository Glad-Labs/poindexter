# Handler: `outbound.telegram_post`

Sends a message via the Telegram Bot API `sendMessage` endpoint. Technically not a "webhook" destination (Telegram Bot API is its own protocol), but it fits the outbound-notification abstraction so it shares the `webhook_endpoints` table.

## Payload

```python
"plain string"              # sent as message text
{"text": "message"}          # equivalent
```

## Row configuration

```
name:               telegram_ops  (or any slug)
direction:          outbound
handler_name:       telegram_post
url:                https://api.telegram.org     (Bot API base, handler appends /bot<token>/sendMessage)
signing_algorithm:  bearer                       (the bot token is the bearer auth)
secret_key_ref:     telegram_bot_token           (app_settings key holding the encrypted token)
config:             {"chat_id": "<your chat id>"}
```

## Caller usage

```python
from services.integrations.outbound_dispatcher import deliver

await deliver("telegram_ops", "Critical alert: pipeline stopped",
              db_service=db, site_config=site_config)
```

## Operator runbook

### First-time setup

1. Chat with @BotFather on Telegram → `/newbot` → save the bot token.
2. Start a chat with your bot and send it any message (so Telegram registers you as a valid recipient).
3. Find your chat_id (visit `https://api.telegram.org/bot<TOKEN>/getUpdates`).
4. Store the token: `poindexter set-secret telegram_bot_token '<paste>'`
5. Seed or update the row:
   ```sql
   UPDATE webhook_endpoints
      SET config = jsonb_build_object('chat_id', '<your chat id>'),
          enabled = TRUE
    WHERE name = 'telegram_ops';
   ```
6. Test: `await deliver("telegram_ops", "hello from poindexter", ...)`

### Multiple chats

Add one row per chat:

```sql
INSERT INTO webhook_endpoints (name, direction, handler_name, url, signing_algorithm, secret_key_ref, enabled, config)
VALUES ('telegram_niche_announcements', 'outbound', 'telegram_post',
        'https://api.telegram.org', 'bearer', 'telegram_bot_token', TRUE,
        jsonb_build_object('chat_id', '-1001234567890'));
```

Same bot token, different `chat_id`.

### Disabling

```sql
UPDATE webhook_endpoints SET enabled = FALSE WHERE name = 'telegram_ops';
```

## Response contract

- 200 → success
- Non-200 → `RuntimeError` with the Telegram API error body (truncated to 200 chars)

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Sibling handlers: `outbound.discord_post`, `outbound.vercel_isr`
- Legacy call site being migrated: `services.task_executor._notify_telegram`
