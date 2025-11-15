# 🎯 Active Decisions - Glad Labs

**Last Updated:** November 14, 2025  
**Purpose:** Document key architectural and strategic decisions  
**Status:** Living Document

---

## 📋 Decision Log

### Architecture Decisions

#### Decision 1: FastAPI for Backend

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Rationale:** See `WHY_FASTAPI.md`  
**Impact:** All backend APIs, agent orchestration, model routing  
**Trade-offs:** Python ecosystem, async support, performance  
**Related:** Python 3.12, SQLAlchemy ORM

**Key Benefits:**

- Native async/await for multi-agent orchestration
- Automatic OpenAPI documentation
- Type hints with Pydantic validation
- High performance for AI workloads

**Revisit Criteria:** If API throughput exceeds 10K req/s or latency >500ms

---

#### Decision 2: PostgreSQL for Production Database

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Rationale:** See `WHY_POSTGRESQL.md`  
**Impact:** All data persistence, Strapi CMS backend  
**Trade-offs:** Operational complexity vs. scalability  
**Related:** SQLAlchemy, connection pooling

**Key Benefits:**

- ACID compliance for critical data
- Full-text search for content
- JSONB for flexible schemas
- Advanced indexing

**Revisit Criteria:** If queries exceed 1s or need NoSQL flexibility

---

#### Decision 3: React + Next.js for Frontend

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Impact:** Public site, Oversight Hub UI  
**Trade-offs:** JavaScript ecosystem, bundle size

**Key Benefits:**

- SSG for static content (public site)
- Real-time updates (Oversight Hub)
- Component reusability
- Large ecosystem

---

#### Decision 4: Multi-Agent Orchestration via FastAPI

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Impact:** Content, Financial, Market, Compliance agents  
**Trade-offs:** Complexity vs. specialization

**Key Benefits:**

- Specialized agents per domain
- Parallel execution via asyncio
- Independent scaling
- Clear separation of concerns

---

### Technical Decisions

#### Decision 5: Model Router with Multi-Provider Fallback

**Status:** ✅ ACTIVE  
**Date Decided:** Q4 2025  
**Impact:** All LLM calls, cost optimization  
**Fallback Chain:** Ollama → Claude 3 → GPT-4 → Gemini

**Key Benefits:**

- Zero-cost local inference (Ollama)
- Cost optimization through fallback
- No vendor lock-in
- Development speed (use free Ollama locally)

**Revisit Criteria:** If new providers offer better capabilities

---

#### Decision 6: Self-Critiquing Content Generation Pipeline

**Status:** ✅ ACTIVE  
**Date Decided:** Q4 2025  
**Impact:** Content quality, publication readiness  
**Components:** 6-agent pipeline (Research → Create → QA → Refine → Image → Publish)

**Key Benefits:**

- Higher content quality
- Self-improvement capability
- Feedback loops
- Publication-ready output

---

### Infrastructure Decisions

#### Decision 7: Railway for Backend Deployment

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Impact:** Production backend, database hosting  
**Alternative Considered:** AWS, Azure, DigitalOcean

**Key Benefits:**

- Simple GitHub integration
- Automatic deployments
- PostgreSQL included
- Cost-effective ($115-230/month)

---

#### Decision 8: Vercel for Frontend Deployment

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Impact:** Public Site, Oversight Hub deployment  
**Alternative Considered:** Netlify, AWS Amplify

**Key Benefits:**

- Optimized for Next.js
- Automatic deployments
- Global CDN
- Free tier for development

---

#### Decision 9: GitHub Actions for CI/CD

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Impact:** Automated testing, deployment workflow

**Key Benefits:**

- Native GitHub integration
- Free for public repos
- Secrets management
- Branch-specific workflows

---

### Process Decisions

#### Decision 10: Pragmatic Documentation Strategy

**Status:** ✅ ACTIVE  
**Date Decided:** November 14, 2025  
**Impact:** Documentation maintenance, developer experience

**Categories:**

- Maintain Actively: Architecture, decisions, technical reference
- Maintain Minimally: How-to guides (only valuable topics)
- Never Maintain: Archive (historical preservation)

**Key Benefits:**

- Reduced maintenance burden
- Better developer experience
- Encourages troubleshooting documentation
- Sustainable long-term

---

#### Decision 11: Conventional Commits for Git Workflow

**Status:** ✅ ACTIVE  
**Date Decided:** Q3 2025  
**Format:** `type: subject` (feat, fix, docs, etc.)  
**Impact:** All commits, changelog generation

---

## 🔄 Decisions Under Review

_None currently. All decisions are active._

---

## 📊 Decision Quality Metrics

| Metric                     | Target    | Status    |
| -------------------------- | --------- | --------- |
| Decisions documented       | 100%      | ✅ 11/11  |
| Decisions with rationale   | 100%      | ✅ 100%   |
| Decision revisit frequency | Quarterly | ✅ Active |
| Trade-offs analyzed        | 100%      | ✅ 100%   |

---

## 🔗 Related Documents

- `WHY_FASTAPI.md` - FastAPI decision details
- `WHY_POSTGRESQL.md` - PostgreSQL decision details
- `docs/02-ARCHITECTURE_AND_DESIGN.md` - System architecture
- `docs/04-DEVELOPMENT_WORKFLOW.md` - Process decisions

---

## 📝 How to Add Decisions

When making a significant decision:

1. Document the decision here with:
   - Status (ACTIVE, UNDER_REVIEW, DEPRECATED)
   - Date decided
   - Rationale/link to detailed doc
   - Impact (what changes)
   - Trade-offs analyzed
   - Key benefits
   - Revisit criteria

2. Create detailed doc if complex (e.g., WHY_FASTAPI.md)

3. Commit with: `docs: decision - [topic]`

4. Reference in related docs (architecture, etc.)

---

**Maintained by:** Tech Leads  
**Review Schedule:** Quarterly (Q1, Q2, Q3, Q4)  
**Last Reviewed:** November 14, 2025  
**Next Review:** February 14, 2026
