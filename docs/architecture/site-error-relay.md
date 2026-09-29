# Site error relay

How errors from a public website (a Next.js site on Vercel, say) reach a
self-hosted, LAN-only GlitchTip, and why the path is a pull rather than a
push.

```
browser ──────────┐
                  ├─ Sentry SDK `tunnel` ─▶ sentry-relay Worker ─▶ D1 queue
serverless fn ────┘                         (edge checks)            │
                                                                     │
DrainSentryRelayJob ◀──────── GET /pending (bearer) ─────────────────┘
(worker, every 2 min) ──▶ GlitchTip /api/<id>/envelope/?sentry_key=<key>  (LAN)
                      ──▶ POST /ack (bearer)
GlitchTip issue ──▶ brain GlitchTip triage probe ──▶ Discord
```

- Worker: `infrastructure/cloudflare/sentry-relay` (queue, edge checks,
  operator setup).
- Drain: `services/sentry_relay.py` + `services/jobs/drain_sentry_relay.py`.
- Paging: `poindexter/brain/glitchtip_triage_probe.py`, with a per-project
  threshold (`glitchtip_triage_alert_threshold_overrides`).

## Why it exists

Until 2026-09-28 the operator's public site called `Sentry.captureException`
from its error boundaries and its "fail loud" route handlers, told visitors
"we've been notified", and listed Sentry in its privacy policy. Nothing was
reported anywhere. Five separate breaks stood between a thrown error and a
person, and each one alone would have been enough:

1. **No DSN in the production build.** `withSentryConfig` ran only when a DSN
   variable was set, and none was, so the browser bundle held only the
   tree-shaken `captureException` stubs: no `init`, no transport.
2. **No instrumentation hook.** Sentry v8+ initializes on the server only
   from `instrumentation.ts` `register()`. The site had none, so its
   `sentry.server.config.ts` never loaded, whatever the DSN.
3. **The relay was never deployed.** The first version of this Worker
   forwarded envelopes to GlitchTip through a public tunnel that did not
   exist.
4. **GlitchTip would have refused the forward.** It reads the DSN key only
   from `?sentry_key=` or `X-Sentry-Auth`. A tunnelled envelope carries its
   DSN only in the envelope header, so a raw forward is `403 Denied`.
5. **CSP would have blocked the browser.** `connect-src` had no entry for any
   error endpoint.

