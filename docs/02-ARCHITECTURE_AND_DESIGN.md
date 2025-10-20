# 02 - Architecture & Design

> **System Architecture, Component Design, and Technical Specifications**
>
> Understand how GLAD Labs is built: architecture diagrams, component design, data models, and design principles.

**Reading Time**: 20 minutes | **For**: Developers, architects, DevOps | **Prerequisite**: [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) | **Next**: [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)

---

## 🎯 Strategic Pillars

### Core Mission

Build the **most efficient, automated, solo-founded digital firm** by fusing:

1. High-quality autonomous content creation
2. Intelligent, conversational AI Co-Founder
3. Comprehensive business operations management

### Three Strategic Pillars

| Pillar             | Focus                         | Goal                                       |
| ------------------ | ----------------------------- | ------------------------------------------ |
| **Core Product**   | Intelligent Automation (SaaS) | Scalable B2B AI services                   |
| **Content Engine** | High-Fidelity Content         | Sophisticated, on-brand content at scale   |
| **Technology**     | Serverless Scalability        | Cost-effective, pay-per-use infrastructure |

### Architecture Principles

✅ **API-First Design** - Headless CMS with RESTful APIs  
✅ **Static Site Generation** - Optimal performance through pre-built pages  
✅ **AI Automation** - Autonomous content creation and management  
✅ **Component Modularity** - Reusable, testable components  
✅ **Production Ready** - Scalable, secure, and monitorable  
✅ **Cost Optimization** - Free local AI (Ollama) + cloud fallback  
✅ **Serverless First** - Google Cloud Run for auto-scaling

---

## 🏗️ System Architecture

### High-Level System Design

```
┌─────────────────────────────────────────────────────────┐
│              GLAD LABS PLATFORM                          │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌────────────┐    ┌──────────────┐    ┌────────────┐  │
│  │  Public    │◀──▶│   Strapi     │◀──▶│ Oversight  │  │
│  │   Site     │API │     CMS      │API │    Hub     │  │
│  │ (Next.js)  │    │   (v5)       │    │  (React)   │  │
│  └────────────┘    └──────┬───────┘    └────────────┘  │
│                           │                              │
│  ┌─────────────────────────────────────────────────────┐ │
│  │     AI Co-Founder (FastAPI Backend)                 │ │
│  │  ┌───────────────────────────────────────────────┐  │ │
│  │  │ Model Router (Ollama/OpenAI/Anthropic)       │  │ │
│  │  └───────────────────────────────────────────────┘  │ │
│  │  ┌─────────┬─────────┬──────────┬──────────────┐  │ │
│  │  │Content  │Financial│  Market  │ Compliance   │  │ │
│  │  │ Agent   │ Agent   │ Insight  │  Agent       │  │ │
│  │  └─────────┴─────────┴──────────┴──────────────┘  │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌──────────────┬────────────────────────────────────┐  │
│  │  Database    │  Infrastructure                    │  │
│  │  Firestore   │  Google Cloud Storage & Run        │  │
│  │  PostgreSQL  │  Railway (Backend)                 │  │
│  └──────────────┴────────────────────────────────────┘  │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

### Service Topology

| Service           | Technology                    | Port | Status        | Purpose                           |
| ----------------- | ----------------------------- | ---- | ------------- | --------------------------------- |
| **Public Site**   | Next.js 15 + React 19         | 3000 | ✅ Production | Public website & content delivery |
| **Oversight Hub** | React 18 + Material-UI        | 3001 | ✅ Production | Admin dashboard & management      |
| **Strapi CMS**    | Strapi v5 + SQLite/PostgreSQL | 1337 | ✅ Production | Headless content management       |
| **AI Co-Founder** | Python 3.12 + FastAPI         | 8000 | ✅ Production | Central AI orchestrator           |

---

## 🔧 Component Design

### 1. Public Site (Next.js)

**Location**: `web/public-site/`

**Architecture**:

- Server-side rendering (SSR) + Static site generation (SSG)
- Tailwind CSS for styling
- TypeScript for type safety
- Optimized image loading

**Key Features**:

- Homepage with featured posts
- Individual post pages (dynamic slug-based routing)
- Category and tag filtering
- SEO optimization (meta tags, Open Graph)
- Mobile-responsive design

**File Structure**:

```
public-site/
├── pages/
│   ├── index.js              # Homepage
│   ├── posts/[slug].js       # Individual post pages
│   ├── category/[slug].js    # Category pages
│   └── _app.js               # App wrapper
├── components/               # Reusable React components
├── lib/                      # Utilities and API clients
└── styles/                   # Tailwind CSS
```

**Data Flow**:

```
Next.js ──(fetch via API)──→ Strapi CMS ──→ Render Pages ──→ Browser
```

### 2. Oversight Hub (React Dashboard)

**Location**: `web/oversight-hub/`

**Technology Stack**:

- React 18 with Hooks
- Material-UI 7 for components
- Zustand for state management
- Axios for API calls

**Key Features**:

- System health monitoring
- Task management (CRUD)
- Model provider management
- Cost tracking & metrics
- Real-time dashboards

**Pages**:

- Dashboard (overview)
- Tasks (management)
- Models (provider config)
- Analytics (metrics)
- Settings (configuration)

### 3. Strapi v5 CMS

**Location**: `cms/strapi-main/`

**Technology**:

- Strapi v5 (headless CMS)
- SQLite (local) / PostgreSQL (production)
- GraphQL + REST APIs
- Admin UI built-in

**Content Types** (7 total):

| Type               | Fields                                                     | Purpose           |
| ------------------ | ---------------------------------------------------------- | ----------------- |
| **Posts**          | title, slug, content, excerpt, image, date, category, tags | Blog articles     |
| **Categories**     | name, slug, description, posts                             | Blog organization |
| **Tags**           | name, slug, posts                                          | Content tagging   |
| **Authors**        | name, email, bio, posts                                    | Author profiles   |
| **About**          | title, content, teamMembers                                | About page        |
| **Privacy Policy** | title, content, lastUpdated                                | Legal page        |
| **ContentMetric**  | views, clicks, shares, engagementScore                     | Analytics         |

**Database Relations**:

```
Posts ──M:1──→ Categories
Posts ←──M:M──→ Tags
Posts ──M:1──→ Authors
ContentMetric ──1:1──→ Posts
```

**API Endpoints**:

```
GET    /api/posts              # List all posts
GET    /api/posts/:id          # Get specific post
POST   /api/posts              # Create post (requires auth)
PUT    /api/posts/:id          # Update post (requires auth)
DELETE /api/posts/:id          # Delete post (requires auth)

