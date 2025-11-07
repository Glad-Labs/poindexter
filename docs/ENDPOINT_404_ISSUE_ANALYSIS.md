# 🔴 404 Endpoint Issue - Root Cause Analysis

**Date:** November 6, 2025  
**Issue:** CreateTaskModal and TaskManagement trying to use non-existent endpoints  
**Status:** 🔧 IDENTIFYING CORRECT ENDPOINTS

---

## 🚨 The Problem

```
❌ POST http://localhost:8000/api/content/generate 404 (Not Found)
❌ GET http://localhost:8000/api/content/status/59b6e4f9-6798-4e5a-9746-3fedeaad0007 404 (Not Found)
```

**Why?** These endpoints don't exist in the backend!

---

## 🔍 Root Cause

In the previous fix session, I assumed the backend had these endpoints:

- ✗ `/api/content/generate`
- ✗ `/api/content/status/{task_id}`

But the actual backend implementation (in `src/cofounder_agent/routes/content_routes.py`) defines different endpoints:

- ✅ `/api/content/blog-posts` (POST - Create blog post)
- ✅ `/api/content/blog-posts/tasks/{task_id}` (GET - Check status)

---

## 📋 Actual Backend Endpoints

### Create Blog Post

```python
@content_router.post(
    "/blog-posts",
    response_model=CreateBlogPostResponse,
    status_code=201,
)
async def create_blog_post(request: CreateBlogPostRequest, ...):
```

**Endpoint:** `POST /api/content/blog-posts`

**Request Model:**

```python
class CreateBlogPostRequest(BaseModel):
    topic: str                          # Blog post topic (required)
    style: ContentStyle                 # technical, narrative, listicle, etc.
    tone: ContentTone                   # professional, casual, academic, etc.
    target_length: int                  # Word count (200-5000)
    tags: Optional[List[str]]           # Optional tags
    categories: Optional[List[str]]     # Optional categories
    generate_featured_image: bool       # Search Pexels for image
    publish_mode: PublishMode           # draft or publish
    enhanced: bool                      # Use SEO enhancement
    target_environment: str             # production or staging
```

**Response Model:**

```python
class CreateBlogPostResponse(BaseModel):
    task_id: str                        # Use this to poll status
    status: str                         # Should be "pending"
    topic: str                          # Echo of topic sent
    created_at: str                     # ISO timestamp
    polling_url: str                    # URL to check status
```

### Check Blog Post Status

```python
@content_router.get(
    "/blog-posts/tasks/{task_id}",
    response_model=TaskStatusResponse,
)
async def get_blog_post_status(task_id: str):
```

**Endpoint:** `GET /api/content/blog-posts/tasks/{task_id}`

**Response Model:**

```python
class TaskStatusResponse(BaseModel):
    task_id: str                        # The task ID
    status: str                         # pending, generating, completed, failed
    progress: Optional[Dict]            # Progress info while generating
    result: Optional[Dict]              # Generated blog post when completed
    error: Optional[Dict]               # Error details if failed
    created_at: str                     # ISO timestamp
```

---

## 🔄 Request/Response Flow

### Step 1: Create Blog Post (Frontend → Backend)

```javascript
// WRONG (current code - 404):
POST http://localhost:8000/api/content/generate
{ topic, style, tone, target_length, tags }

// CORRECT (should be):
POST http://localhost:8000/api/content/blog-posts
{
  topic: "AI Trends in 2025",
  style: "technical",           // NOT "Technical"
  tone: "professional",
  target_length: 1500,
  tags: ["AI", "trends"],
  generate_featured_image: true,
  publish_mode: "draft",
  enhanced: false,
  target_environment: "production"
}

// Response (201 Created):
{
  task_id: "abc123-def456",
  status: "pending",
  topic: "AI Trends in 2025",
  created_at: "2025-11-06T10:30:00",
  polling_url: "/api/content/blog-posts/tasks/abc123-def456"
}
```

### Step 2: Poll for Status (Frontend → Backend)

