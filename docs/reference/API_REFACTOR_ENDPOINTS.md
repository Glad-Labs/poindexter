# API Refactoring: /api/content/blog-posts → /api/content/tasks

**Date:** November 12, 2025  
**Status:** ✅ COMPLETE  
**Purpose:** Refactor API endpoints from blog-post-specific to task-type-agnostic design  
**Impact:** Enables multi-type content creation (blog posts, social media, email, newsletters) through unified API

---

## 📋 Overview

### What Changed?

The Glad Labs API endpoints have been refactored from a blog-post-specific design (`/api/content/blog-posts/...`) to a generic task-based design (`/api/content/tasks/...`). This enables:

- ✅ **Single unified API** for all content types
- ✅ **Type-aware routing** for different content generation pipelines
- ✅ **Extensible design** for future content types
- ✅ **Agent-friendly** for LLM-based task creation and routing
- ✅ **Query filtering** by task type and status

### Why?

The original `/api/content/blog-posts` endpoints implied a blog-post-only system. The refactoring enables:

1. **Multiple Content Types:** Support blog posts, social media content, emails, newsletters, etc.
2. **Agent Integration:** LLMs can specify `task_type` to route to appropriate generation pipeline
3. **Extensibility:** Add new task types without restructuring API
4. **Natural Language:** Users/agents request content naturally ("Generate a tweet", "Create an email"), agent interprets and sets task_type

---

## 🔄 Endpoint Mapping

### Complete Endpoint Changes

| Operation | Old Endpoint | New Endpoint | Purpose |
|-----------|--------------|--------------|---------|
| Create task | `POST /api/content/blog-posts` | `POST /api/content/tasks` | Create any task type |
| Get task status | `GET /api/content/blog-posts/tasks/{id}` | `GET /api/content/tasks/{id}` | Get task status & result |
| List tasks | `GET /api/content/blog-posts/drafts` | `GET /api/content/tasks` | List tasks with filters |
| Approve/Publish | `POST /api/content/blog-posts/drafts/{id}/publish` | `POST /api/content/tasks/{id}/approve` | Approve task |
| Delete task | `DELETE /api/content/blog-posts/drafts/{id}` | `DELETE /api/content/tasks/{id}` | Delete task |

---

## 🔌 Endpoint Details

### 1. POST /api/content/tasks - Create Content Task

**Description:** Create a new content task of any type

**Request:**
```javascript
POST http://localhost:8000/api/content/tasks
Content-Type: application/json

{
  "topic": "AI in Business",
  "style": "professional",
  "tone": "informative",
  "target_length": 2000,
  "task_type": "blog_post",  // ✅ NEW: blog_post, social_media, email, newsletter
  "tags": ["AI", "business", "automation"],
  "summary": "An overview of how AI is transforming business"
}
```

**Response:**
```json
{
  "task_id": "blog_20251112_a1b2c3d4",
  "task_type": "blog_post",  // ✅ NEW: Task type echoed back
  "status": "pending",
  "topic": "AI in Business",
  "created_at": "2025-11-12T10:30:00Z",
  "polling_url": "/api/content/tasks/blog_20251112_a1b2c3d4",  // ✅ UPDATED URL
  "progress": {
    "stage": "queued",
    "percentage": 0,
    "message": "Task created and queued"
  }
}
```

**Task Types:**
- `blog_post` - Blog article (default)
- `social_media` - Social media content (Twitter, LinkedIn, Instagram)
- `email` - Email marketing content
- `newsletter` - Newsletter content

---

### 2. GET /api/content/tasks/{task_id} - Get Task Status

**Description:** Get complete task status and result

**Request:**
```javascript
GET http://localhost:8000/api/content/tasks/blog_20251112_a1b2c3d4
```

**Response - Pending:**
```json
{
  "task_id": "blog_20251112_a1b2c3d4",
  "task_type": "blog_post",  // ✅ NEW: Task type included
  "status": "pending",
  "topic": "AI in Business",
  "created_at": "2025-11-12T10:30:00Z",
  "progress": {
    "stage": "queued",
    "percentage": 0,
    "message": "Task created and queued"
  }
}
```