# Same pattern for: categories, tags, authors, pages, metrics
```

### 4. AI Co-Founder (FastAPI Backend)

**Location**: `src/cofounder_agent/`

**Architecture**:

```python
# main.py
FastAPI App
├── Model Router (multi-provider AI selection)
├── Multi-Agent Orchestrator
│   ├── Content Agent
│   ├── Financial Agent
│   ├── Market Insight Agent
│   ├── Compliance Agent
│   └── Social Media Agent
└── Task Queue & Result Storage
```

**Core Components**:

- **Model Router**: Selects best AI provider (Ollama/OpenAI/Anthropic/Gemini)
- **Agent Orchestrator**: Manages agent lifecycle and coordination
- **Task Manager**: Queue and tracking
- **Integration Clients**: Strapi, social media, etc.

**API Endpoints**:

```
POST   /tasks              # Create new task
GET    /tasks/:id          # Get task status
GET    /tasks              # List tasks
DELETE /tasks/:id          # Cancel task

GET    /models/status      # Model provider status
POST   /models/test        # Test model connectivity
```

### 5. Specialized Agents

**Content Agent** (`src/agents/content_agent/`)

- Research and topic analysis
- Content generation
- QA review and refinement
- Image sourcing via Pexels
- Strapi publishing

**Financial Agent** (`src/agents/financial_agent/`)

- Expense tracking
- Budget analysis
- Cost optimization
- Financial reporting

**Market Insight Agent** (`src/agents/market_insight_agent/`)

- Trend analysis
- Competitor research
- Topic suggestions
- Proactive task generation

**Compliance Agent** (`src/agents/compliance_agent/`)

- Security audits
- Policy enforcement
- Risk assessment
- Audit trail generation

---

## 📊 Data Architecture

### Database Schema

**Strapi Collections** (SQLite/PostgreSQL):

```sql
-- Posts table
posts (
  id, title, slug, content, excerpt, coverImage,
  publishedAt, categoryId, createdAt, updatedAt
)

-- Categories table
categories (
  id, name, slug, description, createdAt, updatedAt
)

-- Tags table
tags (
  id, name, slug, createdAt, updatedAt
)

-- Posts-Categories relationship (M:1)
posts_categories_links

