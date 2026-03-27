# Glad Labs — Personal Media Operating System

**Vision:** Enable anyone to run a professional media presence powered by AI, so they can focus on creating and living while the system handles distribution, optimization, and growth.

**First customer:** Matt — PC hardware builder, gamer, AI developer.

---

## The Problem

Creative people have skills and passions but the operational overhead of media presence is crushing:

- Maintaining posting schedules across 5+ platforms
- Writing SEO-optimized articles
- Editing clips from streams/recordings
- Managing comments and community
- Tracking analytics across platforms
- The anxiety of "putting yourself out there"
- The paralysis of maintaining it all manually

Most people never start, or start and burn out within months.

## The Solution

An autonomous media operating system that:

1. Takes raw inputs (photos, stream recordings, ideas, conversations)
2. Transforms them into platform-optimized content (blog posts, YouTube shorts, TikTok clips, tweets, Instagram reels)
3. Distributes across all connected platforms
4. Monitors performance and learns what works
5. Runs 24/7 with minimal human intervention
6. Operates via conversational interface (Discord/Telegram)

The human does the fun part (building PCs, gaming, creating). The system does everything else.

---

## Architecture

### Layer 1: Conversation Interface (OpenClaw)

The only interface the user needs. Everything happens through chat:

```
"Here's a photo of my new RTX 5090 build" [attaches image]
→ System generates: blog post, Instagram caption, tweet, Reddit post
→ Schedules distribution across all platforms
→ Responds: "Nice build! I've drafted content for 4 platforms. Auto-posting at optimal times today."

"I'm streaming Elden Ring tonight at 8pm"
→ System monitors the stream
→ Clips highlights automatically
→ Generates YouTube Short + TikTok + Twitter clip
→ Posts next morning with AI-generated commentary

"What's working best this month?"
→ Analytics agent queries all platforms
→ "Your PC build posts get 3x more engagement than game reviews.
   The RTX 5090 build got 12K views on YouTube. Recommendation:
   do more build content, especially teardowns and benchmarks."
```

### Layer 2: Business Brain (FastAPI + LLM Intent Router)

The central nervous system. Receives intents from OpenClaw and routes to the right agents:

