# **Data Schemas for Glad Labs Platform**

This document defines the database schemas for the operational system, built on PostgreSQL 15+ with asyncpg for async access patterns.

**Last Updated:** February 11, 2026  
**Version:** 3.0 (PostgreSQL)  
**Architecture:** PostgreSQL 15+ + FastAPI + async task workers

---

## **PostgreSQL Database Module Overview**

The system uses a modular database architecture with 5 specialized database modules, each handling domain-specific operations:

1. **UsersDatabase** - User accounts, OAuth, authentication
2. **TasksDatabase** - Task CRUD, filtering, status management
3. **ContentDatabase** - Posts, articles, publishing metadata
4. **AdminDatabase** - Logging, financial tracking, system health
5. **WritingStyleDatabase** - Writing samples for RAG-based style matching

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

### 2. `tasks` Table

Stores all tasks created by agents and users.

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    priority VARCHAR(20) DEFAULT 'medium',
    task_type VARCHAR(50),
    metadata JSONB DEFAULT '{}',
    assigned_agent VARCHAR(100),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);
```

### 3. `content` Table

Stores blog posts, articles, and published content.

```sql
CREATE TABLE content (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    slug VARCHAR(500) NOT NULL UNIQUE,
    content TEXT,
    excerpt VARCHAR(1000),
    cover_image_url TEXT,
    featured BOOLEAN DEFAULT FALSE,
    publish_status VARCHAR(50) DEFAULT 'draft',
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

CREATE INDEX idx_content_slug ON content(slug);
CREATE INDEX idx_content_status ON content(publish_status);
CREATE INDEX idx_content_published_at ON content(published_at DESC);
```

### 4. `admin_logs` Table

Tracks system operations, errors, and audit events.

```sql
CREATE TABLE admin_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(100) NOT NULL,
    user_id UUID REFERENCES users(id),
    agent_name VARCHAR(100),
    description TEXT,
    metadata JSONB DEFAULT '{}',
    status VARCHAR(50),
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_logs_type ON admin_logs(event_type);
CREATE INDEX idx_logs_created_at ON admin_logs(created_at DESC);
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
    strapi_id: Optional[int] = None
    strapi_url: Optional[str] = None
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
    strapi_image_id: Optional[int] = None
```

---

## **Firebase Collections (Oversight Hub)**

### 1. `tasks` Collection

Tracks content generation tasks and agent activities.

```json
{
  "taskId": "string", // Document ID - UUID
  "agentId": "string", // "content-agent"
  "taskName": "string", // "Generate Blog Post: [Topic]"
  "status": "string", // "queued", "in_progress", "completed", "failed"
  "createdAt": "timestamp",
  "updatedAt": "timestamp",
  "metadata": {
    "topic": "string",
    "priority": "number", // 1-High, 2-Medium, 3-Low
    "strapiId": "number", // Link to created post
    "trigger": "string" // "manual", "scheduled", "api"
  }
}
```

### 2. `agent_logs` Collection

Detailed logging from content agent operations.

```json
{
  "logId": "string", // Document ID
  "agentId": "string",
  "taskId": "string",
  "level": "string", // "INFO", "WARNING", "ERROR", "DEBUG"
  "message": "string",
  "timestamp": "timestamp",
  "payload": {
    "step": "string", // "Research", "Generation", "QA", "Publishing"
    "durationMs": "number",
    "error": "string", // Optional error details
    "metadata": "object" // Flexible additional data
  }
}
```

### 3. `content_metrics` Collection

Performance tracking for published content.

```json
{
  "contentId": "string", // Document ID
  "strapiId": "number", // Link to Strapi post
  "title": "string",
  "type": "string", // "blog_post"
  "status": "string", // "published", "draft", "archived"
  "publishedAt": "timestamp",
  "url": "string",
  "performance": {
    "views": "number",
    "engagement": "number",
    "socialShares": "number"
  },
  "metadata": {
    "agentVersion": "string",
    "generationTimeMs": "number",
    "aiModel": "string" // "gpt-4", "gpt-3.5-turbo"
  }
}
```

---

## **API Endpoints Reference**

### Strapi v5 REST API

- **Posts**: `/api/posts` (GET, POST, PUT, DELETE)
- **Categories**: `/api/categories` (GET, POST, PUT, DELETE)
- **Tags**: `/api/tags` (GET, POST, PUT, DELETE)
- **Upload**: `/api/upload` (POST for media files)

### Common Query Parameters

- `populate=*` - Include all relations
- `filters[field][$eq]=value` - Filter by field value
- `sort[field]=asc|desc` - Sort by field
- `pagination[page]=1&pagination[pageSize]=25` - Pagination

### Example API Calls

```bash
# Get all posts with relations
GET /api/posts?populate=*

# Get featured posts
GET /api/posts?filters[featured][$eq]=true&populate=*

# Get posts by category
GET /api/posts?filters[category][slug][$eq]=ai-machine-learning&populate=*

# Get single post by slug
GET /api/posts?filters[slug][$eq]=post-slug&populate=*
```

---

**Schema Documentation maintained by:** Glad Labs Development Team  
**Contact:** Matthew M. Gladding (Glad Labs, LLC)  
**Last Review:** October 13, 2025
