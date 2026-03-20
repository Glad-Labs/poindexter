# 02 - Architecture & Design

**Last Updated:** March 10, 2026
**Version:** 3.0.81
**Status:** ✅ Production Ready | Multi-Agent System | Fast API Backend

---

## 🎯 Quick Links

- **[Vision & Mission](#vision--mission)** - What Glad Labs does
- **[System Architecture](#system-architecture)** - High-level overview
- **[Technology Stack](#technology-stack)** - Tools and platforms
- **[Component Design](#component-design)** - Each system explained
- **[Data Architecture](#data-architecture)** - Database and storage
- **[Roadmap](#roadmap)** - Phase 1-3 implementation plan

---

## 🌟 Vision & Mission

### The Complete AI Co-Founder

**Mission:** Create a fully autonomous digital business partner that can:

- Understand business goals and market trends
- Plan and execute content strategies
- Manage multi-platform social media presence
- Generate multimedia content (text, images, video)
- Handle sales, CRM, and accounting integration
- Ensure legal compliance
- Identify and execute growth opportunities
- Continuously optimize for ROI
- Operate locally or in the cloud
- Accessible from any device, anywhere

**Core Principle:** Maximum capability at minimum cost

### Strategic Pillars

| Pillar             | Focus                             | Goal                                                                                           |
| ------------------ | --------------------------------- | ---------------------------------------------------------------------------------------------- |
| **Core Product**   | **Intelligent Automation (SaaS)** | The marketable product is the AI Agent System itself, pivoting to scalable B2B services        |
| **Content Engine** | **High-Fidelity Content**         | Consistently generate sophisticated, on-brand content that builds community and drives traffic |
| **Technology**     | **Serverless Scalability**        | Utilize cost-effective, cloud-native stack with pay-per-use pricing for maximum runway         |

### Architecture Principles

1. **API-First Design**: Headless CMS with RESTful APIs
2. **Component Modularity**: Reusable, testable components
3. **Production Ready**: Scalable, secure, and monitorable
4. **Cost Optimization**: Zero-cost local AI (Ollama) + cloud fallback
5. **Multi-Provider AI**: Flexible model selection with automatic fallback
6. **Serverless First**: Auto-scaling without infrastructure management
7. **Offline Capable**: Works locally without internet (Ollama mode)

---

## 🏗️ System Architecture

### High-Level Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│                  OVERSIGHT HUB (Control Center)                  │
│                       Dashboard & UI                             │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ • Content Calendar  • Agent Monitor  • Cost Tracking       │ │
│  │ • Performance Dashboard  • Approval Workflows  • Settings  │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                              ↕️ REST API
┌──────────────────────────────────────────────────────────────────┐
│         AI CO-FOUNDER ORCHESTRATOR (Central Brain)               │
│                     FastAPI + Python                             │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Multi-Provider Model Router (Ollama/OpenAI/Claude/Gemini)│ │
│  │  Multi-Agent Orchestrator & Task Distribution             │ │
│  │  Memory System & Context Management                       │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                              ↕️ Internal APIs
### Backend: FastAPI Orchestrator (Port 8000)

The backend is built with FastAPI and handles all asynchronous task execution and agent orchestration.

**Key Architecture Shifts (Feb 2026):**
- **Unified Task API:** Synchronous route modules (like `/api/content`) have been consolidated into a single `/api/tasks` entry point.
- **Async DB Engine:** Replaced SQLAlchemy ORM with **asyncpg** for high-performance PostgreSQL interaction.
- **Worker Polling:** The `TaskExecutor` service runs a background polling loop (every 5s) to pick up new tasks.

**Request Flow:**
1. **POST `/api/tasks`**: User creates a task (e.g., `task_type="blog_post"`).
2. **PostgreSQL**: Task is stored as `pending`.
3. **TaskExecutor**: Background polling picks up the task and calls `UnifiedOrchestrator`.
4. **UnifiedOrchestrator**: Parses intent and routes to the correct Agent Pipeline.

### Data Architecture

- **Primary DB**: PostgreSQL 15+
- **Driver**: `asyncpg` (Full Async)
- **Schema Management**: Managed via `DatabaseService` delegates (`TasksDatabase`, `UsersDatabase`, etc.).
│  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐       │
│  │  PostgreSQL│ │            │ │  Redis   │ │ Storage  │       │
│  │ (Production)│ │            │ │  Cache   │ │ (Media)  │       │
│  └────────────┘ └────────────┘ └──────────┘ └──────────┘       │
└──────────────────────────────────────────────────────────────────┘
```

### Request Flow

```text
1. User Action (Oversight Hub)
   ↓
2. REST API Call to Co-Founder Agent
   ↓
3. Request Processing & Routing
   ↓
4. Multi-Agent Orchestrator selects agents
   ↓
5. Agents execute tasks (in parallel when possible)
   ↓
6. Model Router selects best AI model
   ↓
7. LLM API call (Ollama/OpenAI/Claude/Gemini)
   ↓
8. Response aggregation
   ↓
9. Result stored in PostgreSQL
   ↓
10. Response sent back to UI
```

---

## 🔧 Technology Stack

### Frontend Architecture

| Component         | Technology                       | Port | Status        |
| ----------------- | -------------------------------- | ---- | ------------- |
| **Public Site**   | Next.js 15 + React 18 + Tailwind | 3000 | ✅ Production |
| **Oversight Hub** | React 18 + Material-UI + Zustand | 3001 | ✅ Production |

**Frontend Features:**

- Server-side rendering (SSR) and static generation (SSG)
- Responsive design with Tailwind CSS
- Component-based architecture
- RESTful API integration
- Real-time updates (WebSocket ready)
- Authentication & authorization
- Dark mode support

### Backend Architecture

| Component         | Technology                      | Port | Status        |
| ----------------- | ------------------------------- | ---- | ------------- |
| **AI Co-Founder** | FastAPI + Python 3.12 + Uvicorn | 8000 | ✅ Production |
| **CMS Data**      | PostgreSQL (Direct Access)      | 5432 | ✅ Production |

**Backend Features:**

- RESTful API (160+ endpoints)
- WebSocket support (planned)
- Multi-agent orchestration
- Model routing and fallback
- Memory system with context awareness
- Async task processing
- Error handling and recovery
- Comprehensive logging

### Infrastructure & Services

| Service        | Provider/Tech                         | Purpose                        | Status       |
| -------------- | ------------------------------------- | ------------------------------ | ------------ |
| **Database**   | PostgreSQL (required, no SQLite)      | Content and operational data   | ✅ Active    |
| **Cache**      | Redis                                 | Session management and caching | ✅ Available |
| **Storage**    | File system / Cloud Storage           | Media files and assets         | ✅ Active    |
| **Task Queue** | REST API + async workers (dev/prod)   | Async task processing          | ✅ Active    |
| **Deployment** | Railway (backend) / Vercel (frontend) | Cloud hosting                  | ✅ Active    |
| **Monitoring** | Application Insights (optional)       | Performance monitoring         | ⏳ Optional  |

### AI Model Providers (Multi-Provider Support)

| Provider      | Models                         | Cost         | Setup          | Speed   | Priority |
| ------------- | ------------------------------ | ------------ | -------------- | ------- | -------- |
| **Ollama**    | Mistral, Llama3.2, Phi, etc.   | 🟢 Free      | Easy (Local)   | 🟡 Vary | 🥇 #1    |
| **Anthropic** | Claude 3 (Opus, Sonnet, Haiku) | 🟠 Paid      | Easy (API key) | 🟢 Fast | 🥈 #2    |
| **OpenAI**    | GPT-4, GPT-4o, GPT-3.5         | 🟠 Paid      | Easy (API key) | 🟢 Fast | 🥉 #3    |
| **Google**    | Gemini Pro, Gemini 2.0         | 🟡 Free+Paid | Easy (API key) | 🟢 Fast | #4       |

**Fallback Chain (Automatic):** Ollama (local) → Claude 3 Opus → GPT-4 → Gemini → Fallback model

---

## 🧩 Component Design

### 1. Public Site (Next.js)

**Location:** `web/public-site/`

**Purpose:** Public-facing website showcasing content and brand

**Key Features:**

- Homepage with featured posts and content grid
- Individual post pages with full markdown rendering
- Category and tag-based content filtering
- SEO optimization with meta tags and Open Graph
- Newsletter signup integration
- Responsive design optimized for all devices

**Architecture:**

```text
public-site/
├── pages/
│   ├── index.js           # Homepage
│   ├── posts/[slug].js    # Dynamic post pages
│   ├── category/[slug].js # Category pages
│   ├── _app.js            # App wrapper
│   └── _document.js       # HTML document
├── components/
│   ├── Layout.js          # Main layout
│   ├── PostCard.js        # Post preview card
│   ├── Header.js          # Navigation header
│   ├── Footer.js          # Footer
│   └── SEO.js             # SEO metadata
├── lib/
│   ├── api.js             # FastAPI client
│   ├── constants.js       # App constants
│   └── utils.js           # Helper functions
├── styles/
│   └── globals.css        # Tailwind styles
└── public/
    └── images/            # Static images
```

**Data Flow:**

```text
Build Time:
  pages/posts/[slug].js → getStaticPaths → FastAPI
  ↓
  getStaticProps → Fetch post data
  ↓
  Generate static HTML (ISR enabled)

Runtime:
  User visits http://site.com/posts/post-slug
  ↓
  Serve pre-generated static HTML
  ↓
  React hydrates on client
```

### 2. Oversight Hub (React)

**Location:** `web/oversight-hub/`

**Purpose:** Admin control center for managing AI agents and content

**Key Features:**

- Real-time system health monitoring
- Task management with full CRUD operations
- Model provider configuration
- Cost tracking and financial metrics
- Social media management
- Agent status and performance monitoring

**Main Dashboard Sections:**

```text
Dashboard/
├── System Health
│   ├── Service Status (Backend, Services)
│   ├── Active Agents
│   ├── Recent Errors
│   └── Performance Metrics
│
├── Task Management
│   ├── Active Tasks
│   ├── Scheduled Tasks
│   ├── Completed Tasks
│   └── Failed Tasks (with retry)
│
├── Models & Configuration
│   ├── Available Models
│   ├── Provider Settings
│   ├── Model Performance
│   └── API Key Management
│
├── Financial Dashboard
│   ├── Cost Tracking (by model/provider)
│   ├── Budget Alerts
│   ├── ROI Calculations
│   └── Cost Trends
│
└── Content Calendar
    ├── Scheduled Posts
    ├── Draft Queue
    ├── Published Content
    └── Performance Timeline
```

**State Management:**

- Zustand for global app state
- React hooks for component-level state
- Axios for API communication
- WebSocket integration (ready for real-time updates)

### 3. CMS Data Layer (PostgreSQL)

**Location:** `src/cofounder_agent/routes/cms_routes.py`

**Purpose:** Database-driven content management via FastAPI routes (No separate CMS service)

**Data Models (PostgreSQL Tables):**

1. **Posts** (`posts` table)
   - title, slug, content (markdown/rich text)
   - excerpt, featured image, cover image
   - category (relation), tags (relation)
   - author, published date
   - SEO metadata (title, description, keywords)
   - Status (draft, published, archived)

2. **Categories** (`categories` table)
   - name, slug, description
   - Featured image
   - Posts relation
   - Meta description

3. **Tags** (`tags` table)
   - name, slug, description
   - Posts relation
   - Color/icon (for UI)

4. **Pages** (`pages` table)
   - title, slug, content
   - Featured image
   - SEO metadata
   - Visibility settings

5. **Tasks** (`tasks` table)
   - Title, description, type
   - Status (pending, in-progress, completed, failed)
   - Assigned agents
   - Created/updated timestamps
   - Result data

**API Endpoints (FastAPI):**

```bash
GET  /api/posts                    # List posts
GET  /api/posts/:id                # Get single post
POST /api/posts                    # Create post
PUT  /api/posts/:id                # Update post
DELETE /api/posts/:id              # Delete post

GET  /api/categories               # List categories
GET  /api/tags                     # List tags
```

### 4. Agent System Architecture (Self-Critiquing Pipeline)

**Location:** `src/agents/content_agent/`

**Purpose:** Modular AI agents for specialized tasks with self-critique feedback loops

**Key Features:**

- Self-critiquing pipeline: Creative generation → QA evaluation → Feedback → Refinement
- Individual agent capabilities: Research, Creative, Images, Publishing, QA, Summarizer
- Model fallback chain: Claude 3 Opus → GPT-4 → Gemini → Ollama (local, zero-cost)
- Modular usage: End-to-end blog generation OR individual agent access
- Output formatting: Markdown + SEO assets + Database compatible

**Core Agents:**

```python
# Agent roles and responsibilities
- CreativeAgent: Content generation with style consistency
- ResearchAgent: Topic research and fact gathering
- ImageAgent: Image selection and optimization
- PublishingAgent: Database formatting and publishing
- QAAgent: Quality evaluation and improvement suggestions
- SummarizerAgent: Extract key points and outline creation
```

**Self-Critiquing Pipeline Flow:**

```text
1. Input: Topic/Request
   ↓
2. ResearchAgent → Research data
   ↓
3. CreativeAgent → Draft content
   ↓
4. QAAgent → Evaluate & critique
   ↓
5. CreativeAgent (with feedback) → Refined content
   ↓
6. ImageAgent → Select visual assets
   ↓
7. PublishingAgent → Format for CMS
   ↓
8. Output: Publication-ready content
```

**Usage Patterns:**

- **End-to-end Content:** POST `/api/tasks` → Executes agent pipeline via TaskExecutor
- **Individual agents:** POST `/api/agents/{agent-name}` → Specific capability
- **Custom workflows:** Combine agents in any order for flexible pipelines

### 5. AI Co-Founder (FastAPI Backend)

**Location:** `src/cofounder_agent/`

**Purpose:** Central orchestrator for all AI-powered operations

**Core Components:**

#### Main API (`main.py`)

- FastAPI application
- 50+ REST endpoints
- Error handling and logging
- CORS middleware
- Request/response validation

#### Model Router (`services/model_router.py`)

- Multi-provider AI orchestration
- Automatic provider fallback (Claude → GPT → Gemini → Ollama)
- Cost tracking and optimization
- Rate limiting
- Token counting

#### Multi-Agent Orchestrator (`multi_agent_orchestrator.py`)

- Agent lifecycle management
- Task distribution and scheduling
- Parallel execution coordination
- Result aggregation
- Error recovery

#### Specialized Agents

```python
# Each agent inherits from BaseAgent

class ContentAgent(BaseAgent):
    """Generates and manages content"""
    - Content planning
    - Blog post generation
    - Social media content
    - Email campaigns

class FinancialAgent(BaseAgent):
    """Manages business financials"""
    - Cost tracking
    - Revenue calculations
    - Budget management
    - Financial projections

class MarketInsightAgent(BaseAgent):
    """Market analysis and trends"""
    - Competitor analysis
    - Trend identification
    - Audience insights
    - Opportunity detection

class ComplianceAgent(BaseAgent):
    """Legal and regulatory compliance"""
    - Content compliance checking
    - GDPR/CCPA checks
    - Risk assessment
    - Privacy policy management
```

#### Memory System (`memory_system.py`)

- Short-term context (current conversation)
- Long-term memory (persistent storage)
- Semantic search across memories
- Automatic cleanup and optimization

**API Endpoints (Core):**

```bash
POST /api/tasks              # Create task
GET  /api/tasks/:id          # Get task status
GET  /api/tasks              # List tasks
PUT  /api/tasks/:id          # Update task

POST /api/models/test        # Test model connection
GET  /api/models             # List available models
POST /api/models/configure   # Configure model

GET  /api/health             # System health check
GET  /api/metrics            # Performance metrics
```

---

## 🗄️ Data Architecture

### Entity Relationship Diagram

```text
┌─────────────┐         ┌─────────────┐
│   Posts     │────────▶│ Categories  │
│ (many)      │  1..n   │   (1)       │
└─────────────┘         └─────────────┘

┌─────────────┐         ┌─────────────┐
│   Posts     │────────▶│    Tags     │
│ (many)      │  m..n   │  (many)     │
└─────────────┘         └─────────────┘

┌─────────────┐         ┌─────────────┐
│   Posts     │────────▶│   Authors   │
│ (many)      │  1..n   │   (1)       │
└─────────────┘         └─────────────┘

┌─────────────┐         ┌─────────────┐
│   Tasks     │────────▶│   Agents    │
│ (many)      │  1..n   │  (many)     │
└─────────────┘         └─────────────┘

┌──────────────┐        ┌─────────────┐
│  Memories    │────────▶│   Agents    │
│ (many)       │  1..n   │   (1)       │
└──────────────┘        └─────────────┘
```

### Database Schema

**Posts Table:**

```sql
CREATE TABLE posts (
  id UUID PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  slug VARCHAR(255) UNIQUE NOT NULL,
  content TEXT NOT NULL,
  excerpt VARCHAR(500),
  featured_image_id UUID,
  cover_image_id UUID,
  category_id UUID REFERENCES categories(id),
  author_id UUID REFERENCES authors(id),
  status VARCHAR(50) DEFAULT 'draft',
  seo_title VARCHAR(255),
  seo_description VARCHAR(500),
  seo_keywords VARCHAR(255),
  published_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

**Tasks Table:**

```sql
CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  type VARCHAR(100) NOT NULL,
  status VARCHAR(50) DEFAULT 'pending',
  assigned_agents TEXT[],
  result_data JSONB,
  error_message TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP
);
```

**Memory Table:**

```sql
CREATE TABLE memories (
  id UUID PRIMARY KEY,
  agent_id UUID NOT NULL,
  content TEXT NOT NULL,
  embedding VECTOR(1536),
  memory_type VARCHAR(50),
  created_at TIMESTAMP DEFAULT NOW(),
  accessed_at TIMESTAMP DEFAULT NOW()
);
```

---

## 🎯 Roadmap

### Phase 1: Foundation Enhancement (Current - Weeks 1-4)

**Goal:** Strengthen existing infrastructure and prepare for expansion

#### 1.1 Oversight Hub Enhancements

- [x] Basic dashboard structure
- [ ] Content calendar view (day/week/month)
- [ ] Agent status dashboard
- [ ] Real-time notifications
- [ ] Cost tracking dashboard
- [ ] Approval workflow interface

**Status:** 🔄 In Progress

#### 1.2 Backend API Completion

- [x] Core endpoints (tasks, models)
- [ ] Advanced filtering and pagination
- [ ] WebSocket support
- [ ] Rate limiting improvements
- [ ] Comprehensive error handling

**Status:** 🔄 In Progress

#### 1.3 Database & CMS Optimization

- [x] Content types setup (PostgreSQL tables)
- [ ] Direct database access optimization
- [ ] Performance tuning (indexing, query optimization)
- [ ] Backup and recovery procedures
- [ ] Multi-language support (optional)

**Status:** 🔄 In Progress

**Estimated Time:** 2-3 weeks  
**Effort:** 40-50 hours

---

### Phase 2: Agent Specialization (Weeks 5-8)

**Goal:** Expand agent capabilities and integrate external services

#### 2.1 Specialized Agents

- [ ] Financial Agent - Cost tracking, projections, budget management
- [ ] Market Insight Agent - Competitor analysis, trend detection
- [ ] Compliance Agent - Legal review, risk assessment
- [ ] Enhanced Content Agent - Multi-format support

#### 2.2 External Integrations

- [ ] Social media APIs (Twitter, LinkedIn, Instagram, TikTok)
- [ ] CRM integration (HubSpot, Salesforce, Zoho)
- [ ] Accounting tools (QuickBooks, Xero, Wave)
- [ ] Email platforms (SendGrid, Mailchimp)

#### 2.3 Advanced Features

- [ ] Multi-modal content (images, videos)
- [ ] A/B testing framework
- [ ] Predictive analytics
- [ ] Recommendation engine

**Status:** 📋 Planned  
**Estimated Time:** 3-4 weeks  
**Effort:** 60-80 hours

---

### Phase 3: Scaling & Automation (Weeks 9-12)

**Goal:** Production-ready system with advanced automation

#### 3.1 Scaling Infrastructure

- [ ] Kubernetes deployment
- [ ] Auto-scaling configuration
- [ ] Multi-region support
- [ ] Load balancing optimization

#### 3.2 Advanced Automation

- [ ] Workflow builder UI
- [ ] Custom automation rules
- [ ] Trigger-based actions
- [ ] Multi-step sequences

#### 3.3 Analytics & Reporting

- [ ] Custom reports
- [ ] Performance dashboards
- [ ] ROI calculations
- [ ] Predictive insights

**Status:** 📋 Planned  
**Estimated Time:** 4-5 weeks  
**Effort:** 80-100 hours

---

## 🔐 Security Architecture

### Authentication

- **JWT tokens** for API authentication
- **API keys** for service-to-service communication
- **OAuth 2.0** for third-party integrations
- **Role-based access control (RBAC)**

### Data Protection

- **HTTPS/TLS** for all communications
- **Encryption at rest** for sensitive data
- **Rate limiting** on all APIs
- **CORS restrictions** for web requests

### Compliance

- **GDPR** compliant data handling
- **CCPA** privacy policy implementation
- **SOC 2** audit readiness
- **Regular security audits**

---

## 📊 Performance Design

### Caching Strategy

- **Redis cache** for frequently accessed data
- **CDN** for static assets
- **Browser caching** with proper headers
- **API response caching** where appropriate

### Database Optimization

- **Indexed queries** for performance
- **Connection pooling** for efficiency
- **Query optimization** and monitoring
- **Automatic backups** and recovery

### Frontend Optimization

- **Code splitting** and lazy loading
- **Image optimization** with next/image
- **CSS-in-JS** minimization
- **Service workers** for offline support

---

## 🚀 Next Steps

1. **Understand the architecture:**
   - Review this document thoroughly
   - Explore component READMEs in docs/components/

2. **Set up your development environment:**
   - Follow [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md)
   - Run all services locally

3. **Learn the codebase:**
   - Start with public-site/ (Next.js basics)
   - Move to oversight-hub/ (React state management)
   - Study cofounder_agent/ (FastAPI patterns)

4. **Contribute to development:**
   - Check [04-DEVELOPMENT_WORKFLOW.md](./04-DEVELOPMENT_WORKFLOW.md)
   - Review [05-AI_AGENTS_AND_INTEGRATION.md](./05-AI_AGENTS_AND_INTEGRATION.md)

---

## 📚 Related Documentation

- **[Setup Guide](./01-SETUP_AND_OVERVIEW.md)** - Getting started
- **[Deployment Guide](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)** - Production setup
- **[Development Workflow](./04-DEVELOPMENT_WORKFLOW.md)** - Git and testing
- **[AI Agents & Integration](./05-AI_AGENTS_AND_INTEGRATION.md)** - Agent details
- **[Operations Guide](./06-OPERATIONS_AND_MAINTENANCE.md)** - Production support

---

**[← Back to Documentation Hub](./00-README.md)**

[Setup](./01-SETUP_AND_OVERVIEW.md) • [Deployment](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md) • [Development](./04-DEVELOPMENT_WORKFLOW.md)