```
┌─────────────────────────────────────────────────────┐
│                  BUSINESS BRAIN                      │
│                                                      │
│  Intent Router (LLM + RAG context)                  │
│  "What should I do with this input?"                 │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │              AGENT REGISTRY                   │   │
│  │                                               │   │
│  │  Content Agents:                              │   │
│  │  ├── BlogWriter      (long-form articles)     │   │
│  │  ├── SocialWriter    (platform-specific)      │   │
│  │  ├── VideoClipper    (stream → highlights)    │   │
│  │  ├── ImageProcessor  (photos → multi-format)  │   │
│  │  └── SEOOptimizer    (keywords, metadata)     │   │
│  │                                               │   │
│  │  Distribution Agents:                         │   │
│  │  ├── YouTubeAgent    (upload, metadata, SEO)  │   │
│  │  ├── TikTokAgent     (format, upload, tags)   │   │
│  │  ├── TwitterAgent    (threads, media, timing) │   │
│  │  ├── InstagramAgent  (reels, stories, posts)  │   │
│  │  ├── RedditAgent     (subreddit targeting)    │   │
│  │  ├── BlogPublisher   (Next.js ISR)            │   │
│  │  └── NewsletterAgent (email campaigns)        │   │
│  │                                               │   │
│  │  Operations Agents:                           │   │
│  │  ├── AnalyticsAgent  (cross-platform metrics) │   │
│  │  ├── SchedulerAgent  (content calendar)       │   │
│  │  ├── CostAgent       (budget management)      │   │
│  │  ├── QualityAgent    (content QA)             │   │
│  │  ├── CommunityAgent  (comments, engagement)   │   │
│  │  ├── ResearchAgent   (trends, competitors)    │   │
│  │  └── HealthAgent     (system monitoring)      │   │
│  │                                               │   │
│  │  Adding a new agent:                          │   │
│  │  1. Implement BaseAgent interface             │   │
│  │  2. Register in agent_registry                │   │
│  │  3. System auto-discovers via description     │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Layer 3: Knowledge Base (PostgreSQL + Embeddings)

The shared memory that makes the system intelligent over time:

```
┌─────────────────────────────────────────────────────┐
│               KNOWLEDGE BASE                         │
│                                                      │
│  Content Knowledge:                                  │
│  ├── Every article ever written (full text + embed)  │
│  ├── Topics covered per site/niche                   │
│  ├── Writing style profiles per brand                │
│  ├── Quality scores and QA feedback history          │
│  └── What content performs best (learned)            │
│                                                      │
│  Audience Knowledge:                                 │
│  ├── Platform-specific engagement patterns           │
│  ├── Best posting times per platform                 │
│  ├── Which topics resonate where                     │
│  ├── Comment sentiment analysis                      │
│  └── Audience growth trends                          │
│                                                      │
│  Business Knowledge:                                 │
│  ├── Cost per piece of content by type               │
│  ├── Revenue per platform (AdSense, affiliate, etc.) │
│  ├── ROI by content type                             │
│  ├── Budget constraints and projections              │
│  └── Strategic priorities (set by operator)          │
│                                                      │
│  Operator Knowledge:                                 │
│  ├── Personal interests and expertise areas          │
│  ├── Tone and voice preferences                      │
│  ├── Anxiety triggers (what NOT to post)             │
│  ├── Creative assets (photos, videos, builds)        │
│  └── Schedule and availability                       │
│                                                      │
│  All stored in PostgreSQL with vector embeddings     │
│  Every agent reads from AND writes to this base      │
│  System gets smarter with every interaction          │
└─────────────────────────────────────────────────────┘
```

### Layer 4: Compute (Hybrid Local + Cloud)

```
┌─────────────────────────┐     ┌─────────────────────────┐
│     LOCAL PC             │     │     CLOUD                │
│                          │     │                          │
│  Ollama (free LLMs)      │     │  Anthropic/OpenAI/Google │
│  ├── Research (free)     │     │  ├── Quality drafting    │
│  ├── Drafting (free)     │     │  ├── Complex reasoning   │
│  └── Embeddings (free)   │     │  └── Vision/multimodal   │
│                          │     │                          │
│  SDXL (image gen)        │     │  Cloud Run (FastAPI)     │
│  FFmpeg (video clips)    │     │  Cloud SQL (PostgreSQL)  │
│  Whisper (transcription) │     │  Vercel (Next.js sites)  │
│                          │     │  Grafana (monitoring)    │
│  OpenClaw Gateway        │     │                          │
│  Claude Code (dev)       │     │  YouTube/TikTok/Twitter  │
│                          │     │  APIs (distribution)     │
└─────────────────────────┘     └─────────────────────────┘

