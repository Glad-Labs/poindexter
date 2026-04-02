# Build Your Own AI Content Pipeline

## The Solo Founder's Guide to Automated Publishing

**Price: $39** | ~120 pages | Starter repo + config templates included

---

## Sales Page Copy

### Headline

**Stop paying $2,000/month for AI APIs. Build an AI factory that runs on your own hardware for under $30/month total.**

### The Problem

You know AI can generate content. You've seen the tools. But every solution out there has the same fatal flaw: recurring API costs that scale with usage. Write 10 articles? $15. Write 100? $150. And that's before you pay for hosting, databases, and the six other SaaS tools they want you to subscribe to.

Meanwhile, you're sitting on a perfectly good GPU that's idle 22 hours a day.

### What This Guide Is

This is the exact system behind [Glad Labs](https://gladlabs.io) — a solo-founder content business running 64+ published posts, 16 custom services, and a self-healing content pipeline. Total monthly cost: **$30**. API costs: **$0**.

This is not a tutorial that walks you through a toy project and wishes you luck. This is the production architecture, extracted into a reproducible guide. Every decision is explained. Every trade-off is documented. You'll build the same system that generates, reviews, optimizes, and publishes content autonomously — while you sleep.

### Who This Is For

- **Technical founders** who want automated content as a growth channel
- **Developers** with a decent GPU (RTX 3060+ / 12GB VRAM minimum) who want to put it to work
- **Solo operators** tired of paying per-token for something their own machine can do
- **Anyone** who read "every house is a datacenter" and felt something click

### Who This Is NOT For

- People who want to click a button and get a blog. Use ChatGPT.
- Non-technical folks. You need to be comfortable with Docker, Python, and SQL.
- People expecting 500 articles on day one. This builds a sustainable pipeline, not a spam cannon.

### What Makes This Different

| Generic AI Tutorial             | This Guide                                                                   |
| ------------------------------- | ---------------------------------------------------------------------------- |
| "Use the OpenAI API to..."      | Local Ollama inference, zero API costs                                       |
| Single model, hope for the best | Multi-model QA — different LLMs check each other's work                      |
| Hardcoded config in .env files  | Database-driven config, change anything without redeploying                  |
| "Deploy to Vercel!"             | Full stack: FastAPI + Next.js + PostgreSQL + pgvector + Grafana              |
| No quality control              | Three-layer anti-hallucination: prompts, LLM review, programmatic validation |
| Manual publishing               | Autonomous pipeline with auto-publish thresholds                             |

---

## What's Included

1. **The Guide** (PDF, ~120 pages) — Complete architecture and implementation walkthrough
2. **Starter Repository** — Working codebase with the core pipeline, ready to customize
3. **Configuration Templates** — Database seed files with 33+ app_settings keys, pre-tuned prompts, QA workflow definitions, and affiliate link schemas
4. **Docker Compose Stack** — One-command local development environment (FastAPI + PostgreSQL + pgvector + Ollama + Grafana)
5. **Grafana Dashboard JSON** — Import-ready monitoring dashboards for pipeline health, content quality, and cost tracking
6. **Model Recommendation Matrix** — Which Ollama models work best for which pipeline stages, tested on 12GB and 24GB VRAM configurations

---

## Table of Contents

### Part I: Architecture & Philosophy

#### Chapter 1: Why This Architecture Exists

_The decisions behind the system, so you understand what you're building and why._

- The $200/month problem: why cloud AI APIs don't work for solo founders
- "Every house is a datacenter" — the local-first thesis
- Brain-inspired architecture: why modeling your system after human neurology actually makes sense
- PostgreSQL as the spinal cord: why everything communicates through the database
- The coordinator/worker split: cloud for serving, local for computing
- What $30/month actually buys you (and what's free)

**Pages: 10**

#### Chapter 2: System Overview & Component Map

_The 30,000-foot view before we build anything._

- Full architecture diagram: how every service connects
- The six pipeline stages: Research, Draft, QA, Validation, SEO, Publish
- Service inventory: what each of the 16 services does and why it exists
- Data flow: from topic idea to published post to performance analytics
- The three deployment modes: local dev, hybrid (local worker + cloud coordinator), full cloud
- Hardware requirements and GPU sizing

**Pages: 8**

---

### Part II: Infrastructure

#### Chapter 3: The Foundation — Docker, PostgreSQL, and Ollama

_Get your local stack running in under 30 minutes._

- Docker Compose stack walkthrough (every service explained)
- PostgreSQL setup with pgvector extension for semantic search
- Ollama installation and model pulling (which models, why those models)
- Database schema: the 12 tables that run everything
- Seed data: app_settings, QA workflows, cost tiers
- Verifying everything works: health check endpoints

**Pages: 14**

#### Chapter 4: The FastAPI Backend — Coordinator and Worker Modes

_One codebase, two deployment modes. The trick that keeps cloud costs near zero._

- Project structure walkthrough
- The deployment mode pattern: how `DEPLOYMENT_MODE` changes behavior
- Coordinator mode: API serving, webhook delivery, scheduled publishing
- Worker mode: task claiming, Ollama inference, content generation
- Async-everywhere: why every function is async and how to not block the event loop
- The task queue pattern: `content_tasks` table as a distributed work queue
- Environment variables you actually need (just 3 in production)

**Pages: 14**

#### Chapter 5: The Next.js Frontend

_A fast, SEO-optimized blog that serves your content to the world._

- Next.js 15 app router setup
- ISR (Incremental Static Regeneration) with 5-minute revalidation
- Dynamic sitemap generation
- ViewTracker: roll your own analytics instead of paying for it
- Internal linking display and affiliate link rendering
- Google Search Console and AdSense integration
- The one-way sync pattern: local database to cloud on publish only

**Pages: 10**

---

### Part III: The Content Pipeline

#### Chapter 6: Stage 1 & 2 — Research and Drafting

_Teaching your local LLM to write content that doesn't suck._

- The research stage: how the system gathers context before writing
- Topic selection: revenue-aware content decisions (write what makes money)
- Prompt engineering for local models: what works with 7B-70B parameter models
- The drafting stage: structured output, consistent formatting
- Model routing: cost tiers (free/budget/standard/premium) instead of hardcoded model names
- Why you use different models for different stages

**Pages: 12**

#### Chapter 7: Stage 3 — Multi-Model QA (The Secret Weapon)

_Different AI models checking each other's work. This is where most pipelines fail and yours won't._

- Why single-model self-review is worthless
- The multi-model QA architecture: adversarial review between different LLMs
- QA Registry: composable QA workflows defined in your database
- Scoring rubrics: what "quality" means in measurable terms
- The three-layer anti-hallucination stack:
  - Layer 1: Prompt engineering (prevention)
  - Layer 2: LLM-based QA review (detection)
  - Layer 3: Programmatic validator — rule-based checks with no LLM involved (hard stops)
- Auto-publish thresholds: if QA score >= 80, ship it; otherwise, queue for review
- Building your own content_validator rules

**Pages: 16**

#### Chapter 8: Stage 4 & 5 — SEO Optimization and Training Data

_Automated metadata, internal linking, and capturing your own training data._

- Automated SEO metadata generation (titles, descriptions, slugs, keywords)
- The internal linker: automatically connecting related posts
- The affiliate linker: database-driven link injection
- Semantic search with pgvector: finding related content by meaning, not keywords
- Training data capture: every piece of content becomes future model fine-tuning data
- The revenue engine: tracking what content performs and feeding it back into topic selection

**Pages: 10**

#### Chapter 9: Stage 6 — Publishing and Distribution

_From approved draft to live post to social media — hands-free._

- The scheduled publisher: how posts go live without you touching anything
- Social post generation: X/Twitter and LinkedIn content from your blog posts
- Newsletter digest generation: automated weekly roundups
- The approval flow: fully autonomous vs. human-in-the-loop modes
- Webhook delivery for external integrations

**Pages: 8**

---

### Part IV: Operations & Intelligence

#### Chapter 10: The Brain Daemon — Self-Healing Infrastructure

_A standalone service that monitors everything and fixes problems before you notice them._

- Why the brain runs separately from the main application
- The 5-minute monitoring cycle
- Self-healing patterns: detecting and restarting failed services
- The knowledge graph: `brain_knowledge` table (entity/attribute/value triples)
- The reasoning queue: how the brain processes observations into decisions
- Decision audit trail: every autonomous action is logged
- Telegram alerts: getting notified on your phone when something needs attention

**Pages: 12**

#### Chapter 11: Monitoring with Grafana

_See everything. Pipeline health, content quality, costs, and hardware — all in one place._

- Grafana setup (local or Grafana Cloud free tier)
- Connecting to your PostgreSQL data source
- The five dashboards you need:
  - Operations (home): service health, task queue depth, error rates
  - Pipeline: content flow, stage durations, bottlenecks
  - Quality: QA scores over time, rejection rates, validator hit rates
  - Cost: spend tracking, model usage, budget burn rate
  - Hardware: GPU temperature, VRAM usage, inference speed
- Setting up alerts: stuck tasks, failure rate spikes, worker offline
- The "Command Center" playlist: cycling dashboards on a wall monitor or tablet

**Pages: 10**

#### Chapter 12: Database-Driven Configuration

_Change any behavior without touching code or redeploying. This is the killer feature._

- The `app_settings` table: 33+ configuration keys explained
- Settings categories: pipeline, QA, publishing, costs, social, monitoring
- The settings service: typed access with caching
- The cost guard: daily and monthly spend limits that actually stop spending
- Adding new settings: the pattern for extending configuration
- Managing settings remotely: API endpoints and MCP tools
- Why this beats environment variables for everything except secrets

**Pages: 8**

---

### Part V: Scaling and Monetization

#### Chapter 13: From Side Project to Business

_The operational reality of running an AI content business._

- Revenue model: AdSense, affiliate links, newsletter sponsorships, digital products
- Content performance analysis: which topics actually drive traffic and revenue
- The anticipation engine: observing gaps and proposing actions proactively
- Finance tracking: Mercury banking integration and automated P&L reports
- Business reports: daily and weekly metrics summaries delivered to your inbox
- Real numbers from Glad Labs: traffic, revenue, costs, margins

**Pages: 8**

#### Chapter 14: Deploying to Production

_Moving from local dev to a production hybrid setup._

- The split-database architecture: slim cloud Postgres for public data, local Postgres for operations
- Railway deployment for the coordinator (why Railway, how to set it up)
- Vercel deployment for the Next.js frontend
- Tailscale for secure access to local services from anywhere
- One-way sync: local to cloud on publish
- SSL, domains, and DNS configuration
- Production checklist: the 20 things to verify before going live

**Pages: 10**

#### Chapter 15: What's Next — Advanced Patterns

_Where to take the system once it's running._

- Fine-tuning local models on your own published content
- Multi-site operation: running several content properties from one pipeline
- The Linux migration path: moving from Windows to a dedicated Linux workstation
- Advanced QA: training a classifier on your accept/reject decisions
- API-free vision: replacing the last cloud LLM call (cross-model review) with local models
- Community and support: where to get help

**Pages: 6**

---

## Appendices

### Appendix A: Complete Database Schema

_Every table, every column, every index. Copy-paste ready._
**Pages: 4**

### Appendix B: App Settings Reference

_All 33+ configuration keys with descriptions, types, defaults, and examples._
**Pages: 4**

### Appendix C: Ollama Model Recommendations

_Tested model/stage combinations for 12GB, 16GB, and 24GB VRAM configurations._
**Pages: 2**

### Appendix D: Troubleshooting

_Common issues and fixes for every stage of the pipeline._
**Pages: 4**

---

## Page Count Summary

| Section                                       | Pages          |
| --------------------------------------------- | -------------- |
| Part I: Architecture & Philosophy (Ch 1-2)    | 18             |
| Part II: Infrastructure (Ch 3-5)              | 38             |
| Part III: The Content Pipeline (Ch 6-9)       | 46             |
| Part IV: Operations & Intelligence (Ch 10-12) | 30             |
| Part V: Scaling and Monetization (Ch 13-15)   | 24             |
| Appendices (A-D)                              | 14             |
| **Total**                                     | **~170 pages** |

---

## Pricing Rationale

**$39 one-time purchase.** No subscriptions. No upsells. No "premium tier."

At $39, this pays for itself the first month you don't spend $200 on API costs. The starter repo alone saves 100+ hours of architecture decisions and implementation. The config templates save another 20+ hours of trial and error with prompt engineering and QA workflow tuning.

---

## Gumroad Product Settings

- **Product name:** Build Your Own AI Content Pipeline
- **Subtitle:** The Solo Founder's Guide to Automated Publishing
- **Price:** $39
- **Format:** PDF guide + GitHub repo access + config templates
- **Tags:** AI, content automation, FastAPI, Ollama, local LLM, solo founder, developer tools
- **Thumbnail concept:** Dark background, terminal-style pipeline diagram showing the 6 stages, with a GPU icon and "$0 API costs" callout
