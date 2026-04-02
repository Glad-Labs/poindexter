---
name: System Capabilities & Delegation
last_updated: 2026-04-02T17:52:00Z
updated_by: claude-code
category: decisions
---

# System Capabilities & Delegation

## Who Does What

### Claude Code (CLI)

- Code changes, refactoring, bug fixes
- Database schema changes and migrations
- Git operations (commit, push, merge)
- MCP server management
- Infrastructure configuration
- Test execution and CI/CD

### OpenClaw

- Voice interface (Discord STT → process → TTS)
- Telegram channel monitoring and auto-responses
- Periodic content audits (schedule-driven)
- Social media posting (when API keys available)
- Proactive notifications to Matt
- Real-time system monitoring via Discord

### Brain Daemon (autonomous)

- Health monitoring every 5 minutes
- Telegram alerts on failures
- Auto-create Gitea issues on 3x probe failure
- Service URL monitoring
- Config loaded from app_settings DB at startup

### Idle Worker (autonomous)

- Quality audits on published posts (every 6h)
- Broken link checks (every 12h)
- Topic gap analysis (every 24h)
- Threshold tuning suggestions (every 12h)
- Embedding freshness checks (every 4h)
- Only runs when no content tasks are active

### Pipeline (autonomous)

- 14 stages, all toggleable via pipeline_stages table
- A/B testing via pipeline_experiments table
- Every stage logged to pipeline_run_log
- Prompts loaded from prompt_templates table
- Config loaded from app_settings

## How To Coordinate

1. Read `.shared-context/state/system-status.md` for current state
2. Read `.shared-context/identity/system-identity.md` for brand config
3. Query app_settings for runtime config
4. Query pipeline_stages for pipeline state
5. Query content_tasks for task queue
6. Write to brain_knowledge for observations
7. Write to brain_queue for action requests

## Key Principle

The database is the single source of truth. All coordination happens through
the database. No direct service-to-service communication. Change a value in
app_settings → every service picks it up on next read.
