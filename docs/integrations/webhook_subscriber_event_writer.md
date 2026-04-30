# Handler: `webhook.subscriber_event_writer`

Consumes Resend email event webhooks and inserts one row into `subscriber_events` per event.

## What it writes

| Column       | Source                                          |
| ------------ | ----------------------------------------------- |
| `email`      | `data.to` (string or first element of list)     |
| `event_type` | `type` (e.g. `email.delivered`, `email.opened`) |
| `event_data` | Full raw payload as JSONB                       |

## Supported events

`email.sent`, `email.delivered`, `email.opened`, `email.clicked`, `email.bounced`, `email.complained`. Unknown event types still persist for audit completeness.

## Row configuration

```
name:               resend  (or any operator-chosen slug)
direction:          inbound
handler_name:       subscriber_event_writer
signing_algorithm:  svix
secret_key_ref:     resend_webhook_secret
enabled:            true  (default false)
```

## Required app_settings

- `resend_webhook_secret` (is_secret=true, encrypted) — Svix-format signing secret from the Resend webhook dashboard.

## Operator runbook

### First-time setup

1. Log in to https://resend.com/webhooks → Add Endpoint.
2. URL: `https://<your-host>/api/webhooks/resend`
3. Subscribe to: `email.sent`, `email.delivered`, `email.opened`, `email.clicked`, `email.bounced`, `email.complained`.
4. Copy the signing secret (starts with `whsec_...`).
5. `poindexter set-secret resend_webhook_secret '<paste>'`
6. `UPDATE webhook_endpoints SET enabled = TRUE WHERE name = 'resend';`
7. Send a test email from Resend. Verify a row lands in `subscriber_events`.

### Signature details

Resend uses Svix-style HMAC signatures in the `Svix-Signature` header with format `v1,<hex>`. The dispatcher handles the `v1,` prefix and space-separated alternates automatically.

### Disabling

```sql
UPDATE webhook_endpoints SET enabled = FALSE WHERE name = 'resend';
```

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Target table: `subscriber_events`
- Sibling handler: `webhook.revenue_event_writer` (Lemon Squeezy)
