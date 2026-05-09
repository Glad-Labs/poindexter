# Handler: `publishing.bluesky`

POST a status to a Bluesky account via the AT Protocol. One row per Bluesky account in the `publishing_adapters` table; the row's `enabled` flag is the master switch.

## Payload

```python
{"text": "Check out my new post!", "url": "https://example.com/post-slug"}
```

The handler builds the Bluesky-format text from these two fields:

- If `url` already appears in `text`, the text is sent as-is.
- Otherwise, `url` is appended on a new line.
- Status is hard-truncated to 300 graphemes (Bluesky's published limit) with an ellipsis.

## Row configuration

```
name:           bluesky_main         # Stable slug; unique across all publishing rows
platform:       bluesky               # Filterable; matches the handler family
handler_name:   bluesky               # Resolves to `publishing.bluesky` in the registry
credentials_ref: bluesky_             # Prefix for the app_settings keys below
enabled:        true                  # Master switch
config:         '{}'                  # Reserved for future per-row tuning
```

## Required app_settings

```sql
INSERT INTO app_settings (key, value, is_secret) VALUES
  ('bluesky_identifier',   'gladlabs.bsky.social',   true),
  ('bluesky_app_password', '<paste from bsky.app>',  true);
```

Both rows are `is_secret=true` (encrypted at rest via pgcrypto). The `bluesky_app_password` is an **app password** generated at `bsky.app/settings/app-passwords` — never the account password.

## Caller usage

The handler is registered under the `publishing` surface and dispatched by `services/social_poster.py:_distribute_to_adapters` (post-`poindexter#112`). Callers of `social_poster` don't address the handler directly; they pass posts + an enabled set, and the row-driven dispatcher walks every enabled `publishing_adapters` row.

```python
from services.social_poster import generate_and_distribute_social_posts

await generate_and_distribute_social_posts(
    title="Why local LLMs win",
    slug="why-local-llms-win",
    excerpt="A deep dive into cost, latency, and privacy",
    keywords=["LLM", "Ollama", "self-hosting"],
)
```

The orchestrator generates the platform-appropriate copy, looks up enabled `publishing_adapters` rows, and dispatches each via `services/integrations/registry.dispatch("publishing", row.handler_name, ...)`.

## Operator runbook

### Enabling Bluesky for the first time

```bash
# 1. Register the credentials.
poindexter settings set --secret bluesky_identifier 'gladlabs.bsky.social'
poindexter settings set --secret bluesky_app_password 'xxxx-xxxx-xxxx-xxxx'

# 2. Verify the seed row exists (the publishing_adapters migration ships it disabled-or-enabled).
poindexter publishers list

# 3. Enable.
poindexter publishers enable bluesky_main

# 4. Smoke test.
poindexter publishers fire bluesky_main
```

### Adding a second Bluesky account

```sql
INSERT INTO publishing_adapters
    (name, platform, handler_name, credentials_ref, enabled, config, metadata)
VALUES (
    'bluesky_secondary',
    'bluesky',
    'bluesky',
    'bluesky_secondary_',
    false,
    '{}',
    jsonb_build_object('description', 'Secondary handle for cross-promotion')
);
```

Then add `bluesky_secondary_identifier` + `bluesky_secondary_app_password` rows to `app_settings`. The handler reads `<credentials_ref>identifier` and `<credentials_ref>app_password` so each row picks up its own secrets.

### Rotating the app password

1. Revoke the old password at `bsky.app/settings/app-passwords`.
2. Generate a new one.
3. `poindexter publishers set-secret bluesky_main app_password '<new-token>'`.

The next post picks up the new password (secrets aren't cached).

### Disabling

```bash
poindexter publishers disable bluesky_main
```

The next call to `_distribute_to_adapters` skips this row silently. No error is logged — disabled is a normal state.

## Failure modes

| Symptom                                                           | Cause                                      | Fix                                                                 |
| ----------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------- |
| `[BLUESKY] Skipped — site_config not provided to bluesky adapter` | Caller forgot the `site_config=` kwarg     | Bug; check the dispatch path in `_distribute_to_adapters`           |
| `bluesky_identifier or bluesky_app_password not configured`       | Credentials missing in `app_settings`      | Run the `set-secret` commands above                                 |
| `atproto package not installed`                                   | Container missing the `atproto` SDK        | Add `atproto` to `pyproject.toml` deps and rebuild                  |
| AT Protocol returns 4xx/5xx                                       | Bluesky API error (auth, rate limit, etc.) | Inspect `last_error` on the row; `last_run_status` will be `failed` |

## Why publishing is its own table (not webhook_endpoints)

`publishing_adapters` was added in `Glad-Labs/poindexter#112` as the fourth corner of the declarative-data-plane (`external_taps` + `retention_policies` + `webhook_endpoints` + `publishing_adapters`). Webhooks deliver opaque blobs to URLs; publishing has richer per-row config: `default_tags`, `rate_limit_per_day`, `credentials_ref` namespacing. Mixing the two would have meant either bloating `webhook_endpoints` with publishing-only columns or leaking publishing semantics into the webhook handler API. Two tables, same handler-registry pattern.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md` (post-implementation status appendix shows what landed)
- Sibling handler: `publishing.mastodon`
- Issue: `Glad-Labs/poindexter#112`
- CLI: `poindexter publishers --help` (5 subcommands: list / show / enable / disable / set-secret / fire)
