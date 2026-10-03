# Newsletter signup capture

**Code:** `services/newsletter_audience.py`, `services/newsletter_signup_canary.py`,
`services/jobs/sync_newsletter_audience.py`, `services/jobs/probe_newsletter_signup.py`,
`web/public-site/app/api/newsletter/subscribe/route.ts`,
`web/public-site/app/legal/privacy/page.tsx` (section 3.4)
**CLI:** `poindexter newsletter sync [--dry-run]`, `poindexter newsletter canary [--url]`
**Last reviewed:** 2026-09-28

A newsletter signup starts in a visitor's browser on the public site and has to
end as a row in `newsletter_subscribers`, because that table is what
`newsletter_service.send_post_newsletter` mails on every publish. The site is
served from Vercel. The worker is local-first and has **no public ingress**, so
the site cannot write that table. This page is how the signup gets there anyway,
and how we know it still does.

```text
browser ──POST──▶ site route /api/newsletter/subscribe
                      │  Resend POST /contacts   (segment = RESEND_AUDIENCE_ID)
                      ▼
                 Resend segment  ◀──GET /segments/{id}/contacts── SyncNewsletterAudienceJob
                                                                   (worker, outbound, every 15 min)
                                                                          │
                                                                          ▼
                                                           newsletter_subscribers
                                                           (tokens, opt-outs, sends)

ProbeNewsletterSignupJob (daily): POST a Resend test inbox through the site route,
check with the worker's key that it reached the segment, delete it.
```

It is the same split as the Resend delivery poll (`services/resend_delivery.py`)
and the Lemon Squeezy invoice poll: **when a provider looks like it needs an
inbound hook, check whether its API already knows.** Resend's contacts API
already holds every signup the site captures, so the worker reads it outbound.

## Why the site no longer writes to the worker

The route used to write the signup to two stores: the worker's
`POST /api/newsletter/subscribe` through a Tailscale Funnel hostname, and a
Resend audience. It returned success if either accepted. On 2026-09-28 both
stores were checked:

| Evidence                                          | Finding                                                                                                                                                          |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The funnel hostname in the site's build config    | Belonged to a node that was retired. It no longer resolves in public DNS, so every public signup failed the worker leg at the DNS lookup                         |
| The host's current funnel                         | Routes only the MCP and OAuth paths to the worker. `POST /api/newsletter/subscribe` answers 404 there too                                                        |
| `newsletter_subscribers`                          | One row: the operator's own end-to-end test from 2026-06-03                                                                                                      |
| The Resend segment                                | Empty. The account's only contact was an internal test address in no segment                                                                                     |
| Resend's retained email list (2026-08-30 → 09-25) | 15 emails, all newsletter sends to the operator, and **no welcome email**. The route sends one only after a store accepts the signup, so none did in that window |
| Where the route's failures went                   | `console.error` (Vercel keeps runtime logs about a day) and Sentry. The error tracker has never received an event from the public site                           |

So nothing recoverable was missing, and nothing noticed the path was down. There
was nothing to backfill. Whether visitors tried and got the route's 503 is
unknowable from what is retained.

This was the path's **second** silent outage. Before 2026-06-03 the route stored
signups nowhere: a send-only Resend key, an unset audience, and the failure
swallowed, while the visitor still got a welcome email.

Re-exposing the worker route on the funnel was considered and rejected. It
would add public ingress to a local-first worker, and it would rebuild the exact
dependency that broke: a node name, a funnel path table other apps also claim
(the funnel root now serves an unrelated app), and a Vercel env value that has
to track both. The worker also restarts routinely, and a signup that arrives
mid-deploy would fail. Resend is up whenever the site is.

## Resend facts the code relies on

Verified against the live API on 2026-09-28 with transient `@resend.dev` test
contacts, created and then deleted, with no email sent:

- **`POST /contacts` is an upsert.** A duplicate returns 201 with the same id.
  `"unsubscribed": false` in the body clears a prior opt-out, so a returning
  subscriber re-consents by signing up again.
