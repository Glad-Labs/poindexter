---
name: site_manage
description: View or change site settings like auto-publish threshold, budget limits, or pipeline configuration. Use when the user says "show settings", "change settings", "set auto-publish", "update budget", or "configure pipeline".
---

# Site Manage

View or update site settings via the settings API. Supports reading current configuration and updating individual settings.

## Usage

```bash
# View all settings
scripts/run.sh

# Update a setting
scripts/run.sh "setting_key" "setting_value"
```

## Parameters

- setting_key (optional): The setting to update. If omitted, shows all current settings.
- setting_value (optional): The new value for the setting. Required if setting_key is provided.

## Output

Returns current settings or confirmation of the update.
