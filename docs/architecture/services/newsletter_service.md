# Newsletter Service

**File:** `src/cofounder_agent/services/newsletter_service.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_newsletter_service.py`
**Last reviewed:** 2026-04-30

## What it does

`newsletter_service.send_post_newsletter()` sends a "we just
published a new article" email to every active, verified subscriber
in `newsletter_subscribers` whenever a blog post publishes. It
supports two providers, picked by `newsletter_provider`:

- **Resend** (default) — calls the `resend` SDK; free tier covers
  100/day, 3,000/month.
- **SMTP** — uses `aiosmtplib`; works against any SMTP server
  (self-hosted, Postmark, SendGrid, etc.).

Sends are batched (default 50 per batch with a 2-second delay
between batches) so the call respects provider rate limits. Every
attempt — success or failure — is logged to `campaign_email_logs`
for delivery tracking. The whole operation is fire-and-forget from
`publish_service`'s point of view; failures don't block the publish.

The HTML body is a minimal, inlined-CSS template with the post title,
excerpt, a CTA button, and a List-Unsubscribe link. Personalization
is limited to first-name greeting if the subscriber row has it.

## Public API

- `await send_post_newsletter(pool, title, excerpt, slug) -> dict` —
  the only public function. `pool` is an asyncpg connection pool
  (the caller in `publish_service` passes
  `db_service.cloud_pool or db_service.pool`). Returns
  `{"sent": int, "failed": int, "skipped": int,
"total_subscribers": int, "skipped_reason": str?}` — `skipped_reason`
  is set when the function exits early (`disabled`, `no_api_key`,
  `no_smtp_host`).

The internal helpers (`_cfg`, `_get_active_subscribers`,
`_build_html`, `_send_via_resend`, `_send_via_smtp`, `_log_send`)
are private.

## Key behaviors / invariants

- **Disabled by default.** `newsletter_enabled` defaults to `false`.
  Until an operator flips it, every call returns
  `{"skipped_reason": "disabled"}` immediately. No DB read of
  subscribers, no provider check.
- **Provider config is gated.** Resend without an API key, or SMTP
  without a host, returns early with the matching
  `skipped_reason` — no half-attempts that would partial-fail.
- **`smtp_password` and `resend_api_key` are read via `get_secret`
  vs `get`.** Per migration 0121, `smtp_password` is flagged
  `is_secret=true`, which means it's filtered out of the in-memory
  `SiteConfig` cache and MUST be fetched via the async
  `get_secret()` path. The current code uses `get_secret` for
  `smtp_password` but **plain `get` for `resend_api_key`** — see
  Status callout below.
- **Subscriber filter:** `unsubscribed_at IS NULL AND verified = TRUE`,
  ordered by `id` for determinism.
- **`site_url` is required.** `_site_url()` calls
  `site_config.require("site_url")`, which raises `RuntimeError` if
  unset. The HTML body would otherwise contain broken `/posts/...`
  links — the loud failure is intentional.
- **List-Unsubscribe header on SMTP only.** The SMTP path adds
  `List-Unsubscribe: <{site_url}/newsletter/unsubscribe>`. The
  Resend path does NOT — it relies on Resend's account-level
  unsubscribe handling.
- **Per-recipient send is sequential.** Inside each batch, sends are
  awaited one at a time. There's no concurrency within a batch — if
  you have 5,000 subscribers and a 2s SMTP latency, the whole job
  takes ~10,000s plus batch delays. The batch_delay sleep happens
  between batches, not within them.
- **Logging swallows DB errors.** `_log_send` catches and logs at
  DEBUG — a failed `campaign_email_logs` insert never aborts the
  send loop.

## Status: secret-flag inconsistency

`smtp_password` is fetched via `get_secret` (correct, post-migration
0121). `resend_api_key` is fetched via plain `get`, which means it
must NOT be flagged `is_secret=true` in `app_settings` or
`SiteConfig` will filter it out of the cache and the value will
silently be empty. Check the row's `is_secret` value before assuming
Resend will work:

```sql
SELECT key, value <> '' AS has_value, is_secret
FROM app_settings
WHERE key = 'resend_api_key';
```

If `is_secret=true`, either flip it to `false` or update
`newsletter_service._cfg()` to use `await site_config.get_secret(...)`
for the Resend key the same way it does for SMTP.

## Configuration

All from `app_settings` via `services.site_config`:

- `newsletter_enabled` (default `false`) — global on/off switch.
- `newsletter_provider` (default `"resend"`) — `"resend"` or
  `"smtp"`.
- `newsletter_from_email` (required when enabled) — sender address.
- `newsletter_from_name` (default empty) — display name for the
  From: header.
- `resend_api_key` (required when provider is Resend) — Resend API
  key. See Status callout — must NOT be `is_secret=true` today.