- `"segments": [{"id": …}]` on create puts the contact in the segment. Adding it
  again is idempotent.
- `GET /segments/{id}/contacts` keeps unsubscribed contacts in the listing, with
  `"unsubscribed": true`. That makes a Resend-side opt-out visible to the pull.
- The legacy `/audiences/{id}/contacts` endpoints still work but are deprecated.
  **An audience id is the segment id**: the account's one audience is listed
  under both names. The setting keeps its old name, `resend_audience_id`.

## Opt-out precedence

This is the invariant that matters most, because getting it backwards mails
someone who asked to leave.

| Owned row        | Resend contact | The pull does                                                                  | Counted as             |
| ---------------- | -------------- | ------------------------------------------------------------------------------ | ---------------------- |
| none             | active         | insert: new unsubscribe token, verified, `subscribed_at` = Resend `created_at` | `imported`             |
| active           | active         | nothing                                                                        | `already_subscribed`   |
| **unsubscribed** | active         | **nothing: the owned opt-out wins**                                            | `opted_out_kept`       |
| active           | unsubscribed   | set `unsubscribed_at`, reason `resend_contact_unsubscribed`                    | `unsubscribes_applied` |
| unsubscribed     | unsubscribed   | nothing (the original timestamp and reason stay)                               | `already_unsubscribed` |
| none             | unsubscribed   | nothing                                                                        | `skipped_unsubscribed` |

Matching is case-insensitive. The table's UNIQUE constraint is on the raw
column, so `Foo@x.com` and `foo@x.com` would otherwise become two subscribers.

**The deliberate cost:** someone who unsubscribes through the relay and later
signs up again on the site is _not_ re-activated by the pull. A Resend contact
carries no timestamp that tells "signed up again" apart from "never left", so
the pull cannot tell a re-subscribe from a stale contact. Missing a re-subscribe
is the safe failure. The `opted_out_kept` counter makes it visible. A
re-activation by hand is `poindexter newsletter sync --dry-run` to confirm,
then clearing `unsubscribed_at` for that row.

**Resend does not learn about opt-outs made here.** An unsubscribe through the
relay or `POST /api/newsletter/unsubscribe` updates `newsletter_subscribers`
only, and the Resend contact stays active. That is safe today, because nothing
sends from the Resend side: the newsletter is per-recipient sends from the
worker's own list, which honours the opt-out. **Do not send a Resend Broadcast
to this segment** until owned opt-outs are mirrored to Resend. It would mail
people who unsubscribed.

## The canary

A low-traffic newsletter makes "nobody signed up" and "the form is broken" look
identical from the owned table. `ProbeNewsletterSignupJob` tells them apart by
running the real path once a day:

1. POST `{email, first_name: "Canary"}` to `newsletter_signup_canary_url`,
   exactly as the form does.
2. With the **worker's** Resend key, check that the contact is in the segment
   named by `resend_audience_id`. A 200 alone is not proof. A site whose
   `RESEND_AUDIENCE_ID` or `RESEND_API_KEY` points at a different segment or
   Resend team also answers 200, and the worker would never see its signups.
3. Delete the contact, always, including after a failure.

A failure raises `newsletter_signup_capture_broken` (Discord, daily cooldown).
The address is one of Resend's own test inboxes
(`delivered+signup-canary@resend.dev`). It costs one send against the quota per
run and does not affect domain reputation. Two consumers skip it on purpose:

- **The pull**, in case a failed cleanup leaves the canary in the segment.
- **The delivery poll.** The site sends the canary a welcome email every day.
  Were those recorded in `subscriber_events`, the table would stay fresh on the
  canary alone, and the webhook-freshness probe that watches it would read a
  dead newsletter as healthy.

The canary is **off by default** (`newsletter_signup_canary_url` empty), because
switching it on means a daily welcome email. Run it once without switching it on
with `poindexter newsletter canary --url https://<site>/api/newsletter/subscribe`.