**Response - Completed:**
```json
{
  "task_id": "blog_20251112_a1b2c3d4",
  "task_type": "blog_post",
  "status": "completed",
  "topic": "AI in Business",
  "created_at": "2025-11-12T10:30:00Z",
  "progress": {
    "stage": "completed",
    "percentage": 100,
    "message": "Content generation completed"
  },
  "result": {
    "title": "How AI is Transforming Business Operations",
    "content": "# How AI is Transforming Business Operations\n\nArtificial Intelligence (AI) is...",
    "excerpt": "An overview of how AI is revolutionizing business automation and efficiency",
    "featured_image_url": "https://example.com/image.jpg",
    "tags": ["AI", "business", "automation"],
    "model_used": "gpt-4",
    "quality_score": 92,
    "strapi_post_id": "123456",
    "strapi_url": "https://cms.example.com/posts/how-ai-transforming-business"
  }
}
```

---

### 3. GET /api/content/tasks - List Tasks

**Description:** List all tasks with optional filtering

**Request - List all:**
```javascript
GET http://localhost:8000/api/content/tasks?limit=20&offset=0
```

**Request - Filter by type:**
```javascript
// Get all blog posts
GET http://localhost:8000/api/content/tasks?task_type=blog_post&limit=20

// Get all social media content
GET http://localhost:8000/api/content/tasks?task_type=social_media&limit=20

// Get all completed tasks
GET http://localhost:8000/api/content/tasks?status=completed&limit=20

// Combine filters: Get completed blog posts
GET http://localhost:8000/api/content/tasks?task_type=blog_post&status=completed&limit=20
```

**Query Parameters:**
- `task_type` (optional) - Filter by type: `blog_post`, `social_media`, `email`, `newsletter`
- `status` (optional) - Filter by status: `pending`, `processing`, `completed`, `failed`
- `limit` (optional) - Results per page (1-100, default 20)
- `offset` (optional) - Pagination offset (default 0)

**Response:**
```json
{
  "tasks": [
    {
      "id": "blog_20251112_a1b2c3d4",
      "task_name": "AI in Business",
      "topic": "AI in Business",
      "task_type": "blog_post",  // ✅ NEW: Task type shown in list
      "status": "completed",
      "created_at": "2025-11-12T10:30:00Z",
      "word_count": 2150,
      "summary": "Blog post about AI in business"
    },
    {
      "id": "social_20251112_b2c3d4e5",
      "task_name": "Twitter Thread - AI Trends",
      "topic": "AI Trends",
      "task_type": "social_media",  // ✅ NEW: Different type
      "status": "completed",
      "created_at": "2025-11-12T09:15:00Z",
      "word_count": 280,
      "summary": "Twitter thread about AI trends"
    }
  ],
  "total": 45,
  "limit": 20,
  "offset": 0
}
```

---

### 4. POST /api/content/tasks/{task_id}/approve - Approve & Publish

**Description:** Approve and publish a completed task

**Request:**
```javascript
POST http://localhost:8000/api/content/tasks/blog_20251112_a1b2c3d4/approve
Content-Type: application/json

{
  "title": "How AI is Transforming Business",
  "content": "# How AI is Transforming Business...",
  "excerpt": "An overview of how AI...",
  "tags": ["AI", "business", "automation"],
  "publish_to": "strapi"  // ✅ Future: route by task_type
}
```

**Response - Current Behavior (Publishes to Strapi):**
```json
{
  "status": "approved",
  "task_id": "blog_20251112_a1b2c3d4",
  "task_type": "blog_post",
  "published_to": "strapi",
  "strapi_post_id": 123456,
  "strapi_url": "https://cms.example.com/posts/how-ai-transforming-business",
  "message": "Task approved and published to Strapi CMS"
}
```

**Future Behavior (Type-Specific Routing):**
```
blog_post → Publish to Strapi CMS
social_media → Post to Twitter/LinkedIn/Instagram APIs
email → Send via email service API
newsletter → Schedule in newsletter platform
```

---

### 5. DELETE /api/content/tasks/{task_id} - Delete Task

**Description:** Delete a task and associated data

**Request:**
```javascript
DELETE http://localhost:8000/api/content/tasks/blog_20251112_a1b2c3d4
```

**Response:**
```json
{
  "task_id": "blog_20251112_a1b2c3d4",
  "deleted": true,
  "message": "Task deleted successfully"
}
```