- `smtp_host` (required when provider is SMTP) — SMTP server host.
- `smtp_port` (default `587`) — SMTP port.
- `smtp_user` (default empty) — SMTP auth user, optional.
- `smtp_password` (default empty, **`is_secret=true` after 0121**)
  — SMTP auth password.
- `smtp_use_tls` (default `true`) — STARTTLS toggle.
- `newsletter_batch_size` (default `50`) — emails per batch.
- `newsletter_batch_delay_seconds` (default `2`) — sleep between
  batches.
- `site_url` (required, fail-loud) — used for `/posts/<slug>` and
  `/newsletter/unsubscribe` URLs.
- `company_name`, `site_name` — used in the HTML body header and
  footer text.

## Dependencies

- **Reads from:**
  - `newsletter_subscribers` — active+verified subscriber list.
  - `services.site_config.site_config` — all configuration.
- **Writes to:**
  - `campaign_email_logs` — one row per send attempt with
    `delivery_status` (`delivered` / `failed`) and optional
    `delivery_error`.
- **External APIs:**
  - **Resend**: `resend.Emails.send` (sync SDK, run in executor).
  - **SMTP**: `aiosmtplib.send` against the configured host.
- **Callers:**
  - `services.publish_service` (post-publish hook section "11f.
    Newsletter to subscribers"). Spawned via `_spawn_background` —
    fire-and-forget; failures logged at DEBUG and don't block the
    publish.

## Failure modes

- **Disabled** — early return `{"skipped_reason": "disabled"}`.
- **Resend chosen but API key missing/empty** — early return
  `{"skipped_reason": "no_api_key"}`.
- **SMTP chosen but host missing** — early return
  `{"skipped_reason": "no_smtp_host"}`.
- **`site_url` not configured** — `_site_url()` raises
  `RuntimeError`. The publish-service caller catches this in its
  outer try/except (`logger.debug("[NEWSLETTER] Failed (non-fatal):
%s", e)`) so the publish still succeeds, but no newsletter is
  sent.
- **Per-recipient send fails** — the provider helper
  (`_send_via_resend` / `_send_via_smtp`) catches the SDK exception,
  logs a warning, returns `False`. The outer loop counts it as
  `failed` and writes `delivery_status='failed'` to
  `campaign_email_logs`. The next subscriber is attempted normally.
- **Resend API rate-limit hit (429)** — caught the same way as any
  other Resend exception; logged and counted as `failed`. No
  back-off; subsequent calls in the same batch will likely also
  fail. Tune `newsletter_batch_size` / `newsletter_batch_delay_seconds`
  to stay under the documented Resend limits.
- **`resend` or `aiosmtplib` package missing** — `ImportError` at
  send-time inside `_send_via_*`, caught as the generic
  `Exception`, logged as warning. All sends will fail until the
  dependency is installed. Confirm `pyproject.toml` includes both.
- **No active subscribers** — early return after the SELECT, with
  `total_subscribers=0`. Not an error.

## Common ops

- **Enable newsletters:**
  ```bash
  poindexter set newsletter_enabled true
  poindexter set newsletter_from_email "newsletter@gladlabs.io"
  poindexter set newsletter_from_name "Glad Labs"
  poindexter set resend_api_key "re_..."  # check is_secret flag!
  ```
- **Switch to SMTP:**
  ```bash
  poindexter set newsletter_provider smtp
  poindexter set smtp_host smtp.example.com
  poindexter set smtp_port 587
  poindexter set smtp_user newsletter@gladlabs.io
  poindexter set smtp_password '<password>'  # is_secret=true after 0121
  ```
- **Tune batch size for a slower provider:**
  ```bash
  poindexter set newsletter_batch_size 20
  poindexter set newsletter_batch_delay_seconds 5
  ```
- **Inspect recent send results:**
  ```sql
  SELECT campaign_name, delivery_status, COUNT(*)
  FROM campaign_email_logs
  WHERE created_at >= NOW() - INTERVAL '7 days'
  GROUP BY 1, 2
  ORDER BY 1, 2;
  ```
- **Trigger manually for an existing post (one-off):**
  ```python
  from services.newsletter_service import send_post_newsletter
  result = await send_post_newsletter(
      pool, title="Title", excerpt="Excerpt", slug="some-slug",
  )
  print(result)
  ```
- **Find active subscriber count:**
  ```sql
  SELECT COUNT(*) FROM newsletter_subscribers
  WHERE unsubscribed_at IS NULL AND verified = TRUE;
  ```

## See also

- `docs/architecture/services/publish_service.md` — the caller that
  triggers newsletter sends.
- `services.migrations.0121_flip_smtp_password_secret` — the
  migration that gated the SMTP password rotation; the
  `_cfg()` change here was paired with that migration.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_silent_defaults.md`
  — why `_site_url()` raises rather than defaulting.