## Configuration

| Where          | Key                                                                           | Purpose                                                                                         |
| -------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `app_settings` | `resend_audience_id`                                                          | The segment signups land in and the pull reads. Empty on OSS; no-op until set                   |
| `app_settings` | `resend_api_key` (secret)                                                     | Worker's Resend key: contacts read/write, delete (canary cleanup)                               |
| `app_settings` | `newsletter_audience_sync_max_pages`                                          | Page cap for the pull (100 contacts per page). Hitting it is reported, never silently truncated |
| `app_settings` | `newsletter_signup_canary_url`                                                | The site's signup endpoint. Empty = canary off                                                  |
| `app_settings` | `newsletter_signup_canary_email`                                              | The canary's address (a Resend test inbox)                                                      |
| `app_settings` | `newsletter_signup_canary_attempts`, `newsletter_signup_canary_retry_seconds` | In-run retries before the canary reports the endpoint broken                                    |
| Vercel env     | `RESEND_API_KEY`                                                              | Site's key. Needs contact write access, not the send-only kind                                  |
| Vercel env     | `RESEND_AUDIENCE_ID`                                                          | **Must equal** `resend_audience_id`. The canary checks exactly this                             |

The site keeps only email and first/last name, and the signup form asks for
nothing more. Until 2026-09-28 it also asked for company, interests and a
marketing-consent tick, which nothing downstream read. Its small print also
claimed the visitor's IP address and user-agent were stored with the
subscription, which nothing did. Submitting the form is the consent to the
newsletter, so there is no separate tick. Add a form field only together with
the code that stores and reads it.

