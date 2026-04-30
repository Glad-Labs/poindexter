---
name: Decision log
description: Running log of Matt's key decisions with reasoning — preserves the WHY behind choices
type: project
---

# Decision Log

Format: `[DATE] DECISION — WHY`

[2026-03-30] Multi-model QA uses weighted scoring (programmatic 40%, cloud 60%) — Cloud models catch nuance but hallucinate; programmatic rules are deterministic but miss style issues. Weighted blend gets both.

[2026-03-30] Auto-publish threshold set to 80/100 — Below 80 is held for manual review. This balances autonomy (no human bottleneck) with quality (don't publish garbage). Can be adjusted via app_settings.

[2026-03-30] Content generator creates 3 posts per 8-hour cycle — Conservative to start. Can increase once quality gate proves reliable.

[2026-03-31] All affiliate/monetization data lives in DB only, never in source code — Fabricated referral codes leaked into production. Source code has zero tracking params. Only real, verified values go in the affiliate_links table.

[2026-03-31] No mock/dummy/placeholder data anywhere in production code — If a real value isn't available, leave it empty. Fake data propagates silently and breaks trust.

[2026-03-31] Long-term: custom fine-tuned models per role (writer, QA, analyst) — Weekly LoRA training on 5090. Short-term: aggressive memory files + session handoffs for session continuity.

[2026-03-31] Self-sufficiency is the north star — Solar-powered home server, fully autonomous revenue, zero ongoing human intervention. Every decision should trend toward full autonomy.

[2026-03-31] RAG reframed as per-client tone matching for multi-site phase — Not needed for single-site. Custom fine-tuning (#1462) handles Glad Labs' voice. RAG becomes the per-client tone loader when scaling to multiple sites. "We own the tone of all our clients" = proprietary IP moat.

[2026-03-31] Quality scoring was miscalibrated, not the model — glm-4.7-5090 scores 9.61/10 on raw quality shootout but pipeline gave it 71-76. Fixed accuracy baseline (60→70), readability curve (compressed for technical content), engagement baseline (50→60). Expected new range: 78-86.

[2026-03-31] SDXL generation first, Pexels fallback — Unique images > stock photos for SEO and brand identity. Pipeline tries SDXL Lightning (4-step, fast) then falls back to Pexels if GPU busy or unavailable.

[2026-03-31] Auto-publish threshold lowered to 75 — After recalibrating scoring (accuracy, readability, completeness, engagement), scores land in 75-82 range. 11 posts auto-published on first cycle. Raw model quality is 9.6/10; 75 catches bad content while letting good content flow.

[2026-03-31] Cost controls tightened — Gemini was blanket-allowed as "free_tier" (caused $300 incident). Now all Google calls go through budget checks. Daemon checks daily spend before creating tasks ($5/day cap). Direct API calls in ai_content_generator still need cost logging (tracked).

[2026-03-31] Research service over RAG — Built known-reference database (20+ tools' official docs) + internal post linking + optional web search. Injected into generation prompt. Solves "fabricated citations" without needing a full RAG pipeline. RAG reframed as per-client tone matching for multi-site phase.

[2026-03-31] Dogfood our own content — Posts about AI architecture should inform our system design. High-performing AI architecture posts feed back into actual architecture decisions.

[2026-03-31] GPU-aware opportunistic scheduling — every idle millisecond is wasted compute. Daemon checks GPU util every 2 min, runs productive work (re-scoring, pre-generation, benchmarks) when idle. Every new service must expose an anticipation interface.

[2026-03-31] Brain should exercise services like reflexes — not just "is it up" but "is it working correctly." Send test inputs through each service, verify responses, auto-fix or alert. Self-healing through active probing (#1465).

[2026-03-31] Calculated > Generated — LLMs are inconsistent. Use deterministic code for everything that can be computed (trends, scoring, validation, scheduling). LLMs only for creative/judgment tasks. Calculated code can be locked down, unit tested, and validated.

[2026-03-31] Saving Matt's time is the #1 priority — more important than saving money. Every feature must reduce the number of times Matt has to look at something. The ideal: daily Telegram summary, no action required unless genuinely wrong. If Matt has to manually review things, the automation failed.

[2026-03-31] The product IS the at-home AI factory — plug-and-play full stack (hardware + software). Not just content pipeline but the entire self-operating system packaged for others. Solar-powered, self-sufficient, autonomous revenue from home.

[2026-03-31] AI models are biased backward — trained models can't think about what doesn't exist. Need forward-thinking mechanisms: real-time web data, trend extrapolation, human flags for paradigm shifts. The system must know what it DOESN'T know and actively seek to fill those gaps.

[2026-03-31] Revenue streams: AdSense (pending) + Amazon Associates + Grafana/Railway/Mercury referrals + Gumroad guides + Patreon (podcast + subscribers). Same content, multiple formats, multiple channels. Every blog post → podcast episode → social posts.

[2026-03-31] Nothing is free — local GPU costs electricity. Track ALL costs: compute power, API spend, hosting, insurance, hardware depreciation. Total investment ~$35.5K. Monthly operating ~$183. Break-even requires revenue above that. System must know its own economics.

[2026-03-31] Data freshness — the system must ask "are my metrics up to date?" Every value has a freshness window. Stale data = low confidence decisions. The cerebellum monitors when each metric was last updated.

[2026-03-31] Military-grade quality standard — System should be tight enough for mission-critical use. No loose ends, no placeholder data, no silent failures.