GlitchTip had one project (the worker's own), so no consumer existed either.
Each piece looked configured from its own side. Only following one event from
a browser to a person showed the gap.

## Why pull, not push

The tracker is LAN-only and the worker has no public ingress. That is
deliberate: the newsletter signup capture, the Resend delivery receipts, the
Lemon Squeezy invoice poll and the unsubscribe relay all poll outward instead
of accepting inbound connections. Forwarding from the edge would mean
publishing GlitchTip's ingest on a public tunnel. Anyone who found that
hostname (tunnel hostnames are in certificate-transparency logs) could post
straight to it, bypassing the Worker's checks, and a tunnel path table shared
with other apps is exactly the dependency that broke the newsletter signup.

A browser or a serverless function does need something public to post to, so
the Worker is that public surface. It holds the envelopes, and the worker
collects them.

## Invariants

Each of these was earned by something that failed quietly.

- **The drain adds the key.** `sentry_key` goes on the ingest URL from the
  column the Worker stored. Without it every envelope is refused.
- **Ack only after GlitchTip answered, and only for a settled envelope.** A
  2xx is forwarded. A 4xx other than 429 is a verdict about that envelope
  (wrong key, unknown project), so it is acked and reported
  (`sentry_relay_envelope_rejected`), since retrying cannot change it. A 429,
  a 5xx or no answer is an outage: the envelope stays queued, the pass stops,
  and `sentry_relay_drain_failed` fires. A crash between forward and ack
  re-delivers, and GlitchTip dedupes the repeated `event_id`.
- **Errors only.** The SDK sends no traces, replays, sessions or client
  reports, and the Worker answers any envelope without an error-type item
  `200` without storing it. The queue has a write budget, and session pings
  arrive at page-view rate.
- **Nothing expires silently.** `/pending` prunes rows past `RETENTION_DAYS`
  and returns the count. The drain reports it as
  `sentry_relay_envelopes_expired`.
- **The DSN key is dashless hex.** `@sentry/core`'s DSN regex accepts only
  word characters in the key. GlitchTip shows keys as dashed UUIDs, and a DSN
  built from that form is rejected at runtime with a console-only
  `Invalid Sentry Dsn`. The site's build fails on such a DSN. GlitchTip itself
  accepts either form as `sentry_key`.
- **The CSP entry comes from the tunnel variable.** `connect-src` is derived
  from `NEXT_PUBLIC_SENTRY_TUNNEL`, the value the SDK posts to, so the two
  cannot drift apart. This is the page-view beacon's 2026-06 outage in another
  form.
- **DSN and tunnel are set together or not at all.** A DSN without the tunnel
  would post to an endpoint the relay does not serve. The production build
  fails on a half-configured pair.
- **Performance signals are not errors.** The site's poor-Core-Web-Vitals
  reporter used to `captureMessage("Web Vital degraded: LCP=4523ms")`, which
  would have opened one issue, and one page, per distinct number. Vitals go
  to Google Analytics, where they are read. That `import('@sentry/nextjs')`
  had also defeated tree-shaking; removing it took tracing and replay code out
  of every page (+18 KB gzip for the working SDK instead of +68 KB).

## Paging

The brain's triage probe lists issues org-wide and pages Discord when an issue
crosses its threshold. One number cannot fit every project: the worker's own
project repeats known transients hundreds of times, while a public site sees
one real error and then nothing. `glitchtip_triage_alert_threshold_overrides`
(`{"<project slug>": <count>}`) sets a per-project threshold; the site's
project pages on its first event. A malformed map falls back to the global
threshold, so an override can only move a project's threshold, never silence
it.

## Failure modes

| Symptom                          | Where it shows         | Usual cause                                               |
| -------------------------------- | ---------------------- | --------------------------------------------------------- |
| `sentry_relay_drain_failed`      | Discord (3 h cooldown) | Relay unreachable, GlitchTip down, bearer missing         |
| `sentry_relay_envelope_rejected` | Discord (6 h cooldown) | DSN key or project id no longer matches GlitchTip         |
| `sentry_relay_envelopes_expired` | Discord (daily)        | Drain down longer than `RETENTION_DAYS`                   |
| Relay answers `503` to the site  | Browser network tab    | Queue full (drain stopped) or secret unset                |
| Nothing at all                   | —                      | Site built without the env pair, or CSP blocks the tunnel |

A site that reports nothing because it has no errors looks the same as the
last row. The job's `job_run` metrics (`pulled`, `forwarded`, `backlog`) show
whether anything is flowing.

## Cost

- **Cloudflare**: D1's free tier allows 100k row writes and 5M row reads a
  day. A healthy site writes a handful of rows a day, and the drain's poll
  (720 a day) reads a few. The queue caps at `MAX_QUEUE_ROWS`. Workers KV was
  rejected: its free tier allows 1,000 writes and 1,000 lists a day, shared
  with the unsubscribe relay's queue, and a 2-minute poll alone would use
  most of the list budget.
- **Page weight**: the browser SDK without tracing, replay or sessions adds
  about 18 KB gzip to each page's JavaScript.

## Verification (2026-09-28)

- Worker, with its queue SQL on real SQLite (a `node:sqlite` D1 double; `workerd`
  cannot run on the self-hosted CI runner): 35 vitest cases, mutation-checked.
- Edge, live: foreign origin `403`, unauthenticated read `401`, unlisted
  project `403`, a session-only envelope accepted but not stored, an error
  envelope queued with the CORS header.
- Drain, run inside the production worker container against the deployed
  Worker: forwarded to GlitchTip (`200`), acked, and the issue appeared in the
  site's project.
- Server leg: the site's production build, run locally with the same env
  pair. A malformed body sent to the revalidate route reached its
  `captureException`, the Node SDK tunnelled the envelope to the Worker, and
  the issue landed in GlitchTip (`SyntaxError`, transaction
  `POST /api/revalidate`).
- Browser leg: the same build's client carried the tunnel, the dashless DSN
  and no session, tracing or replay integrations, and posted to the tunnel
  (refused, as designed, for a localhost origin).