Model Router decides: use local if available, cloud if needed.
Heavy/free tasks → local. Quality-critical → cloud. Cost-optimized.
```

### Layer 5: Background Loops (The System Working While You Sleep)

```
┌─────────────────────────────────────────────────────┐
│              AUTONOMOUS LOOPS                         │
│                                                      │
│  CONTENT LOOPS:                                      │
│  ├── Content Calendar    (daily)                     │
│  │   Read schedule → create tasks → execute          │
│  ├── Content Executor    (continuous)                │
│  │   Pick up pending → generate → QA → publish       │
│  ├── Stream Monitor      (when live)                 │
│  │   Watch stream → clip highlights → queue edits    │
│  └── Social Scheduler    (hourly)                    │
│      Check queue → post at optimal times per platform│
│                                                      │
│  INTELLIGENCE LOOPS:                                 │
│  ├── Analytics Collector (every 6hrs)                │
│  │   Pull metrics from all platforms → store         │
│  ├── Learning Loop       (daily)                     │
│  │   Analyze performance → update strategy → adjust  │
│  ├── Trend Scanner       (daily)                     │
│  │   Research trending topics in niches → suggest    │
│  └── Competitor Watch    (weekly)                    │
│      Monitor competitor content → identify gaps      │
│                                                      │
│  HEALTH LOOPS:                                       │
│  ├── Site Health         (every 15min)               │
│  │   Ping sites → check uptime → alert if down      │
│  ├── Cost Watchdog       (realtime)                  │
│  │   Track spend → pause if over budget              │
│  ├── Self-Healer         (every 5min)                │
│  │   Stuck tasks → retry. Failed → diagnose.        │
│  └── Quality Monitor     (hourly)                    │
│      Check recent scores → alert if degrading        │
└─────────────────────────────────────────────────────┘
```

---

## Matt's First Three Sites

### Site 1: PC Hardware & Custom Builds

**Domain:** e.g., `buildlab.tech`
**Content mix:**

- AI-generated articles: GPU reviews, build guides, benchmarking, component analysis
- Personal builds: Matt's photos/videos → system generates blog posts, social clips
- Comparison articles: "RTX 5090 vs 5080 for content creators"
- Monetization: Amazon affiliate links (components), AdSense, sponsored reviews

### Site 2: Gaming

**Domain:** e.g., `gladgaming.gg`
**Content mix:**

- Stream highlights: auto-clipped → YouTube Shorts, TikTok, site
- Game reviews: AI-generated first drafts, Matt adds personal takes
- News coverage: AI monitors game news feeds, generates articles
- Community: Giscus comments, Discord community
- Monetization: AdSense, game affiliate links, potential sponsorships

### Site 3: AI Development & Future Tech

**Domain:** e.g., `glad-labs.com` (existing)
**Content mix:**

- Technical articles: AI trends, tutorials, tool reviews
- Project showcases: Matt's AI projects documented automatically
- Industry analysis: AI monitors papers/launches, generates commentary
- Monetization: Newsletter, affiliate (AI tools), consulting leads

### Cross-platform social presence

All three sites feed into shared social accounts:

- YouTube: build videos, stream clips, AI tutorials
- TikTok: short clips from builds and streams
- Twitter/X: threads, hot takes, project updates
- Instagram: build photos, behind-the-scenes
- Reddit: targeted posts to r/buildapc, r/gaming, r/MachineLearning

---

## Data Model (PostgreSQL — the center of everything)

### Core Tables (exist or need creation)

```
sites                    — Multi-site configuration
content_tasks            — Task queue (all content types)
posts                    — Published blog content
media_assets             — Photos, videos, clips (metadata + URLs)
social_posts             — Scheduled/published social content
social_accounts          — Connected platform credentials
content_calendar         — Planned content schedule
agent_registry           — Registered agents and their capabilities

categories, tags         — Content taxonomy
quality_evaluations      — QA scores
cost_logs                — LLM/API cost tracking
settings                 — Runtime configuration

knowledge_base           — RAG entries with embeddings
  ├── type: content_performance (learned from analytics)
  ├── type: audience_insight (learned from engagement)
  ├── type: business_strategy (set by operator)
  ├── type: writing_style (from writing samples)
  └── type: topic_research (from trend scanning)

analytics_snapshots      — Platform metrics over time
webhook_events           — Event delivery to OpenClaw
background_jobs          — Background loop state/scheduling
```

### New Tables Needed

```sql
-- Media assets (photos, videos, clips)
media_assets (
    id, site_id, type[photo|video|clip|thumbnail],
    source[upload|generated|clipped|screenshot],
    url, storage_path, thumbnail_url,
    title, description, alt_text,
    metadata JSONB, -- dimensions, duration, format, etc.
    created_at, updated_at
)

-- Social media accounts per site
social_accounts (
    id, site_id, platform[youtube|tiktok|twitter|instagram|reddit],
    account_name, credentials_encrypted,
    posting_enabled, optimal_times JSONB,
    created_at, updated_at
)

-- Social posts (scheduled + published)
social_posts (
    id, site_id, content_task_id, social_account_id,
    platform, post_type[post|reel|short|story|thread],
    content_text, media_asset_ids[],
    scheduled_at, published_at, status,
    platform_post_id, -- ID returned by platform after posting
    engagement JSONB, -- likes, shares, comments (updated by analytics)
    created_at, updated_at
)

-- Content calendar
content_calendar (
    id, site_id, date, content_type,
    topic, notes, priority,
    auto_generated BOOLEAN, -- true if system planned it
    task_id, -- linked once task is created
    status[planned|in_progress|published|skipped],
    created_at, updated_at
)