```javascript
// WRONG (current code - 404):
GET http://localhost:8000/api/content/status/abc123-def456

// CORRECT (should be):
GET http://localhost:8000/api/content/blog-posts/tasks/abc123-def456

// Response (200 OK):
// While generating:
{
  task_id: "abc123-def456",
  status: "generating",
  progress: {
    stage: "creative",
    percentage: 45
  },
  result: null,
  error: null,
  created_at: "2025-11-06T10:30:00"
}

// When completed:
{
  task_id: "abc123-def456",
  status: "completed",
  progress: null,
  result: {
    title: "AI Trends in 2025: A Comprehensive Guide",
    content: "## Introduction\n\n...",
    seo_title: "AI Trends 2025 - Latest Developments",
    seo_description: "Explore the top AI trends...",
    featured_image_url: "https://pexels.com/...",
    word_count: 1547,
    strapi_post_id: 123
  },
  error: null,
  created_at: "2025-11-06T10:30:00"
}
```

---

## 🔧 Required Fixes

### Fix 1: CreateTaskModal.jsx (Line 234)

**Change FROM:**

```javascript
response = await fetch('http://localhost:8000/api/content/generate', {
  method: 'POST',
  headers,
  body: JSON.stringify(contentPayload),
});
```

**Change TO:**

```javascript
response = await fetch('http://localhost:8000/api/content/blog-posts', {
  method: 'POST',
  headers,
  body: JSON.stringify({
    topic: contentPayload.topic || '',
    style: contentPayload.style || 'technical',
    tone: contentPayload.tone || 'professional',
    target_length: contentPayload.target_length || 1500,
    tags: contentPayload.tags || [],
    generate_featured_image: true,
    publish_mode: 'draft',
    enhanced: false,
    target_environment: 'production',
  }),
});
```

### Fix 2: TaskManagement.jsx (Line 78)

**Change FROM:**

```javascript
const response = await fetch(
  `http://localhost:8000/api/content/status/${taskId}`,
  { headers, signal: AbortSignal.timeout(5000) }
);
```

**Change TO:**

```javascript
const response = await fetch(
  `http://localhost:8000/api/content/blog-posts/tasks/${taskId}`,
  { headers, signal: AbortSignal.timeout(5000) }
);
```

---

## 📊 Comparison: Wrong vs. Correct Endpoints

| Aspect              | Wrong (404)                             | Correct (200)                                          |
| ------------------- | --------------------------------------- | ------------------------------------------------------ |
| **Create Endpoint** | `/api/content/generate`                 | `/api/content/blog-posts`                              |
| **Status Endpoint** | `/api/content/status/{id}`              | `/api/content/blog-posts/tasks/{id}`                   |
| **Request Model**   | Custom fields                           | CreateBlogPostRequest                                  |
| **Response**        | 404 Not Found                           | 201 Created + task_id                                  |
| **Payload Style**   | lowercase                               | camelCase fields                                       |
| **Required Fields** | topic, style, tone, target_length, tags | Same + generate_featured_image, publish_mode, enhanced |

---

## ✅ Verification Checklist

After fixes applied:

- [ ] CreateTaskModal uses `/api/content/blog-posts` endpoint
- [ ] TaskManagement uses `/api/content/blog-posts/tasks/{taskId}` endpoint
- [ ] Both files reference correct endpoint URLs
- [ ] Request payload includes all required CreateBlogPostRequest fields
- [ ] Response parsing handles correct TaskStatusResponse structure
- [ ] Console shows no 404 errors
- [ ] Blog post executes pipeline (20-30 seconds)
- [ ] Results display correctly in UI

---

## 📝 Summary

**Previous Assumption:** Endpoints would be `/api/content/generate` and `/api/content/status`

**Reality:** Actual endpoints are `/api/content/blog-posts` and `/api/content/blog-posts/tasks/{taskId}`

**Action:** Update both frontend files to use correct endpoints and payload structure

**Impact:** Blog posts will route correctly and execute self-critique pipeline
