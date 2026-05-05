# OAuth tokens for Grafana alert webhooks

Grafana fires alerts at the worker via Alertmanager-shaped webhooks
(`POST /api/webhooks/alertmanager`). This guide covers the OAuth-token
path — option B from [Glad-Labs/poindexter#247][issue].

[issue]: https://github.com/Glad-Labs/poindexter/issues/247

## Why a pre-issued long-TTL JWT, not Grafana's OAuth contact-point flow

Grafana's contact-point UI exposes a `oauth2` block on newer versions,
but in practice it's brittle:

- The IdP fields drift across Grafana minor versions (the YAML keys in
  10.x don't match the UI labels in 11.x).
- Grafana's contact-point HTTP client has no way to refresh through a
  client_credentials grant — it expects an authorization-code style
  redirect URI, which a headless alerting system can't satisfy.
- The IdP-side error messages surface as opaque "401 Unauthorized" in
  the contact-point test page, with no log on the Grafana side.

**Pre-issued long-TTL JWTs side-step all of that.** The operator mints
one JWT per quarter (90-day default), pastes it into the contact-point
"Authorization Header" field, and rotates manually. The worker accepts
both OAuth JWTs and the legacy static `alertmanager_webhook_token`
during the migration window (Glad-Labs/poindexter#247).

## Provision the token

```bash
poindexter auth mint-grafana-token --ttl 90d
```

This:

1. **First call**: registers a new OAuth client named `grafana-alerts`
   in `oauth_clients`, persists encrypted credentials to
   `app_settings.grafana_oauth_client_id` and
   `app_settings.grafana_oauth_client_secret`.
2. **Every call**: mints a fresh JWT bound to that client with the
   default scopes `api:read api:write` and the requested TTL.

Output looks like:

```
Grafana OAuth client provisioned + token minted.
  client_id:   pdx_a1b2c3d4e5f6...
  scopes:      api:read api:write
  ttl:         90d (7776000s)
  expires_at:  1785000000 (epoch — 2026-08-01 12:00 UTC)
  jti:         3f8b9c1d...

Token (paste into Grafana contact-point Authorization Header):

eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJwb2luZGV4dGVyIiwic3Vi...
```

**Capture the JWT NOW** — it's not stored anywhere, and re-running the
command mints a different JWT (the old one keeps verifying until its
own `exp` elapses).

## Paste into Grafana

> Grafana's UI labels move every few releases. The path below is for
> Grafana 11.x. If your Grafana looks different, search for "contact
> points" → your contact point → custom HTTP headers.

1. Open Grafana → **Alerting** → **Contact points**.
2. Click the contact point that points at the worker webhook
   (default name: `poindexter-worker`).
3. Edit → **Optional Webhook settings** → **Custom HTTP headers**.
4. Click **+ Add header**.
5. Header name: `Authorization`
6. Header value: `Bearer <paste the JWT here>`
   - The literal word `Bearer`, then a single space, then the JWT.
7. **Save contact point**.
8. **Test** the contact point (button at top of the edit page). The
   worker should respond `200 OK` with a JSON body counting persisted
   alerts. If you get `401 Unauthorized`, double-check there's a single
   space between `Bearer` and the token and no trailing whitespace.

We deliberately don't push the token via the Grafana API — the
contact-point JSON contains the bearer in plaintext, and the API push
ergonomics (token in argv, token in shell history, token in CI logs)
aren't worth the time saved.

## Verify end-to-end

After saving the contact point:

```bash
# Watch the worker accept the next test webhook.
docker logs -f poindexter-cofounder 2>&1 | grep -i "alertmanager webhook"

# In Grafana, hit the "Test" button on the contact point.
# Expected log line: "alertmanager webhook: received=1 persisted=1 paged=0 remediated=0"
```

If the test fires but the worker logs `OAuth JWT rejected: token expired`,
re-run the mint command — your previous token's `exp` already elapsed
(unusual on a 90-day TTL, but possible if you provisioned a 60-min
token by accident).

## Rotation

Set a calendar reminder for ~80 days after each mint (the default 90-day
TTL minus 10 days of buffer). When it fires:

```bash
poindexter auth mint-grafana-token --ttl 90d
```

Paste the new JWT into the same Grafana contact-point header, save.
The previous JWT keeps verifying until its own `exp` elapses, so
there's no traffic gap.

To invalidate ALL JWTs for the Grafana client immediately (e.g. after a
suspected leak), revoke the underlying OAuth client:

```bash
# Get the client_id.
poindexter auth list-clients | grep grafana-alerts

# Revoke. Outstanding JWTs continue verifying until their exp elapses
# — JWT verification is stateless and we trade revocation latency for
# scalability per the #241 design decision.
poindexter auth revoke-client --client-id pdx_<the_id>

# Clear the cached client_id so the next mint provisions a new one.
poindexter set grafana_oauth_client_id ""
poindexter set grafana_oauth_client_secret ""

# Mint a fresh token bound to the new client.
poindexter auth mint-grafana-token --ttl 90d
```

The Grafana contact-point header continues using the old JWT until the
operator pastes the new one in — same procedure as a normal rotation.

## Troubleshooting

| Symptom from Grafana                  | Likely cause                                                                                                                       |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `401 Unauthorized` on test            | Token typo, missing `Bearer ` prefix, the token's `exp` elapsed, or the underlying OAuth client was revoked. Re-mint and re-paste. |
| `503 Service Unavailable`             | `app_settings.alertmanager_webhook_token` is empty. Run `poindexter set alertmanager_webhook_token "<value>"`.                     |
| Test succeeds, real alerts never fire | Check the Grafana alert rule's `Notifications` block — the contact point must be selected.                                         |

The middleware no longer accepts static-Bearer tokens — the dual-auth
window closed in Glad-Labs/poindexter#249. Anything in the
contact-point header that isn't a JWT minted by this command will 401.

## See also

- [`docs/operations/secret-rotation.md`](secret-rotation) — full
  secret-rotation runbook (the Grafana JWT row is one of many).
- [Glad-Labs/poindexter#241](https://github.com/Glad-Labs/poindexter/issues/241) —
  OAuth 2.1 migration umbrella, full design rationale.
- [Glad-Labs/poindexter#247](https://github.com/Glad-Labs/poindexter/issues/247) —
  the issue this work closes.