-- Posts-Tags relationship (M:M)
posts_tags_links
```

**Firestore Collections** (Optional - for advanced features):

```
tasks/
├── taskId (document)
├── agentType
├── status
├── result
└── createdAt

agent_logs/
├── logId (document)
├── taskId
├── agentName
├── action
└── timestamp

content_metrics/
├── contentId (document)
├── views, clicks, shares
├── engagementScore
└── updatedAt
```

### Python Data Models

```python
# BlogPost Model
class BlogPost(BaseModel):
    title: str
    content: str
    excerpt: Optional[str]
    slug: str
    coverImage: Optional[str]
    category: Optional[Category]
    tags: List[Tag]
    date: Optional[datetime]

# ImageDetails Model
class ImageDetails(BaseModel):
    query: str
    photographer: str
    url: str
    strapi_image_id: Optional[int]
```

---

## 🔌 API Design

### REST API (Strapi)

**Base URL**: `http://localhost:1337/api` (local) | `https://strapi-prod.up.railway.app/api` (production)

**Authentication**:

```bash
# Generate API token in Strapi Admin
# Settings → API Tokens → Create new token

# Use in requests
Authorization: Bearer YOUR_TOKEN_HERE
```

**Example Queries**:

```bash
# Get all posts with relations
curl http://localhost:1337/api/posts?populate=*

# Filter by category
curl http://localhost:1337/api/posts?filters[category][slug][$eq]=ai&populate=*

# Get single post
curl http://localhost:1337/api/posts?filters[slug][$eq]=my-post&populate=*

# Create post (requires auth)
curl -X POST http://localhost:1337/api/posts \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "title": "New Post",
      "slug": "new-post",
      "content": "Content here"
    }
  }'
```

### GraphQL API (Optional)

**Endpoint**: `http://localhost:1337/graphql`

**Example Query**:

```graphql
query getPosts {
  posts {
    data {
      id
      attributes {
        title
        slug
        content
        category {
          data {
            attributes {
              name
            }
          }
        }
        tags {
          data {
            attributes {
              name
            }
          }
        }
      }
    }
  }
}
```

### FastAPI Backend

**Base URL**: `http://localhost:8000`

**Interactive Docs**: `http://localhost:8000/docs`

**Example Endpoints**:

```bash
# Create task
POST /tasks
{
  "type": "content_creation",
  "parameters": {
    "topic": "AI trends",
    "wordCount": 1000
  }
}

# Get task status
GET /tasks/task-123

# List all tasks
GET /tasks?status=pending

# Get model status
GET /models/status
```

---

## 🔐 Security Architecture

### API Security

- **CORS**: Configured for specific origins
- **Rate Limiting**: Per-endpoint rate limits
- **Authentication**: JWT tokens for admin operations
- **HTTPS**: All production endpoints use SSL/TLS

### Secret Management

- **Environment Variables**: API keys stored in `.env`
- **Secret Manager**: Google Cloud Secret Manager (production)
- **Database**: Credentials never committed to git

### Data Protection

- **At Rest**: Database encryption (PostgreSQL)
- **In Transit**: HTTPS/SSL for all APIs
- **Access Control**: Role-based permissions in Strapi

---

## ⚡ Performance Design

### Frontend Optimization

- **Static Generation**: Pre-built pages for instant loading
- **Image Optimization**: Next.js automatic image optimization
- **Code Splitting**: Lazy load heavy components
- **Caching**: Browser cache + CDN caching

### Backend Optimization

- **Database Indexing**: Indexes on frequently queried fields
- **API Caching**: Strapi caching strategies
- **Async Operations**: Non-blocking task processing
- **Model Routing**: Intelligent AI provider selection

### Monitoring & Metrics

- **Request Logging**: All API requests logged
- **Performance Tracking**: Response times monitored
- **Error Tracking**: Exceptions captured and logged
- **Dashboards**: Real-time metrics visible in Oversight Hub

---

## 📚 Reference Documentation

For more details, see:

- **[Guides](./guides/)** - Step-by-step how-to guides
- **[Reference](./reference/)** - Technical specifications
- **[Troubleshooting](./troubleshooting/)** - Common issues and solutions

---

**← Previous**: [01-SETUP_AND_OVERVIEW.md](./01-SETUP_AND_OVERVIEW.md) | **Next →**: [03-DEPLOYMENT_AND_INFRASTRUCTURE.md](./03-DEPLOYMENT_AND_INFRASTRUCTURE.md)
