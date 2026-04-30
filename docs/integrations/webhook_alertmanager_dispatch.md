# Handler: `webhook.alertmanager_dispatch`

Consumes Grafana Alertmanager webhook payloads. For every alert in the batch:

1. Inserts a row into `alert_events` (persistence).
2. Evaluates `_should_page_operator` (severity=critical OR category=infrastructure, and status=firing). If true, fans out to Discord + Telegram via `services.task_executor._notify_alert`.
3. Looks up `plugin.remediation.<alertname>` in `app_settings` and logs the intended remediation action (concrete handlers land in follow-up work).

Replaces the bespoke route in `routes/alertmanager_webhook_routes.py`.

## Row configuration

```
name:               alertmanager  (or any operator-chosen slug)
direction:          inbound
handler_name:       alertmanager_dispatch
signing_algorithm:  bearer
secret_key_ref:     alertmanager_webhook_token
enabled:            true  (default false)
```

## Required app_settings

- `alertmanager_webhook_token` (is_secret=true, encrypted) — the bearer token Alertmanager will send in the `Authorization: Bearer <token>` header.
- `discord_ops_webhook_url` — Discord webhook for the #ops channel (notifications go here unconditionally for critical alerts).
- `telegram_bot_token` (is_secret=true) and `telegram_chat_id` — for Telegram notifications on critical alerts.
- `telegram_alerts_enabled` (default false) — optional; set to `true` to fan out non-critical alerts to Telegram as well.
- `plugin.remediation.<alertname>` — optional per-alert remediation specs (JSON with `{enabled, action, params}`).

## Operator runbook

### First-time setup

1. In Alertmanager routing config, add a webhook receiver:
   ```yaml
   receivers:
     - name: poindexter-webhook
       webhook_configs:
         - url: https://<your-host>/api/webhooks/alertmanager
           http_config:
             bearer_token: <choose a random token>
   ```
2. Generate and store the token:
   ```
   poindexter set-secret alertmanager_webhook_token '<paste>'
   ```
3. Enable the row:
   ```sql
   UPDATE webhook_endpoints SET enabled = TRUE WHERE name = 'alertmanager';
   ```
4. Fire a test alert (or wait for the next real one). Verify a row lands in `alert_events`.

### Severity-based paging behavior

- `severity=critical` (firing only) → Discord + Telegram
- `category=infrastructure` (firing only) → Discord + Telegram
- Everything else firing → Discord only (ignored by Telegram unless `telegram_alerts_enabled=true`)
- Resolved alerts → never paged (but still persisted)

### Remediation hook

For each firing alert, the handler fetches `plugin.remediation.<alertname>` from `app_settings`. If the row exists and has `"enabled": true`, the dispatcher logs the intended action. Concrete remediation execution is intentionally deferred — the scaffold records intent so operators can audit what _would_ run before enabling autonomous remediation.

Example row:

```sql
INSERT INTO app_settings (key, value, category, description, is_secret)
VALUES (
  'plugin.remediation.HighGPUMemory',
  '{"enabled": false, "action": "restart_ollama", "params": {"grace_seconds": 30}}',
  'remediation',
  'Restart Ollama if GPU memory pressure alert fires (currently dry-run only)',
  FALSE
);
```

### Disabling

```sql
UPDATE webhook_endpoints SET enabled = FALSE WHERE name = 'alertmanager';
```

The legacy `/api/webhooks/alertmanager` route in `routes/alertmanager_webhook_routes.py` also continues to serve requests during the migration window.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Target table: `alert_events`
- Dispatch helper: `services.task_executor._notify_alert`
