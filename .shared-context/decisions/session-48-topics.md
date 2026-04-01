---
name: Session 48 Complete Topic Index
last_updated: 2026-04-01T19:57:00Z
updated_by: claude-code
category: decision
---

# Session 48 — April 1, 2026 (15+ hour marathon)

## Decisions Made

1. **Gemini removed from pipeline** — all LLM work through local Ollama ($0)
2. **Split database architecture** — cloud (public site data) + local (brain/ops/embeddings)
3. **pgvector as shared brain** — all LLMs search the same semantic memory
4. **Tailscale for remote access** — phone access to local Grafana, DB, services
5. **Local Grafana replaces Grafana Cloud** — dashboards on your hardware
6. **Gitea + Woodpecker replaces GitHub** — when ready, not before launch
7. **Revenue infrastructure stays cloud** — Vercel + Railway earn their costs
8. **Ops infrastructure all local, free, open source** — $0/month operations
9. **Lean, fast, free** — AI code assistants + open source = one person at team scale
10. **Brain is the business, sites are products** — portable, sellable, swappable

## Infrastructure Built

- Docker pgvector (port 5433) + local Grafana (port 3000) — running
- Dual DB connections in codebase (backward compatible)
- Embedding pipeline: Ollama nomic-embed-text → pgvector HNSW
- 61 memory files embedded with semantic recall
- RAG wired into content pipeline (similar post context)
- Sync service (local ↔ cloud, push posts, pull metrics)
- DB backup scripts + daily 2 AM scheduled task
- Gitea + Woodpecker in docker-compose (ready to start)
- Shared context system (Claude Code + OpenClaw)
- Discord voice bot (Whisper STT → Ollama → Sherpa TTS)

## Hardening Done

- 2 SQL injection fixes (cms_routes, admin_db)
- 16 swallowed exceptions surfaced across 8 service files
- 2 critical reliability bugs (stale task sweep, Railway probe false alerts)
- Full SEO audit (canonical URLs, og:tags, keywords, JSON-LD — all fixed)
- Rate limiting, API docs disabled, revalidate hardened, error page sanitized
- Dependency security audit (pyjwt, pypdf, urllib3, requests, werkzeug upgraded)
- X-Powered-By removed, ALLOWED_ORIGINS set, rate limit bumped to 1000/min

## Testing

- 5484 unit tests passing (0 failures, was 19 failing at start)
- 26 approval workflow integration tests rewritten
- 16 content pipeline e2e tests with real Ollama
- Load test script ready (scripts/load-test.py)
- All 11 flaky tests fixed (OPERATOR_ID import-order root cause)

## CI/CD

- Telegram notifications on deploy success/failure
- Existing GitHub Actions pipeline validated
- Gitea + Woodpecker ready for GitHub replacement

## Observability

- Request ID correlation in structlog
- Local Grafana dashboards (brain ops + system health)
- Pipeline audit log planned
- "Metric vomit" — log every decision, state change, data point

## Architecture Vision

- PUBLIC: Vercel (site) + Railway (slim DB) — revenue infrastructure
- PRIVATE: Local Postgres + pgvector, Ollama, Grafana, Gitea, Woodpecker, OpenClaw — all free, all open source
- FUTURE: Linux migration, solar power, every house is a datacenter
- MODELS: gemma3:27b (QA), qwen3.5:35b (content), glm-4.7 (general), nomic-embed-text (embeddings)

## Open Items

- Data migration from Railway → local DB
- Tailscale setup
- Voice bot Discord token conflict (needs separate bot app)
- Grafana dashboard import
- Pipeline audit log table
- Staging environment for pre-production testing
- Dead code cleanup in brain_daemon.py
- Signal handling for graceful daemon shutdown
