---
name: Matt's Preferences and Constraints
last_updated: 2026-04-01T07:00:00Z
updated_by: claude-code
category: feedback
---

# Preferences Both Systems Must Follow

## Work Style

- Don't ask "what's next" — keep working autonomously through the backlog
- Never idle — work 24/7 through backlog, content gen, testing, improvements
- All background tasks MUST be invisible — no console popups (Matt works at the PC)

## Code & Data

- Never use mock/dummy/fabricated data — only real values or leave empty
- Calculated > generated — LLMs only where creativity/judgment required
- Every feature needs an anticipation pattern for idle resource optimization
- Every metric must be in Grafana — auto-add dashboards for new data

## Cost Control (NON-NEGOTIABLE)

- $300 Gemini incident in the past — cost overages are a real worry
- Default to local Ollama for routine work (cheaper than cloud, but not free)
- Cloud models only as fallback, never primary
- Track ALL costs: GPU electricity, API, hosting, hardware depreciation
- Nothing is free — local GPU = electricity cost. Track it.

## Deployment

- Branch workflow: dev -> staging -> main, never push to main directly (bypassed for urgent fixes)
- Never `railway up` from root — deploy via GitHub push to main
- Minimize env vars — store config in DB, manage via OpenClaw

## Infrastructure

- Gemini removed from pipeline (April 2026) — use Ollama everywhere
- Ollama preferred provider for all LLM work
- Self-sufficiency north star: solar-powered, fully autonomous revenue
