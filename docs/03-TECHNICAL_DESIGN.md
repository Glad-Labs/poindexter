# 03 - Technical Design

> **Comprehensive System Architecture & Technical Design for GLAD Labs Platform**

This document consolidates all technical design information including system architecture, data models, coding standards, and integration patterns.

---

## 📋 Table of Contents

1. [Strategic Overview](#strategic-overview)
2. [System Architecture](#system-architecture)
3. [Component Design](#component-design)
4. [Data Architecture](#data-architecture)
5. [API Design](#api-design)
6. [AI Model Architecture](#ai-model-architecture)
7. [Security Architecture](#security-architecture)
8. [Performance Design](#performance-design)
9. [Deployment Architecture](#deployment-architecture)
10. [Coding Standards](#coding-standards)
11. [Development Workflows](#development-workflows)

---

## 🎯 Strategic Overview

### Core Mission

To operate the most efficient, automated, solo-founded digital firm by fusing high-quality content creation with an intelligent, conversational **AI Co-Founder** that manages all business operations.

### Strategic Pillars

| Pillar             | Focus                             | Goal                                                                                           |
| ------------------ | --------------------------------- | ---------------------------------------------------------------------------------------------- |
| **Core Product**   | **Intelligent Automation (SaaS)** | The marketable product is the AI Agent System itself, pivoting to scalable B2B services        |
| **Content Engine** | **High-Fidelity Content**         | Consistently generate sophisticated, on-brand content that builds community and drives traffic |
| **Technology**     | **Serverless Scalability**        | Utilize cost-effective, Google-Native stack with pay-per-use pricing for maximum runway        |

### Architecture Principles

1. **API-First Design**: Headless CMS with RESTful APIs
2. **Static Site Generation**: Optimal performance through pre-built pages
3. **AI Automation**: Autonomous content creation and management
4. **Component Modularity**: Reusable, testable components
5. **Production Ready**: Scalable, secure, and monitorable
6. **Cost Optimization**: Zero-cost local AI (Ollama) + cloud fallback
7. **Serverless First**: Google Cloud Run for auto-scaling

### Brand Standards

**Tone Mandate:** **Positive, Educational, and Authentically Futuristic**

- Intelligent and empowering language
- **Forbidden:** All cyberpunk slang ("choom," "jack-in," "preem")
- **Allowed:** Technical analogies ("neural network," "asynchronous data stream")

---

## 🏗️ System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     GLAD LABS PLATFORM                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌────────────┐         ┌──────────────┐      ┌─────────────┐ │
│  │  Public    │────────▶│  Strapi v5   │◀─────│  Oversight  │ │
│  │   Site     │   API   │     CMS      │ API  │    Hub      │ │
│  │ (Next.js)  │         │  (Headless)  │      │  (React)    │ │
│  └────────────┘         └──────┬───────┘      └──────┬──────┘ │
│       │                        │                     │         │
│       │                        │                     │         │
│       ▼                        ▼                     ▼         │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │           AI CO-FOUNDER (FastAPI Backend)               │  │
│  │  ┌────────────────────────────────────────────────┐     │  │
│  │  │  Model Router (Ollama/OpenAI/Anthropic/Gemini)│     │  │
│  │  └────────────────────────────────────────────────┘     │  │
│  │  ┌────────────┬────────────┬──────────┬───────────┐     │  │
│  │  │  Content   │ Financial  │ Market   │Compliance │     │  │
│  │  │   Agent    │   Agent    │ Insight  │  Agent    │     │  │
│  │  └────────────┴────────────┴──────────┴───────────┘     │  │
│  └─────────────────────────────────────────────────────────┘  │
│       │                        │                     │         │
│       ▼                        ▼                     ▼         │
│  ┌────────────┐    ┌─────────────────┐    ┌──────────────┐  │
│  │  Firestore │    │ Google Pub/Sub  │    │ Cloud        │  │
│  │ (Database) │    │ (Message Bus)   │    │ Storage      │  │
│  └────────────┘    └─────────────────┘    └──────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Technology Stack

#### Frontend Layer

| Component         | Technology                       | Port | Status        | Purpose                              |
| ----------------- | -------------------------------- | ---- | ------------- | ------------------------------------ |
| **Public Site**   | Next.js 15 + React 19 + Tailwind | 3000 | ✅ Production | Public-facing website and blog       |
| **Oversight Hub** | React 18 + Material-UI 7         | 3001 | ✅ Production | Admin dashboard for agent management |

#### Backend Layer

| Component         | Technology                      | Port | Status        | Purpose                     |
| ----------------- | ------------------------------- | ---- | ------------- | --------------------------- |
| **Strapi CMS**    | Strapi v5 + SQLite/PostgreSQL   | 1337 | ✅ Production | Headless content management |
| **AI Co-Founder** | Python 3.11 + FastAPI + Uvicorn | 8000 | ✅ Production | Central AI orchestrator     |
| **Content Agent** | Python 3.11 + LangChain         | N/A  | ✅ Production | Content generation pipeline |

#### Infrastructure

| Service                | Provider                                     | Purpose                                 |
| ---------------------- | -------------------------------------------- | --------------------------------------- |
| **Database**           | Google Firestore                             | Real-time task queue & operational data |
| **Message Bus**        | Google Cloud Pub/Sub                         | Async agent communication               |
| **Storage**            | Google Cloud Storage                         | Media files and static assets           |
| **Hosting (Backend)**  | Google Cloud Run                             | Serverless container hosting            |
| **Hosting (Frontend)** | Vercel / Netlify                             | Static site hosting                     |
| **AI Models**          | Ollama (local) + OpenAI + Anthropic + Gemini | Multi-provider AI inference             |

---

## 🔧 Component Design

### 1. Public Site (Next.js)

**Location:** `/web/public-site/`

**Key Features:**

- Server-side rendering (SSR) and static generation (SSG)
- Homepage with featured posts and content grid
- Individual post pages with full markdown rendering
- Category and tag-based content filtering
- SEO optimization with meta tags and Open Graph
- Responsive design with Tailwind CSS

**File Structure:**

```
public-site/
├── pages/
│   ├── index.js         # Homepage
│   ├── posts/[slug].js  # Dynamic post pages
│   ├── category/[slug].js
│   └── _app.js
├── components/
│   ├── Layout.js
│   ├── PostCard.js
│   └── Header.js
├── lib/
│   └── api.js          # Strapi API client
└── styles/
    └── globals.css
```

**API Integration:**

```javascript
// lib/api.js
const API_URL =
  process.env.NEXT_PUBLIC_STRAPI_API_URL || 'http://localhost:1337';

export async function getAllPosts() {
  const res = await fetch(`${API_URL}/api/posts?populate=*`);
  const data = await res.json();
  return data.data;
}

export async function getPostBySlug(slug) {
  const res = await fetch(
    `${API_URL}/api/posts?filters[slug][$eq]=${slug}&populate=*`
  );
  const data = await res.json();
  return data.data[0];
}
```

### 2. Oversight Hub (React)

**Location:** `/web/oversight-hub/`

**Key Features:**

- Real-time system health monitoring
- Task management with CRUD operations
- Model provider configuration
- Cost tracking and metrics
- Social media management suite
- WebSocket real-time updates (planned)

**Architecture:**

```
oversight-hub/
├── src/
│   ├── components/
│   │   ├── dashboard/
│   │   │   ├── SystemHealthDashboard.jsx
│   │   │   └── CostMetricsDashboard.jsx
│   │   ├── tasks/
│   │   │   └── TaskManagement.jsx
│   │   ├── models/
│   │   │   └── ModelManagement.jsx
│   │   └── social/
│   │       └── SocialMediaManagement.jsx
│   ├── services/
│   │   └── api.js
│   └── App.js
└── package.json
```

**State Management:**

- Zustand for global state
- Local state with React hooks
- API client with Axios

### 3. Strapi v5 CMS

**Location:** `/cms/strapi-v5-backend/`

**Content Types:**

1. **Posts** (`api::post.post`)
   - title, slug, content (markdown)
   - excerpt, featured, coverImage
   - category (relation), tags (relation)
   - SEO metadata

2. **Categories** (`api::category.category`)
   - name, slug, description
   - posts (relation)

3. **Tags** (`api::tag.tag`)
   - name, slug
   - posts (relation)

4. **Pages** (`api::page.page`)
   - title, slug, content
   - SEO metadata

**Configuration:**

```javascript
// config/database.ts
export default ({ env }) => ({
  connection: {
    client: env('DATABASE_CLIENT', 'sqlite'),
    connection:
      env('DATABASE_CLIENT') === 'sqlite'
        ? {
            filename: path.join(__dirname, '..', '.tmp/data.db'),
          }
        : {
            host: env('DATABASE_HOST', '127.0.0.1'),
            port: env.int('DATABASE_PORT', 5432),
            database: env('DATABASE_NAME', 'strapi'),
            user: env('DATABASE_USERNAME', 'strapi'),
            password: env('DATABASE_PASSWORD', 'strapi'),
            ssl: env.bool('DATABASE_SSL', false),
          },
  },
});
```

### 4. AI Co-Founder (FastAPI)

**Location:** `/src/cofounder_agent/`

**Core Components:**

1. **Main API** (`main.py`)
   - FastAPI application
   - REST endpoints (50+ endpoints)
   - WebSocket support (planned)
   - CORS middleware

2. **Model Router** (`services/model_router.py`)
   - Multi-provider AI orchestration
   - Cost-aware model selection
   - Automatic fallback logic

3. **Multi-Agent Orchestrator** (`multi_agent_orchestrator.py`)
   - Agent lifecycle management
   - Task distribution
   - Result aggregation

**Architecture Pattern:**

```python
# main.py
from fastapi import FastAPI, HTTPException
from services.model_router import ModelRouter
from multi_agent_orchestrator import AgentOrchestrator

app = FastAPI(title="GLAD Labs AI Co-Founder API")
router = ModelRouter()
orchestrator = AgentOrchestrator(model_router=router)

@app.post("/tasks")
async def create_task(task: TaskCreate):
    """Create new task for agent processing."""
    result = await orchestrator.dispatch_task(
        agent_type=task.agent_type,
        task_data=task.data
    )
    return {"taskId": result.id, "status": "queued"}
```

### 5. Specialized Agents

#### Content Agent (`/src/agents/content_agent/`)

**Pipeline Stages:**

1. **Research** - Topic exploration and keyword analysis
2. **Generation** - AI-powered content creation
3. **QA Review** - Quality assurance and refinement
4. **Image Sourcing** - Pexels API integration
5. **Publishing** - Strapi CMS publication

**Agent Class:**

```python
class ContentAgent:
    def __init__(self, model_router: ModelRouter):
        self.router = model_router
        self.qa_threshold = 7.5  # Minimum quality score

    async def generate_content(self, topic: str) -> BlogPost:
        # 1. Research phase
        keywords = await self.research_topic(topic)

        # 2. Generation phase
        content = await self.generate_draft(topic, keywords)

        # 3. QA review phase
        while self.qa_score(content) < self.qa_threshold:
            content = await self.refine_content(content)

        # 4. Image processing
        images = await self.source_images(content)

        # 5. Publish to Strapi
        post_id = await self.publish_to_strapi(content, images)

        return BlogPost(strapiId=post_id, title=content.title)
```

#### Financial Agent (Phase 2)

**Capabilities:**

- Expense tracking via Mercury Bank API
- GCP billing analysis
- Burn rate calculation
- Budget alerts

#### Market Insight Agent (Phase 2)

**Capabilities:**

- Trend analysis
- Competitor research
- Content topic suggestions
- Proactive task generation

#### Compliance Agent (Phase 3)

**Capabilities:**

- Security audits
- Policy enforcement
- Risk assessment
- Audit trail generation

---

## 📊 Data Architecture

### Strapi v5 Database Schema

```sql
-- Posts table
CREATE TABLE posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id VARCHAR(255) UNIQUE NOT NULL,
  title VARCHAR(255) NOT NULL,
  slug VARCHAR(255) UNIQUE NOT NULL,
  content TEXT,
  excerpt TEXT,
  featured BOOLEAN DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  published_at DATETIME
);

-- Categories table
CREATE TABLE categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id VARCHAR(255) UNIQUE NOT NULL,
  name VARCHAR(255) NOT NULL,
  slug VARCHAR(255) UNIQUE NOT NULL,
  description TEXT
);

-- Tags table
CREATE TABLE tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id VARCHAR(255) UNIQUE NOT NULL,
  name VARCHAR(255) NOT NULL,
  slug VARCHAR(255) UNIQUE NOT NULL
);

-- Relationship tables
CREATE TABLE posts_categories_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  category_id INTEGER NOT NULL,
  FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
  FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

CREATE TABLE posts_tags_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  tag_id INTEGER NOT NULL,
  FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);
```

### Firebase Firestore Collections

#### 1. `tasks` Collection

**Document Structure:**

```typescript
{
  taskId: string;           // Document ID (UUID)
  agentId: string;          // "content-agent", "financial-agent", etc.
  taskName: string;         // Human-readable task name
  status: "queued" | "in_progress" | "completed" | "failed";
  createdAt: Timestamp;
  updatedAt: Timestamp;
  metadata: {
    topic?: string;
    priority: 1 | 2 | 3;    // 1=High, 2=Medium, 3=Low
    strapiId?: number;
    trigger: "manual" | "scheduled" | "api" | "proactive";
  };
}
```

#### 2. `agent_logs` Collection

**Document Structure:**

```typescript
{
  logId: string;            // Document ID
  agentId: string;
  taskId: string;
  level: "INFO" | "WARNING" | "ERROR" | "DEBUG";
  message: string;
  timestamp: Timestamp;
  payload: {
    step: string;           // "Research", "Generation", "QA", "Publishing"
    durationMs: number;
    error?: string;
    metadata?: Record<string, any>;
  };
}
```

#### 3. `content_metrics` Collection

**Document Structure:**

```typescript
{
  contentId: string; // Document ID
  strapiId: number;
  title: string;
  type: 'blog_post' | 'page';
  status: 'published' | 'draft' | 'archived';
  publishedAt: Timestamp;
  url: string;
  performance: {
    views: number;
    engagement: number;
    socialShares: number;
  }
  metadata: {
    agentVersion: string;
    generationTimeMs: number;
    aiModel: string; // "gpt-4", "mistral", "claude-3-5-sonnet"
    costUsd: number;
  }
}
```

### Python Data Models

#### BlogPost Model

```python
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class BlogPost(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    raw_content: Optional[str] = None
    excerpt: Optional[str] = None
    slug: Optional[str] = None
    primary_keyword: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = []
    images: List[ImageDetails] = []
    body_content_blocks: Optional[List[Dict[str, Any]]] = None
    strapi_id: Optional[int] = None
    strapi_url: Optional[str] = None
    qa_feedback: List[str] = []
```

#### ImageDetails Model

```python
class ImageDetails(BaseModel):
    query: Optional[str] = None
    source: str = "pexels"
    path: Optional[str] = None
    public_url: Optional[str] = None
    alt_text: Optional[str] = None
    caption: Optional[str] = None
    description: Optional[str] = None
    strapi_image_id: Optional[int] = None
```

### Entity Relationships

```
┌──────────┐       ┌────────────┐       ┌──────┐
│  Posts   │──1:M──│ Categories │       │ Tags │
└────┬─────┘       └────────────┘       └───┬──┘
     │                                       │
     └────────────── M:M ───────────────────┘

Posts -> Category (Many-to-One)
Posts <-> Tags (Many-to-Many)
```

---

## 🔌 API Design

### Strapi v5 REST API

#### Base URL

```
http://localhost:1337/api    # Development
https://api.gladlabs.com/api # Production
```

#### Authentication

**Public Access:**

```bash
# No authentication required for public endpoints
GET /api/posts?populate=*
```

**Admin Access:**

```bash
# JWT token required
POST /api/posts
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

#### Endpoints

**Posts:**

```bash
# Get all posts with relations
GET /api/posts?populate=*

# Get featured posts
GET /api/posts?filters[featured][$eq]=true&populate=*

# Get posts by category
GET /api/posts?filters[category][slug][$eq]=ai-machine-learning&populate=*

# Get single post by slug
GET /api/posts?filters[slug][$eq]=post-slug&populate=*

# Create post
POST /api/posts
Content-Type: application/json
{
  "data": {
    "title": "New Post",
    "slug": "new-post",
    "content": "# Content here",
    "featured": true
  }
}

# Update post
PUT /api/posts/:id
# Delete post
DELETE /api/posts/:id
```

**Categories:**

```bash
GET /api/categories
POST /api/categories
PUT /api/categories/:id
DELETE /api/categories/:id
```

**Tags:**

```bash
GET /api/tags
POST /api/tags
PUT /api/tags/:id
DELETE /api/tags/:id
```

**Media Upload:**

```bash
POST /api/upload
Content-Type: multipart/form-data
files: [binary data]
```

### AI Co-Founder API (FastAPI)

#### Base URL

```
http://localhost:8000    # Development
https://api.gladlabs.com # Production
```

#### API Documentation

```
http://localhost:8000/docs      # Swagger UI
http://localhost:8000/redoc     # ReDoc
```

#### Core Endpoints

**Health & Status:**

```bash
GET /health                     # Health check
GET /                          # API info
GET /status                    # System status
```

**Model Management:**

```bash
GET /models/status             # All provider status
GET /models/usage              # Usage statistics
POST /models/test              # Test model with prompt
POST /models/{provider}/toggle # Toggle provider on/off
```

**Task Management:**

```bash
GET /tasks                     # List all tasks
POST /tasks                    # Create new task
GET /tasks/{id}                # Get task details
PUT /tasks/{id}                # Update task
DELETE /tasks/{id}             # Delete task
POST /tasks/bulk               # Bulk operations
```

**Agent Operations:**

```bash
GET /agents                    # List all agents
GET /agents/{id}               # Get agent details
POST /agents/{id}/execute      # Execute agent task
GET /agents/{id}/logs          # Get agent logs
```

**Content Generation:**

```bash
POST /content/generate         # Generate blog post
GET /content/history           # Generation history
POST /content/regenerate       # Regenerate content
```

**Social Media (Phase 2):**

```bash
GET /social/platforms          # List platforms
POST /social/connect           # Connect platform
POST /social/generate          # Generate content
POST /social/post              # Publish to platform
POST /social/cross-post        # Multi-platform post
GET /social/analytics          # Get analytics
GET /social/trending           # Get trending topics
```

**System Metrics:**

```bash
GET /metrics/summary           # System overview
GET /metrics/performance       # Performance metrics
GET /metrics/costs             # Cost tracking
GET /system/alerts             # Active alerts
```

#### Request/Response Examples

**Create Task:**

```bash
POST /tasks
Content-Type: application/json

{
  "agent_type": "content",
  "task_name": "Generate AI Ethics Post",
  "priority": 1,
  "metadata": {
    "topic": "AI Ethics in Healthcare",
    "category": "AI & ML",
    "tags": ["ethics", "healthcare", "ai"]
  }
}

# Response
{
  "taskId": "abc123",
  "status": "queued",
  "message": "Task created successfully",
  "estimatedCompletionTime": "2025-10-15T14:30:00Z"
}
```

**Test Model:**

```bash
POST /models/test
Content-Type: application/json

{
  "provider": "ollama",
  "model": "mistral",
  "prompt": "What is machine learning?",
  "max_tokens": 100
}

# Response
{
  "provider": "ollama",
  "model": "mistral",
  "response": "Machine learning is a subset of artificial intelligence...",
  "tokensUsed": 85,
  "costUsd": 0.00,
  "latencyMs": 234
}
```

---

## 🤖 AI Model Architecture

### Model Router Design

**Multi-Provider Strategy:**

```python
class ModelProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"

class ModelTier(str, Enum):
    FREE = "free"          # $0.00 - Ollama local
    BUDGET = "budget"      # $0.035-0.60/1M tokens
    STANDARD = "standard"  # $2-3/1M tokens
    PREMIUM = "premium"    # $10-15/1M tokens
    FLAGSHIP = "flagship"  # $30-75/1M tokens

class ModelRouter:
    def route_request(self, task_type: str) -> dict:
        """Route to optimal model based on task complexity."""
        if self.use_ollama:
            return self._route_to_ollama(task_type)
        else:
            return self._route_to_cloud(task_type)
```

### Model Selection Matrix

| Task Type    | Local (Ollama) | Cloud (API)       | Cost Comparison   |
| ------------ | -------------- | ----------------- | ----------------- |
| **Simple**   | phi (2.7B)     | gpt-4o-mini       | $0.00 vs $0.15/1M |
| **General**  | mistral (7B)   | gpt-4o            | $0.00 vs $2.50/1M |
| **Code**     | codellama (7B) | claude-3-5-sonnet | $0.00 vs $3.00/1M |
| **Complex**  | mixtral (8x7B) | gpt-4-turbo       | $0.00 vs $10/1M   |
| **Critical** | llama2:70b     | claude-opus-4     | $0.00 vs $15/1M   |

### Provider Configuration

**Environment Variables:**

```bash
# Enable local AI (zero cost)
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434

# Cloud providers
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...

# Model preferences
DEFAULT_MODEL=mistral
FALLBACK_MODEL=gpt-4o-mini
```

### Cost Optimization

**Monthly Cost Comparison (100K tokens/day):**

| Provider   | Model             | Cost/Month | Annual |
| ---------- | ----------------- | ---------- | ------ |
| **Ollama** | mistral           | **$0**     | **$0** |
| OpenAI     | gpt-4o-mini       | $4.50      | $54    |
| Gemini     | gemini-1.5-flash  | $10.50     | $126   |
| OpenAI     | gpt-4o            | $75        | $900   |
| Anthropic  | claude-3-5-sonnet | $90        | $1,080 |

**Savings: $54 - $1,080/year with Ollama**

### Performance Benchmarks

| Hardware            | Model          | Tokens/Sec | Latency | Memory   |
| ------------------- | -------------- | ---------- | ------- | -------- |
| Intel i7 + RTX 3060 | mistral (7B)   | 80-120     | ~200ms  | 6-8 GB   |
| Intel i7 + RTX 3060 | mixtral (8x7B) | 40-60      | ~400ms  | 10-12 GB |
| Apple M2 Max        | mistral (7B)   | 100-150    | ~150ms  | 8-10 GB  |
| Cloud API           | gpt-4o         | N/A        | ~300ms  | N/A      |

---

## 🔐 Security Architecture

### Authentication & Authorization

**Strapi Admin:**

- JWT-based authentication
- Role-based access control (RBAC)
- Session management

**API Access:**

- Bearer token authentication
- API key management
- Rate limiting

**AI Co-Founder API:**

- API key authentication
- Request signing (planned)
- IP whitelisting (production)

### Data Protection

**Input Validation:**

```python
from pydantic import BaseModel, validator

class TaskCreate(BaseModel):
    task_name: str
    agent_type: str

    @validator('task_name')
    def validate_task_name(cls, v):
        if len(v) < 3 or len(v) > 200:
            raise ValueError('Task name must be 3-200 characters')
        return v
```

**SQL Injection Protection:**

- ORM-based queries (Strapi v5 ORM)
- Parameterized queries
- No raw SQL execution

**XSS Prevention:**

- Content sanitization
- Output encoding
- CSP headers

**HTTPS Enforcement:**

```python
# Production middleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

if settings.ENVIRONMENT == "production":
    app.add_middleware(HTTPSRedirectMiddleware)
```

### Access Control Matrix

| Resource        | Public | Admin | Agent |
| --------------- | ------ | ----- | ----- |
| Read Posts      | ✅     | ✅    | ✅    |
| Create Posts    | ❌     | ✅    | ✅    |
| Update Posts    | ❌     | ✅    | ✅    |
| Delete Posts    | ❌     | ✅    | ❌    |
| System Metrics  | ❌     | ✅    | ✅    |
| User Management | ❌     | ✅    | ❌    |

---

## ⚡ Performance Design

### Frontend Performance

**Static Site Generation (Next.js):**

```javascript
// pages/posts/[slug].js
export async function getStaticProps({ params }) {
  const post = await getPostBySlug(params.slug);
  return {
    props: { post },
    revalidate: 60, // ISR: Revalidate every 60 seconds
  };
}

export async function getStaticPaths() {
  const posts = await getAllPosts();
  return {
    paths: posts.map((post) => ({ params: { slug: post.slug } })),
    fallback: 'blocking',
  };
}
```

**Image Optimization:**

```javascript
import Image from 'next/image';

<Image
  src={post.coverImage}
  alt={post.title}
  width={800}
  height={400}
  placeholder="blur"
  loading="lazy"
/>;
```

**Code Splitting:**

- Automatic route-based splitting
- Dynamic imports for heavy components
- Tree shaking for unused code

### Backend Performance

**Database Optimization:**

```sql
-- Indexes for fast queries
CREATE INDEX idx_posts_slug ON posts(slug);
CREATE INDEX idx_posts_published ON posts(published_at);
CREATE INDEX idx_posts_featured ON posts(featured);
```

**API Caching:**

```python
from functools import lru_cache
from datetime import timedelta

@lru_cache(maxsize=128)
@cached(ttl=timedelta(minutes=5))
async def get_all_posts():
    """Cache posts for 5 minutes."""
    return await Post.objects.all()
```

**Query Optimization:**

```python
# Efficient relationship loading
posts = await Post.objects.select_related('category').prefetch_related('tags').all()
```

### Performance Targets

| Metric                     | Target | Current |
| -------------------------- | ------ | ------- |
| **First Contentful Paint** | <1.5s  | 1.2s    |
| **Time to Interactive**    | <3s    | 2.8s    |
| **Lighthouse Score**       | 90+    | 95      |
| **API Response Time**      | <200ms | 150ms   |
| **Database Query Time**    | <50ms  | 35ms    |

---

## 🚀 Deployment Architecture

### Development Environment

```yaml
Local Development:
  Frontend:
    - Next.js dev server: http://localhost:3000
    - React dev server: http://localhost:3001
  Backend:
    - Strapi: http://localhost:1337
    - FastAPI: http://localhost:8000
  Database:
    - SQLite: .tmp/data.db
    - Firestore Emulator: localhost:8080
  AI:
    - Ollama: http://localhost:11434
```

### Production Architecture

```yaml
Production Stack:
  Frontend:
    - Next.js: Vercel (SSG + ISR)
    - React: Netlify or Vercel
  Backend:
    - Strapi: Google Cloud Run
    - FastAPI: Google Cloud Run
  Database:
    - PostgreSQL: Google Cloud SQL
    - Firestore: Production instance
  Storage:
    - Media: Google Cloud Storage
    - CDN: Google Cloud CDN
  Monitoring:
    - Logs: Google Cloud Logging
    - Metrics: Google Cloud Monitoring
    - Alerts: Google Cloud Alerting
```

### Deployment Pipeline

```yaml
CI/CD Workflow:
  1. Code Push:
    - Developer pushes to GitHub
    - GitHub Actions triggered

  2. Build & Test:
    - Run unit tests
    - Run integration tests
    - Build Docker images
    - Run security scans

  3. Staging Deployment:
    - Deploy to staging environment
    - Run E2E tests
    - Manual QA review

  4. Production Deployment:
    - Deploy to production
    - Health check verification
    - Gradual traffic shift
    - Rollback if issues detected

  5. Post-Deployment:
    - Monitor metrics
    - Check error rates
    - Verify performance
```

### Scaling Strategy

**Horizontal Scaling:**

```yaml
Google Cloud Run:
  min_instances: 0 # Scale to zero when idle
  max_instances: 10 # Scale up to 10 instances
  concurrency: 80 # Requests per instance
  cpu: 1 # CPU per instance
  memory: 512Mi # Memory per instance
  timeout: 300s # Request timeout
```

**Database Scaling:**

- Read replicas for content distribution
- Connection pooling
- Query result caching

**CDN Integration:**

- Global content delivery
- Edge caching
- Image optimization

---

## 💻 Coding Standards

### Python Standards

**Style Guide:**

- PEP 8 compliance
- Black formatter (line length: 100)
- isort for import sorting
- Type hints required

**Example:**

```python
from typing import List, Optional
from pydantic import BaseModel

class AgentConfig(BaseModel):
    """Configuration for AI agents."""

    agent_id: str
    model_provider: str
    max_retries: int = 3
    timeout_seconds: int = 60

    def validate_config(self) -> bool:
        """Validate configuration parameters."""
        if self.max_retries < 1 or self.max_retries > 10:
            raise ValueError("max_retries must be between 1 and 10")
        return True
```

**Docstrings:**

```python
def generate_content(topic: str, max_tokens: int = 1000) -> str:
    """Generate blog content on specified topic.

    Args:
        topic: The subject matter for content generation
        max_tokens: Maximum number of tokens to generate

    Returns:
        Generated content as markdown string

    Raises:
        ValueError: If topic is empty or invalid
        APIError: If AI model request fails
    """
    pass
```

### JavaScript/TypeScript Standards

**Style Guide:**

- ESLint + Prettier
- 2-space indentation
- Single quotes
- Semicolons required

**Example:**

```javascript
// Good
const fetchPosts = async (filters = {}) => {
  try {
    const response = await fetch(`${API_URL}/posts`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Failed to fetch posts:', error);
    throw error;
  }
};
```

### React Component Standards

**Functional Components:**

```javascript
import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';

const PostCard = ({ post, onDelete }) => {
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await onDelete(post.id);
    } catch (error) {
      console.error('Delete failed:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="post-card">
      <h3>{post.title}</h3>
      <p>{post.excerpt}</p>
      <button onClick={handleDelete} disabled={isDeleting}>
        {isDeleting ? 'Deleting...' : 'Delete'}
      </button>
    </div>
  );
};

PostCard.propTypes = {
  post: PropTypes.shape({
    id: PropTypes.number.isRequired,
    title: PropTypes.string.isRequired,
    excerpt: PropTypes.string,
  }).isRequired,
  onDelete: PropTypes.func.isRequired,
};

export default PostCard;
```

### File Naming Conventions

```
Python:
  - snake_case for files: content_agent.py
  - PascalCase for classes: ContentAgent
  - snake_case for functions: generate_content()

JavaScript/TypeScript:
  - PascalCase for components: PostCard.jsx
  - camelCase for utilities: apiClient.js
  - kebab-case for pages: blog-post.js

CSS:
  - kebab-case: post-card.css
  - BEM methodology: post-card__title--featured
```

### Git Commit Standards

**Format:**

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**

- feat: New feature
- fix: Bug fix
- docs: Documentation only
- style: Formatting changes
- refactor: Code restructuring
- test: Adding tests
- chore: Maintenance

**Example:**

```
feat(social): Add Instagram cross-posting support

Implemented Instagram API integration for the social media agent.
Added image optimization and hashtag suggestions.

Closes #123
```

---

## 🔄 Development Workflows

### Local Development

**1. Setup:**

```powershell
# Clone repository
git clone https://github.com/gladlabs/glad-labs-website.git
cd glad-labs-website

# Install dependencies
.\scripts\setup-dependencies.ps1

# Start all services
.\scripts\start-system.ps1
```

**2. Development:**

```powershell
# Start individual services
cd cms/strapi-v5-backend
npm run develop

cd web/public-site
npm run dev

cd web/oversight-hub
npm start

cd src/cofounder_agent
python -m uvicorn main:app --reload
```

**3. Testing:**

```powershell
# Run tests
npm test                    # Frontend tests
pytest                      # Backend tests
.\scripts\quick-test-api.ps1  # API smoke tests
```

### Feature Development Workflow

**1. Create Branch:**

```bash
git checkout -b feature/new-agent-capability
```

**2. Develop:**

- Write code following standards
- Add unit tests
- Update documentation

**3. Test:**

```bash
# Run all tests
pytest tests/
npm test

# Check code quality
black src/
flake8 src/
eslint web/
```

**4. Commit:**

```bash
git add .
git commit -m "feat(agent): Add new capability X"
```

**5. Push & PR:**

```bash
git push origin feature/new-agent-capability
# Create Pull Request on GitHub
```

**6. Review:**

- Code review by team
- CI/CD pipeline passes
- Manual testing in staging

**7. Merge:**

- Squash and merge to main
- Delete feature branch
- Deploy to production

### Testing Strategy

**Unit Tests:**

```python
# tests/test_content_agent.py
import pytest
from agents.content_agent import ContentAgent

def test_content_generation():
    agent = ContentAgent()
    result = agent.generate_content("AI Ethics")
    assert result.title is not None
    assert len(result.content) > 100
    assert result.qa_score >= 7.5
```

**Integration Tests:**

```python
# tests/integration/test_full_pipeline.py
async def test_content_pipeline():
    # Create task
    task = await create_task("Generate blog post")

    # Agent processes task
    result = await process_task(task.id)

    # Verify publication
    post = await get_strapi_post(result.strapi_id)
    assert post.published_at is not None
```

**E2E Tests:**

```javascript
// tests/e2e/post-creation.spec.js
test('creates and publishes blog post', async () => {
  await page.goto('http://localhost:3001');
  await page.click('#create-task-btn');
  await page.fill('#task-topic', 'AI Ethics');
  await page.click('#submit-btn');

  // Wait for completion
  await page.waitForSelector('.task-completed');

  // Verify on public site
  await page.goto('http://localhost:3000');
  expect(await page.textContent('h1')).toContain('AI Ethics');
});
```

---

## 📚 Related Documents

- [Setup Guide](./01-SETUP_GUIDE.md) - Installation and configuration
- [Developer Journal](./05-DEVELOPER_JOURNAL.md) - Chronological changelog
- [API Reference](./04-API_REFERENCE.md) - Comprehensive API documentation
- [Architecture Diagrams](./ARCHITECTURE.md) - Visual system diagrams

---

<div align="center">

**[← Back to Documentation Hub](./00-README.md)**

Last Updated: October 15, 2025

</div>
