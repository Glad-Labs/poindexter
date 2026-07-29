# title: DB-backed configuration
# category: config
# description: A page of app_settings keys out of the full count, showing config lives in the database rather than env files. Use for beats about configuration or tunability.

Type "poindexter settings list --limit 6"
Sleep 400ms
Enter
Wait+Screen@45s /Settings:/
Sleep 2500ms
