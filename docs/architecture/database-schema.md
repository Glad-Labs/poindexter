# Poindexter Database Schema

This document defines the database schema Poindexter runs on. The
entire system uses PostgreSQL 16 with the pgvector extension through
asyncpg. There is no ORM — queries are hand-written SQL, and schema
changes are tracked as migration files in
`src/cofounder_agent/services/migrations/` (the 169 historical files
were squashed into `0000_baseline.py` on 2026-05-08; new migrations
use a UTC timestamp prefix per poindexter#378).

**Last Updated:** 2026-05-23
**Version:** 0.1.x (alpha)
**Architecture:** PostgreSQL 16 + pgvector + FastAPI + asyncpg background workers

---

## **PostgreSQL Database Module Overview**

The system uses a modular database architecture with 6 specialized database modules, each handling domain-specific operations:

1. **UsersDatabase** - User accounts, OAuth, authentication
2. **TasksDatabase** - Task CRUD, filtering, status management
3. **ContentDatabase** - Posts, articles, publishing metadata
4. **AdminDatabase** - Logging, financial tracking, system health
5. **WritingStyleDatabase** - Writing samples for RAG-based style matching
6. **EmbeddingsDatabase** - pgvector storage and cosine-similarity search (writer-segregated across `brain`, `audit`, `posts`, `memory`, `claude_sessions`, `issues`, `samples`)

All six are reached through `services/database_service.py::DatabaseService` — callers don't import them directly. See [`../reference/services.md`](../reference/services) for service-level details and [GH-106](https://github.com/Glad-Labs/poindexter/issues/106) for the stale-embedding retention policy.

---

## **Core Database Tables**

### 1. `users` Table

Stores user accounts and authentication data.

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(500),
    oauth_provider VARCHAR(50),
    oauth_id VARCHAR(255),
    full_name VARCHAR(255),
    avatar_url TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_oauth UNIQUE(oauth_provider, oauth_id)
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_username ON users(username);
```

### 2. `pipeline_tasks` Table

Stores the content-generation task queue. The Prefect-orchestrated flow
(`services/flows/content_generation.py`) claims rows via `SELECT ... FOR
UPDATE SKIP LOCKED`; each task carries a `template_slug` that picks the
LangGraph template (`canonical_blog` / `dev_diary`) to run.

```sql
CREATE TABLE pipeline_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    template_slug VARCHAR(100) NOT NULL,  -- canonical_blog / dev_diary / ...
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    priority VARCHAR(20) DEFAULT 'medium',
    task_type VARCHAR(50),
    task_metadata JSONB DEFAULT '{}',
    quality_score FLOAT,
    auto_cancelled_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pipeline_tasks_user_id ON pipeline_tasks(user_id);
CREATE INDEX idx_pipeline_tasks_status ON pipeline_tasks(status);
CREATE INDEX idx_pipeline_tasks_created_at ON pipeline_tasks(created_at DESC);
CREATE INDEX idx_pipeline_tasks_template_slug ON pipeline_tasks(template_slug);
```

### 3. `posts` Table

Stores published blog posts and the awaiting-approval queue. The
canonical content table — the older `content` example was never the
production shape.

```sql
CREATE TABLE posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    slug VARCHAR(500) NOT NULL UNIQUE,
    content TEXT,
    excerpt VARCHAR(1000),
    cover_image_url TEXT,
    featured BOOLEAN DEFAULT FALSE,
    status VARCHAR(50) DEFAULT 'draft',  -- draft / awaiting_approval / published / archived
    quality_score FLOAT DEFAULT 0,
    seo_title VARCHAR(255),
    seo_description VARCHAR(500),
    seo_keywords TEXT,
    author VARCHAR(255),
    tags TEXT[],
    category VARCHAR(100),
    view_count INTEGER DEFAULT 0,
    published_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_posts_slug ON posts(slug);
CREATE INDEX idx_posts_status ON posts(status);
CREATE INDEX idx_posts_published_at ON posts(published_at DESC);
```

### 4. `audit_log` Table

Canonical historical record — every significant pipeline transition,
QA decision, writer-fallback event, etc. lands here. Queried by
`routes/pipeline_events_routes.py` despite the legacy URL prefix.

```sql
CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(100) NOT NULL,
    user_id UUID REFERENCES users(id),
    severity VARCHAR(20) DEFAULT 'info',  -- info / warning / error / critical
    details JSONB DEFAULT '{}',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_log_event_type ON audit_log(event_type);
CREATE INDEX idx_audit_log_created_at ON audit_log(created_at DESC);
```

### 5. `writing_samples` Table

Stores user writing samples for RAG-based style matching.

```sql
CREATE TABLE writing_samples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500),
    content TEXT NOT NULL,
    embedding bytea,
    style_attributes JSONB,
    quality_score FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_writing_samples_user_id ON writing_samples(user_id);
CREATE INDEX idx_writing_samples_created_at ON writing_samples(created_at DESC);
```

### 1. `BlogPost` Model (Python)

Used by content agents during the creation process.

```python
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
    qa_feedback: List[str] = []
```

### 2. `ImageDetails` Model (Python)

Represents images processed by the content agent.

```python
class ImageDetails(BaseModel):
    query: Optional[str] = None
    source: str = "pexels"  # Default to Pexels
    path: Optional[str] = None  # Local path or GCS blob name
    public_url: Optional[str] = None
    alt_text: Optional[str] = None
    caption: Optional[str] = None
    description: Optional[str] = None
```

---

## **API Endpoints Reference**

All content endpoints are served by the FastAPI backend at `http://localhost:8002`.
See the live OpenAPI spec at `/api/openapi.json` for the full endpoint catalog.

Key content routes:

- **Tasks**: `/api/tasks` (GET, POST, PUT, DELETE)
- **Content**: `/api/content` (GET, POST, PUT)
- **Posts**: `/api/posts` (GET, POST, PUT, DELETE)
- **Categories**: `/api/categories` (GET, POST, PUT, DELETE)

---

**Maintainer:** Poindexter is built and maintained by [Glad Labs LLC](https://www.gladlabs.io).
For questions, open an issue on the [public repo](https://github.com/Glad-Labs/poindexter).
