---
name: Session 49 handoff — April 2 2026
description: Autonomous pipeline loop, DB-first config, MCP servers, permissions, topic discovery, SDXL images
type: project
---

# Session 49 Handoff — April 2, 2026

## Summary

29 commits across 8 hours. Built the autonomous content pipeline loop, parameterized the entire codebase for forkability, and hardened the system.

## Key Achievements

### Autonomous Pipeline Loop

- Topic Discovery: scrapes HN, Dev.to, DuckDuckGo for trending topics
- Web Research: free DuckDuckGo search replaces Serper ($0 API cost)
- Content Generation: Ollama qwen3.5:35b with web research context
- Multi-Model QA: programmatic validator + Ollama cross-review
- SDXL Images: 6 category-specific styles + Cloudinary CDN upload
- Auto-Publish: approve = publish (fixed stuck-tasks bug)
- Idle Worker: quality audits, link checks, topic gaps, threshold tuning, topic discovery

### DB-First Configuration (117 settings)

- site_config.py: global DB-backed config, only DATABASE_URL as env var
- 27 prompt templates in DB (YAML fallback)
- 14 toggleable pipeline stages with A/B testing
- 48 agent permissions across 7 agents
- 9 alert auto-triage patterns
- Emergency-only cloud API mode (daily limits + Telegram alerts)

### Infrastructure

- 8 MCP servers: gladlabs (19 tools), forgejo (103), grafana, railway, docker, ollama, Gmail, Calendar
- 12 Grafana dashboards (fixed datasources, time filters, queries)
- Grafana Cloud alerts killed, API keys cleared
- 44 Gitea issues (7 closed, 10 created)

### Content

- 140 published posts (was 67 at session start)
- 6 categories: Technology(110), Business(11), Startup(5), Engineering(4), Security(3), Insights(2)
- 70 broken external URLs removed, 29 broken internal links removed

## Numbers

- Posts: 140 published
- Settings: 117 keys
- Prompts: 27 in DB
- Stages: 14 pipeline
- Permissions: 48 rules
- Alerts: 9 patterns
- Embeddings: 1,037
- MCP: 8 servers
- Tests: 5,569 passing
- Cloud cost: $0.04 electricity

## Blockers

- Social posting: needs X/LinkedIn API keys
- Newsletter: needs Resend domain verification (DNS)
- E2E tests: 4 flaky (author + tag pages)
- Woodpecker: untested end-to-end
- Gitea password: in git history, needs rotation

## Design Principles

1. DB-first config — app_settings is the control plane
2. Data-driven tuning — no auto-modification of code/prompts
3. Alert auto-triage — resolve before escalating
4. Agent permissions — no self-modification of prompts
5. Visual verification — check from user's perspective
6. Prebuilt MCP servers where available
7. Pipeline as product — forkable, plug-and-play