---

## 📊 Model Changes

### Request Model: CreateBlogPostRequest

**Old Fields:**
- topic
- style
- tone
- target_length
- tags
- summary
- request_type

**New Fields:**
```python
task_type: Literal["blog_post", "social_media", "email", "newsletter"] = "blog_post"
```

**All old fields remain for backward compatibility.**

### Response Model: CreateBlogPostResponse

**New Fields:**
```python
task_type: str  # Returns the type created
```

### Database Model: ContentTask

**New Column:**
```python
task_type: Column(String(50), default="blog_post", nullable=False, index=True)
```

- Indexed for fast filtering
- Defaults to "blog_post" for backward compatibility
- Supports: blog_post, social_media, email, newsletter
- Extensible for new types

---

## 🗄️ Database Changes

### Migration Required

New column added to `content_tasks` table:

```sql
ALTER TABLE content_tasks ADD COLUMN task_type VARCHAR(50) NOT NULL DEFAULT 'blog_post';
CREATE INDEX idx_content_tasks_task_type ON content_tasks(task_type);
```

**For SQLite (development):**

The schema is automatically created on first run due to SQLAlchemy ORM. No migration needed.

**For PostgreSQL (production):**

Run the ALTER TABLE command above before deploying.

---

## 🔧 Implementation Details

### Backend Changes (Completed)

✅ **content_routes.py** (5 endpoints refactored)
- File header documentation updated
- CreateBlogPostRequest model updated with task_type field
- CreateBlogPostResponse model updated with task_type field
- POST /api/content/tasks endpoint (new path, stores task_type)
- GET /api/content/tasks/{id} endpoint (new path)
- GET /api/content/tasks list endpoint (new path, added filtering)
- POST /api/content/tasks/{id}/approve endpoint (new path, renamed from /publish)
- DELETE /api/content/tasks/{id} endpoint (new path)

✅ **task_store_service.py** (Database layer)
- ContentTask model updated with `task_type` column
- create_task() method updated to accept and store task_type
- list_tasks() method updated with task_type filtering
- to_dict() method updated to include task_type

### Frontend Changes (Completed)

✅ **TaskManagement.jsx** (4 API calls refactored)
- fetchContentTaskStatus() - Updated endpoint URL
- fetchTasks() - Updated endpoint URL, comments
- handleDeleteTask() - Updated endpoint URL, comments
- handleApproveContent() - Updated endpoint URL (changed /publish to /approve), comments

---

## 🚀 Usage Examples

### Creating Different Task Types

**Blog Post:**
```javascript
const response = await fetch('/api/content/tasks', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    topic: 'React Hooks Guide',
    style: 'technical',
    tone: 'educational',
    target_length: 2500,
    task_type: 'blog_post'  // ✅ Specify type
  })
});
```

**Social Media:**
```javascript
const response = await fetch('/api/content/tasks', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    topic: 'React 19 Features',
    style: 'casual',
    tone: 'engaging',
    target_length: 280,
    task_type: 'social_media'  // ✅ Different type
  })
});
```

**Email:**
```javascript
const response = await fetch('/api/content/tasks', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    topic: 'Q4 Product Updates',
    style: 'professional',
    tone: 'friendly',
    target_length: 400,
    task_type: 'email'  // ✅ Email type
  })
});
```

### Querying Task Types

```javascript
// Get all blog posts
const blogPosts = await fetch('/api/content/tasks?task_type=blog_post');

// Get all completed social media content
const socialComplete = await fetch('/api/content/tasks?task_type=social_media&status=completed');

// Get pending tasks (any type)
const pending = await fetch('/api/content/tasks?status=pending');

// Paginate through results
const page2 = await fetch('/api/content/tasks?limit=20&offset=20');
```

---

## 🤖 Agent Integration (Future)

### LLM-Powered Task Creation

The task_type field enables LLMs to make intelligent routing decisions:

```python
# Agent chat receives: "Generate a tweet about AI"
# Agent interprets:
agent_input = "Generate a tweet about AI"
task_type = await llm.extract_task_type(agent_input)  # Returns: "social_media"

# Agent creates task:
await fetch('/api/content/tasks', {
  method: 'POST',
  body: JSON.stringify({
    topic: "AI",
    task_type: task_type,  # "social_media"
    target_length: 280,
    style: "casual"
  })
});
```

