# Handler: `webhook.revenue_event_writer`

Consumes Lemon Squeezy webhook payloads and inserts one row into `revenue_events` per event.

## What it writes

| Column           | Source                                                           |
| ---------------- | ---------------------------------------------------------------- |
| `event_type`     | `meta.event_name` (e.g. `order_created`, `subscription_updated`) |
| `source`         | Always `'lemon_squeezy'`                                         |
| `amount_usd`     | `data.attributes.total` / 100. Refunds stored as negative.       |
| `currency`       | Always `'USD'` (Lemon Squeezy operates in USD only)              |
| `recurring`      | `TRUE` for subscription\_\* events                               |
| `customer_email` | `data.attributes.user_email`                                     |
| `customer_id`    | `data.id`                                                        |
| `external_id`    | `meta.webhook_id` (fallback: `data.id`)                          |
| `external_data`  | Full raw payload as JSONB                                        |

## Supported events

`order_created`, `order_refunded`, `subscription_created`, `subscription_updated`, `subscription_cancelled`. Unknown event types still persist with `amount_usd=0` for audit completeness.

## Row configuration

```
name:               lemon_squeezy  (or any operator-chosen slug)
direction:          inbound
handler_name:       revenue_event_writer
signing_algorithm:  hmac-sha256
secret_key_ref:     lemon_squeezy_webhook_secret  (app_settings key)
enabled:            true  (default false — flip when ready)
```

## Required app_settings

- `lemon_squeezy_webhook_secret` (is_secret=true, encrypted) — the signing secret from the Lemon Squeezy dashboard.

## Operator runbook

### First-time setup

1. Log in to Lemon Squeezy → Store → Settings → Webhooks → Add endpoint.
2. URL: `https://<your-host>/api/webhooks/lemon_squeezy`
3. Events to subscribe: `order_created`, `order_refunded`, `subscription_created`, `subscription_updated`, `subscription_cancelled`.
4. Copy the signing secret Lemon Squeezy generates.
5. Store it: `poindexter set-secret lemon_squeezy_webhook_secret '<paste>'`
6. Enable the row: `UPDATE webhook_endpoints SET enabled = TRUE WHERE name = 'lemon_squeezy';` (or `poindexter webhooks enable lemon_squeezy` once the CLI ships).
7. Trigger a test order in Lemon Squeezy (free test mode works). Verify a row lands in `revenue_events`.

### Rotation

1. Generate a new secret in the Lemon Squeezy dashboard.
2. `poindexter set-secret lemon_squeezy_webhook_secret '<new value>'`
3. Lemon Squeezy will retry failed calls with the new secret automatically.

### Test-fire without a real order

```bash
# Paste your current secret:
SECRET=$(poindexter get-secret lemon_squeezy_webhook_secret)
BODY='{"meta":{"event_name":"order_created","webhook_id":"test-1"},"data":{"id":"999","attributes":{"total":2900,"user_email":"test@example.com"}}}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
curl -X POST https://<your-host>/api/webhooks/lemon_squeezy \
     -H "X-Signature: $SIG" \
     -H "Content-Type: application/json" \
     -d "$BODY"
```

Expected response: `{"ok": true, "name": "lemon_squeezy", "event": "order_created", "amount_usd": 29.00, "recurring": false}`.

### Disabling

```sql
UPDATE webhook_endpoints SET enabled = FALSE WHERE name = 'lemon_squeezy';
```

Takes effect on the next request (no restart).

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Target table: `revenue_events`
- Sibling handler: `webhook.subscriber_event_writer` (Resend email events)
