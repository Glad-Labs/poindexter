# Handler: `webhook.alertmanager_dispatch`

Consumes Grafana Alertmanager webhook payloads. For every alert in the batch:

1. Inserts a row into `alert_events` (persistence).
2. Evaluates `_should_page_operator` (severity=critical OR category=infrastructure, and status=firing). If true, fans out to Discord + Telegram via `services.integrations.operator_notify.notify_operator` (the legacy `services.task_executor._notify_alert` helper was deleted with `task_executor.py` in Prefect Stage 4, 2026-05-16).
3. Looks up `plugin.remediation.<alertname>` in `app_settings` and logs the intended remediation action. This webhook-side hook records **intent only**; autonomous execution is delivered brain-side by the **firefighter** (rule-driven, keyed on the `remediation_rules` table) — see [Deterministic firefighter](/docs/operations/self-healing).

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
   poindexter settings set alertmanager_webhook_token '<paste>' --secret
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

For each firing alert, the handler fetches `plugin.remediation.<alertname>` from `app_settings`. If the row exists and has `"enabled": true`, the dispatcher logs the intended action. This webhook-side hook records **intent only** — a lightweight per-alert audit of what _would_ run.

Autonomous execution is now delivered separately, brain-side, by the **firefighter** (`brain/remediation/`): it matches each about-to-page alert against the `remediation_rules` table, runs an allowlisted action (e.g. `restart_container`, `run_auto_remediate`), holds the page, then verifies before it either resolves silently or escalates. See [Deterministic firefighter](/docs/operations/self-healing) for the loop, the action registry, safety guardrails, and rule authoring. The two surfaces are complementary: this hook is worker-side intent-logging keyed on `plugin.remediation.*`; the firefighter is the closed detect→act→verify→escalate loop keyed on `remediation_rules`.

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

## Dedup & enrichment (brain dispatcher)

Delivery of every `alert_events` row — webhook-sourced **and** findings-sourced (via `FindingsAlertRouterJob`) — is owned by the brain's `alert_dispatcher` poll loop, which dedups and optionally enriches before it pages:

- **Fingerprint precedence.** The dispatcher dedups on the row's own `alert_events.fingerprint` when the producer set one (`findings_alert_router` derives it from the finding's stable `dedup_key`; Alertmanager sends its own), folding in severity so an escalation still re-pages. Only when that column is empty does it fall back to hashing the rendered message body. Without this, a finding whose body carries per-fire detail — e.g. `topic_sanity_rejected` embedding each dropped article title — hashed to a fresh fingerprint on every fire and defeated dedup entirely (the Discord alert flood, 2026-07-04).
- **Triage enrichment only on dispatch.** With `ops_triage_enabled=true`, the firefighter follow-up (`/api/triage` → diagnosis reply) runs **only for rows the dispatcher actually sent this cycle**. Suppressed repeats are not re-triaged: they were already diagnosed on their first fire, and re-triaging a suppressed (parent-id-less) row pushed the duplicate diagnosis to Telegram through `send_followup`'s degraded path — so a warning that was correctly kept Discord-only leaked its diagnosis to the phone (the `topic_batch_stuck` "stuck Nh" Telegram flood, 2026-07-04).

Both behaviors live in `brain/alert_dispatcher.py` (`_evaluate_dedup_decision` and `poll_and_dispatch`).

## Related

- Framework overview: [Integrations](/docs/integrations/index)
- Target table: `alert_events`
- Dispatch helper: `services.integrations.operator_notify.notify_operator` (the bespoke `_notify_alert` shim in the handler — `integrations/handlers/webhook_alertmanager.py::_notify_operator` — wraps it with severity-aware critical-flag handling)
