---
name: Session 48 handoff — April 1 2026
description: HN launch hardening, Gemini removal, cost tracking fixes, voice bot, architecture discussion
type: project
---

## What was built (April 1, 2026 — overnight session)

### Cost Tracking Fixes (3 gaps closed)

- cost_guard.py: Gemini pricing updated from $0/$0 to actual Flash rates
- ai_content_generator.py: Added missing cost logging to \_try_gemini_user_selected
- multi_model_qa.py: QA review costs now written to cost_logs table (was console-only)
- content_router_service.py: Logs QA review costs to database

### QA Review Switched to Local Ollama

- Primary reviewer: gemma3:27b (free, local)
- Gemini Flash as fallback only
- Weight updated from 0.3 to 0.6 in score aggregation

### Training Data Upsert Fix

- content_db.py: INSERT changed to ON CONFLICT DO UPDATE for orchestrator_training_data
- Fixes UniqueViolationError on retried tasks (70 of 13 recent failures)

### Gemini Completely Removed from Pipeline

- GOOGLE_API_KEY disabled in .env.local
- provider_checker.py: Ollama is now #1 priority (was Gemini)
- model_consolidation_service.py: Google removed from fallback chain
- All LLM work routes through Ollama ($0 cost)

### HN Launch Hardening — SEO (CRITICAL)

- Fixed canonical URLs on ALL 72 blog posts (was pointing to glad-labs.com instead of www.gladlabs.io)
- Fixed missing /posts/ path prefix in canonical URLs
- Fixed keywords meta tag character-split bug (Next.js needs array, not string)
- Added og:image, og:url, canonical to homepage
- Fixed JSON-LD structured data wrong domain (glad-labs.com → www.gladlabs.io)
- Fixed 404/error page email/link references
- Cleaned up 14 corrupted seo_keywords entries in database

### HN Launch Hardening — Security

- Rate limited GDPR data-requests endpoint (5/min)
- Disabled API docs in production (/api/docs, /api/redoc return 404)
- Removed X-Powered-By: Next.js header
- Revalidate endpoint fails-secure when REVALIDATE_SECRET is unset
- Error page sanitized — no longer shows raw error.message

### HN Launch Hardening — Performance

- Homepage ISR revalidation increased from 5min to 15min
- Related posts fetch parallelized (eliminated waterfall)
- RATE_LIMIT_PER_MINUTE set to 1000 on Railway
- ALLOWED_ORIGINS set on Railway

### Discord Voice Bot (Full Pipeline)

- Rewrote discord_voice_bot.py with py-cord for voice recording
- Full flow: Voice → Whisper STT (CUDA) → Ollama → Sherpa TTS → Voice
- Commands: ?join, ?listen, ?stop, ?ask, ?say, ?status, ?leave
- Tested: Sherpa TTS works (0.52x RTF), Whisper transcribes perfectly
- Issue: OpenClaw gateway and voice bot fight for same Discord bot token
- Needs: separate Discord bot application or integrate voice into OpenClaw

### Thinking Model Token Budget Fix

- Thinking models (qwen3.5, glm-4.7) return empty responses with low token limits
- QA review: detects thinking models and increases max_tokens to 1500
- Voice bot: num_predict increased to 1000

### Dependency Security Audit

- Upgraded: pyjwt, pypdf, python-multipart, requests, urllib3, werkzeug
- Frontend (npm): 0 vulnerabilities
- Remaining: transitive deps from langchain/embedchain (can't upgrade without breaking compat)

### Test Fixes

- Cleaned up stale worktree directories (was causing vitest duplicate test files)
- Fixed 8 test failures in test_content_agent_tools.py (Pydantic mock incompatibility)
- Fixed writing_style_routes tests (were passing intermittently)
- Provider checker and model consolidation tests need updating for Gemini removal

### Architecture Discussion

- Evaluated OpenClaw (framework) vs Claude Code for autonomous business ops
- Matt leaning toward Claude Code + MCP + Ollama hybrid
- OpenClaw for autonomous 24/7 workflows, Claude Code for interactive dev
- OpenClaw already has 16 skills, Telegram/Discord/WhatsApp channels, voice plugins
- Decision pending — may migrate cortex business logic into OpenClaw skills

## Issues Closed This Session

- #1465 Brain service health probes (implemented + merged)

## Commits (10+ on dev, deployed to main)

- `7eb3fe15` fix: cost tracking gaps + QA moved to local Ollama + training data upsert
- `0da20a4c` fix: HN launch hardening — SEO, security, performance
- `20409ca1` fix: keywords meta tag returns array for proper Next.js rendering
- `b1aff4f3` security: harden revalidate endpoint + sanitize error messages
- `72d673d3` fix: remove Gemini from provider chain — Ollama-first everywhere
- `bfa9b6da` feat: full voice conversation — STT → Ollama → TTS in Discord
- `7d3d4d64` fix: patch Pydantic tool mocks at class level instead of instance
- `b14cc4b2` fix: handle thinking models (qwen3.5, glm-4.7) token budget

## System State

- Worker: running locally with Ollama
- Daemon: running (pythonw)
- OpenClaw: gateway running on port 18789
- Ollama: 12 models, gemma3:27b for QA, qwen3.5:35b/glm-4.7 for content
- Site: deployed to production (Vercel + Railway)
- Railway: RATE_LIMIT_PER_MINUTE=1000, ALLOWED_ORIGINS set

## Pending / Next Session

- HN LAUNCH: Thursday April 2nd at 10am ET
- Voice bot: resolve Discord token conflict (need separate bot app or OpenClaw integration)
- Test failures: approval_workflow tests need rewrite (auth model changed), provider/consolidation tests need Gemini references removed
- Content pipeline: verify end-to-end with Ollama (thinking model empty response risk)
- Twitter/X: @\_gladlabs handle — need to set up and start posting
- Architecture migration: start moving cortex business logic into OpenClaw skills
