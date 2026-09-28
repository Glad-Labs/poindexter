# Newsletter signup capture

**Code:** `services/newsletter_audience.py`, `services/newsletter_signup_canary.py`,
`services/jobs/sync_newsletter_audience.py`, `services/jobs/probe_newsletter_signup.py`,
`web/public-site/app/api/newsletter/subscribe/route.ts`
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

The worker's `POST /api/newsletter/subscribe` still accepts `company`,
`interest_categories` and `marketing_consent` from direct API callers and
stores them in `newsletter_subscribers`. Nothing reads them there either.

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
