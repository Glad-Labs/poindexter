# Handler: `publishing.mastodon`

POST a status to a Mastodon (or any compatible Fediverse) instance. One row per Mastodon account in the `publishing_adapters` table; the row's `enabled` flag is the master switch.

## Payload

```python
{"text": "Check out my new post!", "url": "https://example.com/post-slug"}
```

The handler builds the Mastodon-format text from these two fields:

- If `url` already appears in `text`, the text is sent as-is.
- Otherwise, `url` is appended on a new line.
- Status is hard-truncated to 500 characters (Mastodon's default toot limit; some instances allow more — we conservatively truncate so the post lands on any server).
- Visibility is hard-coded to `public`.

## Row configuration

```
name:           mastodon_main          # Stable slug; unique across all publishing rows
platform:       mastodon                # Filterable; matches the handler family
handler_name:   mastodon                # Resolves to `publishing.mastodon` in the registry
credentials_ref: mastodon_              # Prefix for the app_settings keys below
enabled:        false                   # Default disabled; needs creds + the `enable` step
config:         '{}'                    # Reserved for future per-row tuning
```

## Required app_settings

```sql
INSERT INTO app_settings (key, value, is_secret) VALUES
  ('mastodon_instance_url',  'https://mastodon.social',         false),
  ('mastodon_access_token',  '<paste from instance Settings>',   true);
```

The instance URL is plain config (`is_secret=false`); the access token is encrypted at rest. Generate the token at `<your_instance>/settings/applications` with the `write:statuses` scope.

## Caller usage

Same as `publishing.bluesky` — the row-driven dispatcher in `services/social_poster.py:_distribute_to_adapters` walks every enabled `publishing_adapters` row and calls each handler.

```python
from services.social_poster import generate_and_distribute_social_posts

await generate_and_distribute_social_posts(
    title="Why local LLMs win",
    slug="why-local-llms-win",
    excerpt="A deep dive into cost, latency, and privacy",
    keywords=["LLM", "Ollama", "self-hosting"],
)
```

## Operator runbook

### Enabling Mastodon for the first time

```bash
# 1. Pick an instance + create an application at <instance>/settings/applications.
#    Required scope: write:statuses
# 2. Register the credentials.
poindexter settings set mastodon_instance_url 'https://mastodon.social'
poindexter settings set --secret mastodon_access_token '<token>'

# 3. The mastodon_main row is seeded disabled by default.
poindexter publishers enable mastodon_main

# 4. Smoke test.
poindexter publishers fire mastodon_main
```

### Adding a second Mastodon account (multi-instance)

```sql
INSERT INTO publishing_adapters
    (name, platform, handler_name, credentials_ref, enabled, config, metadata)
VALUES (
    'mastodon_fosstodon',
    'mastodon',
    'mastodon',
    'mastodon_fosstodon_',
    false,
    '{}',
    jsonb_build_object('description', 'fosstodon.org account for FOSS-leaning posts')
);
```

Then add `mastodon_fosstodon_instance_url` + `mastodon_fosstodon_access_token` rows. Each row picks up its own creds via `<credentials_ref>instance_url` / `<credentials_ref>access_token`.

### Rotating the access token

1. Revoke the old application at the instance settings.
2. Generate a new application + token.
3. `poindexter publishers set-secret mastodon_main access_token '<new-token>'`.

### Disabling

```bash
poindexter publishers disable mastodon_main
```

## Failure modes

| Symptom                                                             | Cause                                   | Fix                                                                  |
| ------------------------------------------------------------------- | --------------------------------------- | -------------------------------------------------------------------- |
| `[MASTODON] Skipped — site_config not provided to mastodon adapter` | Caller forgot the `site_config=` kwarg  | Bug; check the dispatch path in `_distribute_to_adapters`            |
| `mastodon_instance_url or mastodon_access_token not configured`     | Credentials missing in `app_settings`   | Run the `settings set` commands above                                |
| `Mastodon.py package is not installed`                              | Container missing the `Mastodon.py` SDK | Add `Mastodon.py` to `pyproject.toml` deps and rebuild               |
| Token rejected with 401                                             | Token expired / scope insufficient      | Regenerate at `<instance>/settings/applications` w/ `write:statuses` |

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Sibling handler: `publishing.bluesky`
- Issue: `Glad-Labs/poindexter#112`
- CLI: `poindexter publishers --help`
