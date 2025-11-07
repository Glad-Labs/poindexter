# 🚀 GLAD Labs - Complete System Integration Guide

**Status:** ✅ Ready for End-to-End Integration  
**Last Updated:** November 2025  
**Objective:** Connect Oversight Hub → Cofounder Agent → Strapi CMS → Public Site

---

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Current Status](#current-status)
4. [Quick Start Checklist](#quick-start-checklist)
5. [Integration Steps](#integration-steps)
6. [Testing & Verification](#testing--verification)
7. [Troubleshooting](#troubleshooting)

---

## 🏗️ System Overview

### Four-Tier Integration

```
┌─────────────────────────────────────────────────────────────┐
│  TIER 1: Oversight Hub (React Dashboard)                    │
│  Port: 3001 | Purpose: Task creation & monitoring           │
│  CREATE BLOG POST TASK                                      │
└─────────────┬───────────────────────────────────────────────┘
              │
              │ POST /api/content/blog-posts
              │ (JSON: topic, style, tone, length, tags...)
              │
┌─────────────▼───────────────────────────────────────────────┐
│  TIER 2: Cofounder Agent (FastAPI Backend)                  │
│  Port: 8000 | Purpose: Content generation & orchestration   │
│  • Ollama: Generate content (local, zero-cost)              │
│  • Quality Assessment: 8-dimension critique                 │
│  • Strapi Publisher: Publish to CMS                         │
└─────────────┬───────────────────────────────────────────────┘
              │
              │ REST API POST /api/posts
              │ (JSON: title, content, slug, excerpt, tags...)
              │
┌─────────────▼───────────────────────────────────────────────┐
│  TIER 3: Strapi CMS (Headless Content Management)           │
│  Port: 1337 | Purpose: Content storage & management         │
│  Collections: Posts, Authors, Categories, Tags, Metrics     │
└─────────────┬───────────────────────────────────────────────┘
              │
              │ REST API GET /api/posts
              │ (Fetch published content with filters)
              │
┌─────────────▼───────────────────────────────────────────────┐
│  TIER 4: Public Site (Next.js Frontend)                     │
│  Port: 3000 | Purpose: Display published content            │
│  Display blog post with metadata, tags, and images          │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Component           | Technology                       | Port | Status          |
| ------------------- | -------------------------------- | ---- | --------------- |
| **Oversight Hub**   | React 18 + Material-UI + Zustand | 3001 | ✅ Running      |
| **Cofounder Agent** | FastAPI + PostgreSQL + Ollama    | 8000 | ⏳ Start needed |
| **Strapi CMS**      | Node.js Strapi v4 + PostgreSQL   | 1337 | ✅ Running      |
| **Public Site**     | Next.js 15 + React 18 + Tailwind | 3000 | ⏳ Start needed |

---

## 📊 Architecture Diagram

### Data Flow: Blog Post Creation → Publication

```
USER ACTION
    ↓
┌─────────────────────────────────────────────────────────────┐
│ 1. OVERSIGHT HUB - Blog Post Creator Form                   │
│                                                             │
│  Input Fields:                                              │
│  ├─ Topic: "AI in Business"                                │
│  ├─ Style: technical/casual/creative                       │
│  ├─ Tone: professional/friendly/humorous                   │
│  ├─ Length: 1000-5000 words                                │
│  ├─ Tags: AI, Business, Technology                         │
│  ├─ Categories: Tech, Leadership                           │
│  └─ Publish Mode: draft/publish_immediate                  │
│                                                             │
│  Button: "Generate Blog Post"                              │
│  Action: POST /api/content/blog-posts                      │
└────────────────┬────────────────────────────────────────────┘
                 │
                 │ REQUEST PAYLOAD
                 │ {
                 │   "topic": "AI in Business",
                 │   "style": "technical",
                 │   "tone": "professional",
                 │   "target_length": 1500,
                 │   "tags": ["AI", "Business"],
                 │   "categories": ["Tech"],
                 │   "generate_featured_image": true,
                 │   "publish_mode": "draft"
                 │ }
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. COFOUNDER AGENT - Content Generation Pipeline            │
│                                                             │
│  Step A: Accept Task                                        │
│  ├─ Create Task ID (UUID)                                  │
│  ├─ Store in task database                                 │
│  └─ Return task_id + polling_url                           │
│                                                             │
│  → RESPONSE: CreateBlogPostResponse                         │
│    {                                                        │
│      "task_id": "abc-123",                                  │
│      "status": "queued",                                    │
│      "polling_url": "/api/content/blog-posts/tasks/abc-123" │
│    }                                                        │
│                                                             │
│  Step B: Background Processing                              │
│  ├─ [OLLAMA] Generate content (LocalModel)                 │
│  ├─ [QA] Quality Assessment (8 dimensions)                 │
│  │   ├─ Clarity                                             │
│  │   ├─ Accuracy                                            │
│  │   ├─ Engagement                                          │
│  │   ├─ Grammar                                             │
│  │   ├─ Structure                                           │
│  │   ├─ SEO                                                 │
│  │   ├─ Originality                                         │
│  │   └─ Relevance                                           │
│  ├─ [IMAGES] Find images (Pexels API)                      │
│  └─ Update task status: "in_progress" → "completed"        │
│                                                             │
│  Generated Content:                                         │
│  {                                                          │
│    "title": "AI in Business: Practical Applications",       │
│    "content": "# AI in Business...",                        │
│    "excerpt": "Explore how AI transforms...",              │
│    "featured_image": "https://...",                         │
│    "featured_image_alt": "...",                             │
│    "quality_score": 8.7,                                    │
│    "quality_details": {                                     │
│      "clarity": 9,                                          │
│      "accuracy": 8,                                         │
│      ...                                                    │
│    }                                                        │
│  }                                                          │
│                                                             │
│  Step C: Publish to Strapi                                 │
│  └─ POST /api/posts (with all metadata)                    │
└────────────────┬────────────────────────────────────────────┘
                 │
                 │ POLLING FLOW (from Oversight Hub)
                 │ GET /api/content/blog-posts/tasks/abc-123
                 │ (every 2 seconds)
                 │
                 │ POLLING RESPONSES:
                 │ Response 1: {"status": "queued", "progress": 5}
                 │ Response 2: {"status": "generating", "progress": 25}
                 │ Response 3: {"status": "generating", "progress": 50}
                 │ Response 4: {"status": "assessing", "progress": 75}
                 │ Response 5: {"status": "completed", "result": {...}}
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. STRAPI CMS - Content Storage & Management                │
│                                                             │
│  Create Post (from Cofounder Agent):                        │
│  POST /api/posts                                            │
│  {                                                          │
│    "data": {                                                │
│      "title": "AI in Business: Practical Applications",    │
│      "slug": "ai-in-business-practical-applications",      │
│      "content": "# AI in Business...",                      │
│      "excerpt": "Explore how AI transforms...",            │
│      "featured_image": {...},                              │
│      "category": 1,  (ID from Strapi)                      │
│      "tags": [1, 2], (IDs from Strapi)                     │
│      "featured": false,                                     │
│      "date": "2025-11-02T10:30:00Z",                       │
│      "publish": true                                        │
│    }                                                        │
│  }                                                          │
│                                                             │
│  Strapi Database Tables:                                    │
│  ├─ posts (main content)                                   │
│  ├─ categories (Blog, Tech, AI, etc.)                      │
│  ├─ tags (AI, ML, Business, etc.)                          │
│  ├─ content-metrics (views, engagement, etc.)              │
│  └─ authors (byline information)                           │
│                                                             │
│  Strapi Response: ✅ Post ID 42 created                     │
└────────────────┬────────────────────────────────────────────┘
                 │
                 │ PUBLIC SITE UPDATES
                 │ (ISR - Incremental Static Regeneration)
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. PUBLIC SITE - Content Display                            │
│                                                             │
│  Fetch Published Posts:                                     │
│  GET /api/posts?filters[publish]=true&sort=date:desc       │
│                                                             │
│  Display Flow:                                              │
│  ├─ Homepage: Show featured posts in grid                  │
│  ├─ Individual Post Page: /posts/[slug]                    │
│  ├─ Category Pages: /category/tech                         │
│  ├─ Tag Pages: /tags/ai                                    │
│  └─ Archive: Paginated post list                           │
│                                                             │
│  Post Display Elements:                                     │
│  ├─ Title                                                  │
│  ├─ Featured Image (with alt text)                         │
│  ├─ Excerpt                                                │
│  ├─ Content (rendered markdown)                            │
│  ├─ Author                                                 │
│  ├─ Publication Date                                       │
│  ├─ Category                                               │
│  ├─ Tags                                                   │
│  └─ Related Posts                                          │
│                                                             │
│  ✅ POST NOW VISIBLE ON PUBLIC SITE                         │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ Current Status

### Components Ready

| Component           | Status | What's Working                                                                            | What's Needed                                |
| ------------------- | ------ | ----------------------------------------------------------------------------------------- | -------------------------------------------- |
| **Oversight Hub**   | ✅ 95% | BlogPostCreator component exists, API client configured                                   | Update `.env` with cofounder-agent URL       |
| **Cofounder Agent** | ✅ 90% | POST /api/content/blog-posts endpoint ready, task storage working, Strapi publisher ready | Start the server, test endpoint connectivity |
| **Strapi CMS**      | ✅ 95% | Content types defined (posts, categories, tags), API endpoints ready                      | Verify API authentication tokens             |
| **Public Site**     | ✅ 95% | Strapi API client implemented, pages ready to display posts                               | Ensure Strapi authentication token is set    |

### Missing Links

- ❌ **Environment Configuration**: Oversight Hub needs `REACT_APP_API_URL` pointing to Cofounder Agent
- ❌ **Strapi Authentication**: Cofounder Agent needs Strapi API token and base URL
- ❌ **Database Validation**: Verify PostgreSQL connections are working

---

## 🚀 Quick Start Checklist

Follow these steps to get the full system working:

### ✅ STEP 1: Start All Services (5 minutes)

```powershell
# Terminal 1: Start Cofounder Agent Backend
cd c:\Users\mattm\glad-labs-website\src\cofounder_agent
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Expected: "Uvicorn running on http://0.0.0.0:8000"
# Check: http://localhost:8000/docs

# Terminal 2: Start Public Site (if not already running)
cd c:\Users\mattm\glad-labs-website\web\public-site
npm run dev
# Expected: "compiled client and server successfully"
# Check: http://localhost:3000

# NOTE: Strapi CMS and Oversight Hub should already be running
# Check Strapi: http://localhost:1337
# Check Oversight Hub: http://localhost:3001
```

### ✅ STEP 2: Configure Environment Variables (5 minutes)

**File: `web/oversight-hub/.env.local`**

```bash
# Cofounder Agent API (backend)
REACT_APP_API_URL=http://localhost:8000

# Optional: Override other defaults
REACT_APP_API_TIMEOUT=180000  # 3 minutes for long operations
```

**File: `src/cofounder_agent/.env`** (if not already set)

```bash
# Strapi CMS Configuration
STRAPI_BASE_URL=http://localhost:1337
STRAPI_API_TOKEN=your-strapi-api-token-here

# Database (PostgreSQL - if using production DB)
DATABASE_URL=postgresql://user:password@localhost:5432/glad_labs

# Or SQLite (default for development)
DATABASE_URL=sqlite:///./test.db

# Ollama Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral  # or llama3.2, phi, neural-chat

# Model Configuration
PREFERRED_MODEL=ollama  # 'ollama' for local (free), or 'openai', 'anthropic'
```

**File: `web/public-site/.env.local`** (if not already set)

```bash
# Strapi CMS API
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=your-strapi-api-token-here
```

### ✅ STEP 3: Verify Strapi API Token (5 minutes)

1. Visit **http://localhost:1337/admin**
2. Login with admin credentials
3. Go to **Settings → API Tokens → Create new API Token**
4. Name: "Cofounder Agent"
5. Type: "Full access" (or specific endpoints)
6. Copy the token
7. Paste into `.env` files for both Cofounder Agent and Public Site

### ✅ STEP 4: Test Basic Connectivity (10 minutes)

```powershell
# Test Cofounder Agent API
curl http://localhost:8000/api/health
# Expected: {"status": "healthy", "timestamp": "..."}

# Test Strapi API
curl http://localhost:1337/api/posts
# Expected: {"data": [], "meta": {...}}

# Test Public Site
curl http://localhost:3000
# Expected: HTML homepage

# Test Oversight Hub
curl http://localhost:3001
# Expected: HTML React dashboard
```

### ✅ STEP 5: Create Your First Blog Post (15 minutes)

1. Open **http://localhost:3001** (Oversight Hub)
2. Navigate to **Blog Post Creator** section
3. Fill in form:
   - **Topic**: "AI Trends in 2025"
   - **Style**: "technical"
   - **Tone**: "professional"
   - **Length**: 1500 words
   - **Tags**: "AI, Technology, 2025"
   - **Categories**: "Tech"
4. Click **"Generate Blog Post"**
5. Monitor progress (refreshes every 2 seconds)
6. Wait for ✅ **"Completed"** status

### ✅ STEP 6: Verify in Strapi CMS (5 minutes)

1. Open **http://localhost:1337/admin**
2. Go to **Content Manager → Posts**
3. You should see your newly created post
4. Click to view full content with metadata

### ✅ STEP 7: Verify on Public Site (5 minutes)

1. Open **http://localhost:3000** (Public Site)
2. Your post should appear in:
   - **Homepage**: Featured posts section
   - **Blog page**: Full post list
   - **Direct URL**: `/posts/[slug]`
3. Click post title to read full article

---

## 🔧 Integration Steps

### Step 1: Verify Cofounder Agent Backend

```powershell
# Start the backend
cd src/cofounder_agent
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# In another terminal, test the health endpoint
curl http://localhost:8000/api/health

# Expected output:
# {
#   "status": "healthy",
#   "timestamp": "2025-11-02T10:30:00.000Z",
#   "services": {
#     "database": "connected",
#     "ollama": "available",
#     "strapi": "configured"
#   }
# }
```

### Step 2: Create and Test Blog Post Task

**Using PowerShell/curl:**

```powershell
# Create a blog post task
$body = @{
    topic = "Future of AI"
    style = "technical"
    tone = "professional"
    target_length = 1500
    tags = @("AI", "Technology", "Future")
    categories = @("Tech")
    generate_featured_image = $true
    publish_mode = "draft"
} | ConvertTo-Json

curl -X POST `
  -H "Content-Type: application/json" `
  -d $body `
  http://localhost:8000/api/content/blog-posts

# Expected response:
# {
#   "task_id": "abc-123-uuid",
#   "status": "queued",
#   "topic": "Future of AI",
#   "created_at": "2025-11-02T10:30:00Z",
#   "polling_url": "/api/content/blog-posts/tasks/abc-123-uuid"
# }
```

**Using Python (recommended):**

```python
import requests
import json
import time

BASE_URL = "http://localhost:8000"

# Create task
task_data = {
    "topic": "Future of AI",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500,
    "tags": ["AI", "Technology", "Future"],
    "categories": ["Tech"],
    "generate_featured_image": True,
    "publish_mode": "draft"
}

response = requests.post(f"{BASE_URL}/api/content/blog-posts", json=task_data)
print(f"✅ Task Created: {response.json()}")

task_id = response.json()["task_id"]

# Poll for completion
for i in range(120):  # 120 * 5 = 600 seconds (10 minutes max)
    task_status = requests.get(f"{BASE_URL}/api/content/blog-posts/tasks/{task_id}")
    status = task_status.json()
    print(f"[{i}] Status: {status['status']} - Progress: {status.get('progress', {})}")

    if status['status'] == 'completed':
        print(f"✅ Task Completed!")
        print(f"Quality Score: {status['result'].get('quality_score')}")
        break
    elif status['status'] == 'failed':
        print(f"❌ Task Failed: {status['error']}")
        break

    time.sleep(5)  # Wait 5 seconds before polling again
```

### Step 3: Verify Strapi Content

```bash
# List all posts
curl http://localhost:1337/api/posts \
  -H "Authorization: Bearer YOUR_API_TOKEN"

# Get specific post with all relations
curl "http://localhost:1337/api/posts?populate=*" \
  -H "Authorization: Bearer YOUR_API_TOKEN"
```

### Step 4: Verify Public Site Display

```bash
# Test that public site can fetch posts
curl http://localhost:3000/api/posts

# Or visit http://localhost:3000 in browser and check:
# - Homepage displays featured posts
# - Blog page lists all posts
# - Individual post pages work (/posts/[slug])
```

---

## 🧪 Testing & Verification

### Test Matrix

| Test                       | Command                                  | Expected Result                                     |
| -------------------------- | ---------------------------------------- | --------------------------------------------------- |
| **Cofounder Agent Health** | `curl http://localhost:8000/api/health`  | `{"status": "healthy"}`                             |
| **Create Blog Task**       | `POST /api/content/blog-posts`           | `201 Created` with task_id                          |
| **Poll Task Status**       | `GET /api/content/blog-posts/tasks/{id}` | Status progression: queued → generating → completed |
| **Strapi API Access**      | `curl http://localhost:1337/api/posts`   | `{"data": [...]}`                                   |
| **Public Site Page Load**  | `curl http://localhost:3000`             | `200 OK` with HTML                                  |
| **Public Site Blog List**  | Visit `/blog`                            | Page displays all posts                             |
| **Individual Post**        | Visit `/posts/[slug]`                    | Full post with metadata displays                    |

### Debugging Commands

```powershell
# Check if services are running
Get-Process | Select-Object ProcessName, Id | Where-Object {$_.ProcessName -like "*node*" -or $_.ProcessName -like "*python*"}

# Check port usage
netstat -ano | findstr ":8000"    # Cofounder Agent
netstat -ano | findstr ":1337"    # Strapi
netstat -ano | findstr ":3001"    # Oversight Hub
netstat -ano | findstr ":3000"    # Public Site

# Check database connections
psql -c "SELECT datname FROM pg_database" 2>&1 | grep glad

# View Cofounder Agent logs
cd src/cofounder_agent
python -m uvicorn main:app --reload 2>&1 | Tee-Object -FilePath logs.txt
```

---

## 🐛 Troubleshooting

### Issue: "Cannot connect to Cofounder Agent"

**Symptom:** Oversight Hub shows "API connection failed"

**Solution:**

```powershell
# 1. Verify Cofounder Agent is running
curl http://localhost:8000/api/health

# 2. Check REACT_APP_API_URL environment variable
# In web/oversight-hub/.env.local should have:
# REACT_APP_API_URL=http://localhost:8000

# 3. Restart Oversight Hub after changing .env
cd web/oversight-hub
npm start

# 4. Check browser console (F12) for CORS errors
# If CORS error, verify FastAPI has CORS configured
```

### Issue: "Strapi API token invalid"

**Symptom:** Cofounder Agent can't publish content to Strapi

**Solution:**

```powershell
# 1. Generate new Strapi API token
# - Visit http://localhost:1337/admin
# - Settings → API Tokens → Create new
# - Copy full token (don't share this in code!)

# 2. Update .env files
# In src/cofounder_agent/.env:
STRAPI_API_TOKEN=your-new-token

# In web/public-site/.env.local:
STRAPI_API_TOKEN=your-new-token

# 3. Restart services
```

### Issue: "No models available in Ollama"

**Symptom:** Cofounder Agent returns "No models found"

**Solution:**

```powershell
# 1. Verify Ollama is running
curl http://localhost:11434/api/tags

# 2. If no models, pull one
ollama pull mistral
# Or: ollama pull llama2, ollama pull neural-chat

# 3. Verify model is available
ollama list

# 4. Update .env
OLLAMA_MODEL=mistral

# 5. Restart Cofounder Agent
```

### Issue: "Blog post created but not showing on Public Site"

**Symptom:** Post appears in Strapi but not on http://localhost:3000

**Solution:**

```powershell
# 1. Verify post is published (not draft)
# In Strapi admin: Content Manager → Posts
# Check that your post has "Published" status

# 2. Verify Public Site can access Strapi
curl "http://localhost:3000/api/posts" -H "Authorization: Bearer YOUR_TOKEN"

# 3. Check for ISR cache issues
# Delete .next cache and rebuild
cd web/public-site
rm -r .next
npm run build

# 4. Clear browser cache (Ctrl+Shift+Delete)
```

### Issue: "Task stuck at 'generating' status"

**Symptom:** Blog post generation never completes

**Solution:**

```powershell
# 1. Check Cofounder Agent logs for errors
# Look for exception messages or model errors

# 2. Verify Ollama is responding
curl http://localhost:11434/api/generate -X POST -d '{
  "model": "mistral",
  "prompt": "Hello",
  "stream": false
}'

# 3. Check database connection
# In Cofounder Agent logs, should see "Database connected"

# 4. Restart Cofounder Agent
cd src/cofounder_agent
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## 📈 Next Steps

Once integration is complete:

1. **Automation**: Set up scheduled blog post generation
2. **Analytics**: Track post views and engagement via Strapi
3. **Quality Monitoring**: Set up alerts for low-quality scores
4. **Multi-language**: Configure content generation in different languages
5. **Custom Models**: Fine-tune Ollama models for your domain
6. **Webhooks**: Set up real-time notifications for task completion
7. **Testing**: Create full end-to-end test suite

---

## 📚 Quick Reference

### Important Endpoints

```bash
# Cofounder Agent
POST   /api/content/blog-posts              # Create blog task
GET    /api/content/blog-posts/tasks/{id}   # Get task status
GET    /api/content/blog-posts/drafts       # List drafts
POST   /api/content/blog-posts/drafts/{id}/publish  # Publish draft

# Strapi
GET    /api/posts                           # List posts
POST   /api/posts                           # Create post
GET    /api/posts/{id}                      # Get post details
PUT    /api/posts/{id}                      # Update post
DELETE /api/posts/{id}                      # Delete post

# Public Site (Next.js API routes)
GET    /api/posts                           # Fetch posts for frontend
```

### Environment Variables

**Oversight Hub** (`web/oversight-hub/.env.local`):

```
REACT_APP_API_URL=http://localhost:8000
```

**Cofounder Agent** (`src/cofounder_agent/.env`):

```
STRAPI_BASE_URL=http://localhost:1337
STRAPI_API_TOKEN=<your-token>
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=mistral
```

**Public Site** (`web/public-site/.env.local`):

```
NEXT_PUBLIC_STRAPI_API_URL=http://localhost:1337
STRAPI_API_TOKEN=<your-token>
```

---

## ✨ Success Criteria

You'll know the integration is working when:

1. ✅ Oversight Hub → Create blog post task
2. ✅ Cofounder Agent → Receives task, generates content, publishes to Strapi
3. ✅ Strapi CMS → Post appears in Content Manager
4. ✅ Public Site → Post visible on homepage and blog pages
5. ✅ Quality Score → Displayed in Oversight Hub task status
6. ✅ Full Workflow → Takes <5 minutes from task creation to publication

---

**Ready to integrate? Start with STEP 1 above!** 🚀