-- Background job registry
background_jobs (
    id, name, type[loop|scheduled|one_shot],
    schedule, -- cron expression or interval
    last_run_at, next_run_at,
    status[running|idle|paused|failed],
    config JSONB,
    last_result JSONB,
    created_at, updated_at
)
```

---

## Agent Interface (Standard Contract)

Every agent — whether it writes blog posts, clips videos, or monitors analytics — follows the same interface:

```python
class BaseAgent(ABC):
    """Standard interface for all Glad Labs agents."""

    name: str                    # Unique identifier
    description: str             # LLM reads this to decide when to invoke
    capabilities: list[str]      # Tags for discovery
    required_tools: list[str]    # What this agent needs (db, model_router, etc.)

    @abstractmethod
    async def execute(self, intent: str, context: dict) -> AgentResult:
        """Execute a task. Context includes RAG results from knowledge base."""

    @abstractmethod
    async def get_status(self) -> dict:
        """Report current state for monitoring dashboard."""

    async def learn(self, outcome: dict) -> None:
        """Optional: process feedback to improve future performance."""
```

### Adding a new "employee"

1. Create `agents/youtube_agent.py` implementing `BaseAgent`
2. Register: `agent_registry.register(YouTubeAgent())`
3. System auto-discovers via `description` field
4. The intent router can now dispatch YouTube-related requests to it
5. Optionally add an OpenClaw SKILL.md for direct invocation

---

## Implementation Roadmap

### Phase 1: OPERATE (current — get money flowing)

- [x] Auto-publish pipeline
- [x] Batch content creation
- [x] OpenClaw skills for pipeline control
- [x] Webhook notifications to Discord
- [ ] Connect Grafana dashboards
- [ ] Pick niches, start publishing content
- [ ] Set up AdSense on first site

### Phase 2: MEDIA (photos + video support)

- [ ] Media asset storage (S3/Cloud Storage + DB metadata)
- [ ] Image processing agent (resize, thumbnail, alt-text generation)
- [ ] Photo → multi-platform content (blog post + Instagram + Twitter)
- [ ] YouTube upload agent (metadata, thumbnails, SEO)
- [ ] TikTok upload agent
- [ ] Social post scheduling (optimal times per platform)

### Phase 3: INTELLIGENCE (the system gets smarter)

- [ ] Enhanced knowledge base (expand memory_system for business context)
- [ ] Analytics collector (pull from Google Analytics, YouTube Studio, etc.)
- [ ] Learning loop (daily analysis → strategy adjustments)
- [ ] Content calendar agent (auto-plans based on what works)
- [ ] Trend scanner (monitors industry news, suggests timely content)

### Phase 4: STREAM (live content automation)

- [ ] Stream monitor agent (watches Twitch/YouTube stream)
- [ ] Auto-clipper (FFmpeg + AI to identify highlight moments)
- [ ] Clip → Short/Reel pipeline (format, caption, upload)
- [ ] Stream summary generator (recap blog post after each stream)

### Phase 5: COMMUNITY (engagement automation)

- [ ] Comment monitoring across platforms
- [ ] Auto-reply for common questions (with operator approval)
- [ ] Community sentiment tracking
- [ ] Newsletter automation (weekly digest of best content)

### Phase 6: PLATFORM (open to others)

- [ ] Multi-tenant architecture
- [ ] Onboarding flow (connect platforms, set preferences)
- [ ] Billing (Stripe usage-based)
- [ ] Self-service dashboard (Grafana or lightweight custom)

---

## Design Principles

1. **PostgreSQL is the source of truth** — every piece of data, every decision, every metric lives in the database. External tools connect to it.

2. **Agents are cheap to add** — standard interface, auto-discovery, shared tools. Adding a "TikTok employee" should be an afternoon of work.

3. **Local-first, cloud-fallback** — use local hardware when available (Ollama, FFmpeg, Whisper). Fall to cloud APIs when local isn't available or quality demands it.

4. **The system should work while you sleep** — background loops handle routine operations. You intervene only for creative decisions and strategy.

5. **Conversation is the interface** — no dashboards to maintain. Ask questions, give directions, review results — all through Discord/Telegram.

6. **Learn from everything** — every piece of content published, every engagement metric, every cost data point feeds back into the knowledge base. The system improves continuously.

7. **Anxiety-aware design** — the system handles the scary parts (posting, responding to comments, dealing with negative feedback). The operator focuses on creating.

8. **Quality over quantity** — never sacrifice content quality for volume. The quality scoring system is the gatekeeper. Better to publish 3 great posts than 10 mediocre ones.
