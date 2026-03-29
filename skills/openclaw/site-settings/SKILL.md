---
name: site-settings
description: View or update pipeline settings stored in the database. Use when the user says "show settings", "set the budget to", "change the API key", "update config", or similar.
---

# Site Settings

View and manage app_settings stored in the database. These control API keys, pipeline config, feature flags, CORS, and webhooks — replacing environment variables.

## Usage

```bash
scripts/run.sh list [category]          # List all settings (or filter by category)
scripts/run.sh get <key>                # Get a specific setting
scripts/run.sh set <key> <value>        # Set a setting value
```

## Categories

- `api_keys` — LLM provider keys, Pexels, Serper, Sentry
- `pipeline` — auto_publish_threshold, daily_budget_usd, default_model_tier
- `auth` — api_token, secret_key, jwt_secret_key
- `features` — enable_training_capture, enable_mcp_server, etc.
- `cors` — allowed_origins, rate_limit_per_minute
- `webhooks` — openclaw_webhook_url, openclaw_webhook_token

## Examples

- "Show me the pipeline settings"
- "Set the daily budget to 3 dollars"
- "What's the Anthropic API key set to?"
- "Update the allowed origins to include staging"