The worker's `POST /api/newsletter/subscribe` now stores what the pull stores:
the address, first and last name, the verified flag and an unsubscribe token.
Until 2026-09-28 it also stored `company`, `interest_categories` and
`marketing_consent` from direct API callers, plus the caller's IP address and
user-agent. The last two described a proxy hop or a server-side fetch, not the
subscriber. Nothing read any of the five, and migration `20260928_184647`
dropped their columns (Glad-Labs/poindexter#1109). A caller that still sends
the three request fields gets its signup, plus a `Deprecation: true` header
that names what was ignored. See the
[API reference](../api/index.mdx#newsletter-signup-payload).

## Operating it

- **See what the pull would do:** `poindexter newsletter sync --dry-run`.
- **Pull now** (backfill, or after fixing config): `poindexter newsletter sync`.
  It exits non-zero on any error.
- **Check the path end to end:** `poindexter newsletter canary`. It exits
  non-zero when the signup is not captured and names the cause.
- **Grafana:** the Integrations & Admin board's _Newsletter Distribution_ row
  shows the last canary result and the subscribers the pull imported (both read
  the jobs' `job_run` rows in `audit_log`).
- **Findings:** `newsletter_audience_sync_failed` means the pull failed.
  Signups are still safe in Resend, but they are not being mailed until it
  clears. `newsletter_signup_capture_broken` means the public form is not
  capturing, so every real signup is failing the same way.

## The public preview page was retired

`web/public-site/app/preview/[token]` rendered a draft by fetching the worker's
`/api/posts/preview/{token}` from Vercel. That hit the same wall, so it has
returned 404 for every token since the funnel stopped fronting the worker.
Nothing linked to it. Approval notifications build preview links from
`preview_base_url`, and the operator console links the worker's own
server-rendered `/preview/{token}` relative to itself.

Re-wiring it would mean publishing unpublished drafts to the internet behind a
bearer token, which cuts against the approval gates deciding what leaves the
machine. The worker's `/preview/{token}` over the tailnet remains the preview
surface.

## What the privacy policy says about this path

The public privacy policy (`web/public-site/app/legal/privacy/page.tsx`: section
3.4, plus entries in sections 2, 4, 5, 9, 10 and 11 and in the FAQ) describes
this path to visitors. It said nothing about the newsletter or Resend until
2026-09-28 (stack#4216). Each sentence in it is a claim about what the code
does, so **a change to what this path stores, sends or keeps is also a change
to the policy.** The claims, and what each one rests on:

| The policy says                                                                                                           | It holds because                                                                                                                                                | Re-check                                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| The form asks for an email and an optional first and last name. No IP address or browser details are stored with it       | The modal has three fields. The route sends Resend only those. `newsletter_subscribers` has no IP or user-agent column (dropped by migration `20260928_184647`) | `e2e/newsletter-modal.spec.ts`, `NewsletterModal.test.js`, `__tests__/api/newsletter-subscribe.test.ts`          |
| The site sends the details to Resend. Our own system copies them into our list, and the newsletter is sent from that list | The route's `POST /contacts`. `SyncNewsletterAudienceJob`. `_get_active_subscribers` reads `newsletter_subscribers`                                             | `tests/unit/services/test_newsletter_audience.py`                                                                |
| Each new post is emailed to every subscriber, with an unsubscribe link at the bottom. A first name is used to greet       | `send_post_newsletter`, fired from every go-live seam. `_build_html`                                                                                            | `tests/unit/services/test_newsletter_service.py`                                                                 |
| A send log is kept, and Resend's delivery results are recorded against the address                                        | `campaign_email_logs` (written per send). `subscriber_events` (`RECORDED_EVENTS` in `resend_delivery.py`)                                                       | `tests/unit/services/test_resend_delivery.py`                                                                    |
| **We do not track opens or clicks**                                                                                       | Resend's `open_tracking` and `click_tracking` are **off for the sending domain** (a Resend dashboard setting, not code), and nothing here writes either         | `GET /domains` on the Resend API. Both read `false` on 2026-09-28. **Turn either on and this sentence is false** |
| The unsubscribe page is a Cloudflare Worker. It holds the token and the time, not the email, until the worker applies it  | `infrastructure/cloudflare/unsubscribe-relay` stores `{requested_at, via}` under the token. `ApplyUnsubscribeRequestsJob` drains it every 5 minutes             | `tests/unit/services/test_unsubscribe_relay.py`, the relay's own `npm test`                                      |
| Unsubscribing deletes nothing: the row is kept, marked unsubscribed, and the Resend contact is left alone                 | The drain only sets `unsubscribed_at`. Owned opt-outs are not mirrored to Resend (see [Opt-out precedence](#opt-out-precedence))                                | `tests/unit/services/test_unsubscribe_relay.py`                                                                  |
| Nothing deletes subscriber rows, send logs or delivery events on a schedule                                               | No `retention_policies` row covers `newsletter_subscribers`, `campaign_email_logs` or `subscriber_events` (checked 2026-09-28), and no code deletes from them   | `poindexter retention list`                                                                                      |

`web/public-site/app/legal/__tests__/pages.test.js` pins that the policy still
has the newsletter section, that the signup modal's link lands on it, and that
every provider host the signup route calls appears in the processors table. It
reads the route's source, so a provider added to the route fails that test
until the policy names it.

### Erasing a subscriber

There is no command for this yet, and the order matters, because the pull
treats the Resend contact as the source of new subscribers:

1. **Delete the Resend contact first** (`DELETE /contacts/{email}` accepts the
   address in the path). Delete only the row here and the next pull finds an
   active contact with no owned row and **imports it again as a new
   subscriber**. That is also why an unsubscribed row is kept rather than
   deleted.
2. Delete the `newsletter_subscribers` row. `campaign_email_logs` rows go with
   it (`ON DELETE CASCADE`).
3. Delete the `subscriber_events` rows for the address. That table is keyed by
   email and has no foreign key, so nothing removes them for you.

Two places this does not reach. Resend keeps its own logs of the emails it
sent. And the database sits inside the backups, so the row survives in every
snapshot taken before the deletion: hourly (24 kept), daily (7), and the
offsite restic repository (7 daily, 4 weekly, 6 monthly on this install, so up
to about six months). The privacy policy does not mention backups yet.