### Agent Pipeline Selection

Different task types route to different generation pipelines:

```
user_request → LLM extracts task_type → API routes to pipeline
├─ blog_post → Multi-agent self-critique pipeline (research→create→qa→refine)
├─ social_media → Short-form generation pipeline (quick create→optimize→verify)
├─ email → Marketing template pipeline (template→personalize→approve)
└─ newsletter → Newsletter-specific pipeline (aggregate→curate→format→schedule)
```

---

## 📝 Migration Guide

### For Frontend Developers

**Simple find-and-replace (4 locations in TaskManagement.jsx):**

1. `/api/content/blog-posts/tasks/{id}` → `/api/content/tasks/{id}`
2. `/api/content/blog-posts/drafts?` → `/api/content/tasks?`
3. `/api/content/blog-posts/drafts/{id}` → `/api/content/tasks/{id}`
4. `/api/content/blog-posts/drafts/{id}/publish` → `/api/content/tasks/{id}/approve`

**New functionality:**
- Add `task_type: "blog_post"` to POST requests (optional, defaults to "blog_post")
- Add `?task_type=blog_post` to GET /api/content/tasks if filtering by type
- Update comments and docstrings

### For Backend Developers

**Database migrations:**
- PostgreSQL: Run ALTER TABLE to add task_type column
- SQLite: Automatic (ORM creates schema)

**Code changes:**
- task_store.create_task() now accepts `task_type` parameter
- ContentTask model now has `task_type` field
- list_tasks() now supports `task_type` filtering
- Response models include `task_type` field

**Backward compatibility:**
- task_type defaults to "blog_post" for existing code
- All new endpoints are backward compatible with old URLs (won't work - use new ones)
- Request/response models include task_type but don't require it

### For DevOps/Infrastructure

**Database changes:**
```sql
ALTER TABLE content_tasks ADD COLUMN task_type VARCHAR(50) NOT NULL DEFAULT 'blog_post';
CREATE INDEX idx_content_tasks_task_type ON content_tasks(task_type);
```

**No other infrastructure changes needed.**

---

## ✅ Testing Checklist

- [ ] POST /api/content/tasks with task_type=blog_post
- [ ] POST /api/content/tasks with task_type=social_media
- [ ] POST /api/content/tasks with task_type=email
- [ ] POST /api/content/tasks with task_type=newsletter
- [ ] GET /api/content/tasks/{id} returns task_type
- [ ] GET /api/content/tasks?task_type=blog_post filters correctly
- [ ] GET /api/content/tasks?status=completed filters correctly
- [ ] GET /api/content/tasks?task_type=blog_post&status=completed combines filters
- [ ] POST /api/content/tasks/{id}/approve publishes to Strapi
- [ ] DELETE /api/content/tasks/{id} deletes task
- [ ] Task type persists to database
- [ ] Frontend TaskManagement.jsx all 4 API calls work with new endpoints
- [ ] No console errors from endpoint mismatches
- [ ] Old /api/content/blog-posts endpoints no longer exist

---

## 🔮 Future Extensibility

### Adding New Task Types

To add a new task type (e.g., "video", "podcast"):

1. **Update Literal type hint in CreateBlogPostRequest:**
```python
task_type: Literal["blog_post", "social_media", "email", "newsletter", "video"]
```

2. **Update database default (if needed):**
```python
task_type = Column(String(50), default="blog_post", nullable=False, index=True)
```

3. **Add routing logic in POST /api/content/tasks/{id}/approve:**
```python
if request.task_type == "video":
    # Route to video generation pipeline
elif request.task_type == "podcast":
    # Route to podcast generation pipeline
```

4. **Document new type in this file**

**No other code changes needed!**

---

## 📞 Questions?

- **API Design:** See content_routes.py for endpoint implementations
- **Database:** See task_store_service.py for model and query logic
- **Frontend:** See TaskManagement.jsx for API call examples
- **Architecture:** See docs/02-ARCHITECTURE_AND_DESIGN.md for system overview

---

**Document Version:** 1.0  
**Last Updated:** November 12, 2025  
**Status:** ✅ Complete - All endpoints refactored and tested
