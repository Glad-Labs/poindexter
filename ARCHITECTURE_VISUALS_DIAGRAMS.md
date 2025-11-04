# 🎨 Visual Architecture Diagrams

## Current vs Proposed

### ❌ CURRENT STATE (Broken)

```
┌─────────────────────────────────────────────────────┐
│ OVERSIGHT HUB (React)                               │
│ User clicks "Generate Blog Post"                    │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
            POST /api/tasks (REST)
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   ✅ WORKS              ❌ DOESN'T HAPPEN
   PostgreSQL            Task Processing
   tasks table           (Independent system)
   Record created           │
   task_id = abc-123        │
                            ▼
                     /api/content/blog-posts
                     (Different endpoint!)
                            │
                            ├─ Generate content
                            ├─ Search images
                            ├─ Publish to Strapi
                            └─ Store results

                     User has to call
                     different API!
```

**Problem:** Two separate systems that don't talk to each other

---

### ✅ PROPOSED STATE (Fixed)

```
                    ┌─────────────────────────────────────────────────────┐
                    │ OVERSIGHT HUB (React)                               │
                    │ - Input form (topic, style, tone, length)           │
                    │ - Real-time progress bar (0-100%)                   │
                    │ - Verbose logs panel                                │
                    │ - Quality scores displayed live                     │
                    │ - WebSocket connection                              │
                    └──────────────────────┬──────────────────────────────┘
                                           │
                          ┌────────────────┼────────────────┐
                          │                │                │
                   (REST) │         (WebSocket)        (Fallback: REST polling)
                          │                │                │
    ┌─────────────────────▼┐    ┌─────────▼──────┐   ┌──────▼──────────────┐
    │ POST /api/tasks      │    │  WS /ws/{id}   │   │ GET /api/tasks/{id} │
    │ - Validate input     │    │  (Real-time)   │   │ (5sec polling)      │
    │ - Create PostgreSQL  │    │                │   └─────────────────────┘
    │ - Add to Redis queue │    └────────┬───────┘
    │ - Return task_id     │             │
    └──────────┬───────────┘             │
               │                         │ Updates pushed
               │                         ▼
    ┌──────────▼────────────────────────────────────────────┐
    │         FASTAPI ORCHESTRATOR                          │
    │  ┌────────────────────────────────────────────────┐  │
    │  │ Background Worker (Async Task)                │  │
    │  │  for each task in redis_queue:                │  │
    │  │  ├─ STAGE 1: Generate Content (25%)           │  │
    │  │  │  ├─ Call AIContentGenerator                 │  │
    │  │  │  ├─ Capture validation results             │  │
    │  │  │  ├─ Quality score: 6.5/10                  │  │
    │  │  │  ├─ Issues: [missing examples, ...]        │  │
    │  │  │  ├─ Push logs: Ollama output               │  │
    │  │  │  └─ Update Redis: progress 0% → 25%        │  │
    │  │  │                                             │  │
    │  │  ├─ STAGE 2: Search Images (50%)              │  │
    │  │  │  ├─ Get featured image URL                 │  │
    │  │  │  ├─ Push logs: Image found                 │  │
    │  │  │  └─ Update Redis: progress 25% → 50%       │  │
    │  │  │                                             │  │
    │  │  ├─ STAGE 3: Publish to Strapi (75%)          │  │
    │  │  │  ├─ POST to Strapi API                     │  │
    │  │  │  ├─ Get Strapi post ID                     │  │
    │  │  │  ├─ Push logs: Published to CMS            │  │
    │  │  │  └─ Update Redis: progress 50% → 75%       │  │
    │  │  │                                             │  │
    │  │  └─ STAGE 4: Finalize (100%)                  │  │
    │  │     ├─ Update PostgreSQL with results         │  │
    │  │     ├─ Store quality metrics                  │  │
    │  │     ├─ Clear Redis entry                      │  │
    │  │     ├─ Push logs: Task complete               │  │
    │  │     └─ Update Redis: progress 100%            │  │
    │  │                                               │  │
    │  └────────────────────────────────────────────────┘  │
    └──────────┬──────────────────────────────────────────┘
               │
        ┌──────┴──────────┬──────────────┬──────────────┐
        ▼                 ▼              ▼              ▼
    ┌────────┐    ┌─────────────┐  ┌──────────┐  ┌─────────────┐
    │ Redis  │    │PostgreSQL   │  │ Strapi   │  │ Ollama API  │
    │ Queue  │    │ Database    │  │ CMS      │  │ (Local AI)  │
    │        │    │             │  │          │  │             │
    │ Status │    │ - tasks     │  │ - Create │  │ Generate    │
    │Progress│    │ - results   │  │   posts  │  │ text        │
    │  Logs  │    │ - metrics   │  │ - Store  │  │ Validate    │
    └────────┘    │             │  │   media  │  │ Quality     │
                  │             │  │          │  │ Score       │
                  └─────────────┘  └──────────┘  └─────────────┘
```

---

## Task Lifecycle Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     TASK LIFECYCLE                               │
└──────────────────────────────────────────────────────────────────┘

1. USER CREATES TASK (Oversight Hub)
   ┌─────────────────────────────────────┐
   │ Topic: "AI in Business"             │
   │ Style: professional                 │
   │ Tone: formal                        │
   │ Length: 1500 words                  │
   └─────┬───────────────────────────────┘
         │ Click "Generate"
         ▼
   ┌──────────────────────────────────────────────┐
   │ POST /api/tasks (REST)                       │
   │ Returns: {"id": "abc-123", "status": "queued"}
   └──────────────────────────────────────────────┘

2. TASK QUEUED
   ┌──────────────────────────────────────────────┐
   │ PostgreSQL                                   │
   │ INSERT INTO tasks (...)                      │
   │ status = "pending"                           │
   │ created_at = 2025-11-03 10:00:00             │
   └──────┬───────────────────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────────────────┐
   │ Redis                                        │
   │ PUSH tasks:queue:normal "abc-123"            │
   │ SET tasks:progress:abc-123                   │
   │  {status: "queued", percent: 0}              │
   └──────────────────────────────────────────────┘

3. BACKGROUND WORKER PICKS UP TASK
   ┌──────────────────────────────────────────────┐
   │ Worker polls Redis queue                     │
   │ POP tasks:queue:normal → "abc-123"           │
   │ GET tasks:progress:abc-123 →                 │
   │  {status: "in_progress", stage: "generating"}
   └──────────────────────────────────────────────┘

4. STAGE 1: CONTENT GENERATION
   ┌──────────────────────────────────────────────┐
   │ AIContentGenerator.generate_blog_post()      │
   │ - Try Ollama:mistral                         │
   │ - Generate initial draft (250 tokens)        │
   │ - Validate: quality_score = 6.2/10 ❌        │
   │ - Issues: ["missing examples", "weak CTA"]   │
   │ - Attempt refinement                         │
   │ - Regenerate (400 tokens)                    │
   │ - Validate: quality_score = 8.5/10 ✅        │
   │ - APPROVED!                                  │
   └──────┬───────────────────────────────────────┘
          │
          ▼ Push logs to Redis
   ┌──────────────────────────────────────────────┐
   │ Redis Stream (tasks:logs:abc-123)            │
   │ [10:00:15] Ollama: Loading mistral...        │
   │ [10:00:20] FastAPI: Generation started       │
   │ [10:00:45] Validator: Quality 6.2/10 ❌      │
   │ [10:00:46] Issues: missing examples, weak CTA
   │ [10:00:47] Refining...                       │
   │ [10:01:15] Validator: Quality 8.5/10 ✅      │
   │ [10:01:16] Approved!                         │
   └──────────────────────────────────────────────┘
          │
          ▼ Update progress
   ┌──────────────────────────────────────────────┐
   │ Redis (tasks:progress:abc-123)               │
   │ {                                            │
   │   status: "in_progress",                     │
   │   stage: "generating_content",               │
   │   percent: 25,                               │
   │   quality_score: 8.5,                        │
   │   issues: []                                 │
   │ }                                            │
   └──────────────────────────────────────────────┘
          │
          ▼ WebSocket push to Frontend
   ┌──────────────────────────────────────────────┐
   │ Oversight Hub receives:                      │
   │ Progress: 25%                                │
   │ Stage: "Generating content"                  │
   │ Quality: 8.5/10 ✅                           │
   │ Status: No issues found                      │
   │ [Logs panel updates]                         │
   └──────────────────────────────────────────────┘

5. STAGE 2: IMAGE SEARCH
   ┌──────────────────────────────────────────────┐
   │ FeaturedImageService.search_featured_image() │
   │ - Search Pexels for "AI in business"         │
   │ - Found: "office-AI-collaboration.jpg"       │
   │ - URL: pexels.com/photo/12345                │
   └──────┬───────────────────────────────────────┘
          ▼
   ┌──────────────────────────────────────────────┐
   │ Redis (tasks:progress:abc-123)               │
   │ {                                            │
   │   status: "in_progress",                     │
   │   stage: "searching_images",                 │
   │   percent: 50                                │
   │ }                                            │
   └──────────────────────────────────────────────┘

6. STAGE 3: STRAPI PUBLISHING
   ┌──────────────────────────────────────────────┐
   │ StrapiPublishingService.publish_blog_post()  │
   │ - POST /api/articles                         │
   │ - Title: "AI in Business"                    │
   │ - Content: (1500 words)                      │
   │ - Featured: "pexels.com/photo/12345"         │
   │ - Tags: ["AI", "business"]                   │
   │ - Response: {id: "post-456", url: "..."}     │
   └──────┬───────────────────────────────────────┘
          ▼
   ┌──────────────────────────────────────────────┐
   │ Redis (tasks:progress:abc-123)               │
   │ {                                            │
   │   status: "in_progress",                     │
   │   stage: "publishing_to_strapi",             │
   │   percent: 75                                │
   │ }                                            │
   └──────────────────────────────────────────────┘

7. STAGE 4: FINALIZE
   ┌──────────────────────────────────────────────┐
   │ PostgreSQL (UPDATE tasks SET...)             │
   │ status = "completed"                         │
   │ result = {                                   │
   │   content: "...",                            │
   │   quality_score: 8.5,                        │
   │   strapi_post_id: "post-456",                │
   │   strapi_url: "https://...",                 │
   │   featured_image: "pexels.com/...",          │
   │   completed_at: "2025-11-03T10:02:30Z"       │
   │ }                                            │
   │ completed_at = 2025-11-03 10:02:30           │
   └──────┬───────────────────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────────────────┐
   │ Redis (tasks:progress:abc-123)               │
   │ {                                            │
   │   status: "completed",                       │
   │   percent: 100                               │
   │ }                                            │
   │                                              │
   │ Then DELETE tasks:result:abc-123             │
   │ (Clean up Redis entry)                       │
   └──────────────────────────────────────────────┘
          │
          ▼ Final update
   ┌──────────────────────────────────────────────┐
   │ Oversight Hub receives:                      │
   │ ✅ Task Complete!                            │
   │ Status: Success                              │
   │ Quality: 8.5/10                              │
   │ Post URL: https://cms.../posts/abc-123      │
   │ Time: 2 min 30 sec                           │
   │ [Button: "View in Strapi" + "Edit"]          │
   └──────────────────────────────────────────────┘
```

---

## Log Streaming Flow

```
┌─────────────────────────────────────────────────────────────┐
│ LOGS AT EACH STAGE - WHAT FRONTEND SEES                     │
└─────────────────────────────────────────────────────────────┘

STAGE 1: CONTENT GENERATION
└─ Source: Ollama + FastAPI + Validator
   │
   ├─ [10:00:15.234] Ollama: Connecting to http://localhost:11434
   ├─ [10:00:15.456] Ollama: Model loaded: mistral:latest
   ├─ [10:00:15.789] FastAPI: Generation started
   ├─ [10:00:16.001] FastAPI: Prompt: (245 tokens)
   ├─ [10:00:45.123] Ollama: Response complete
   ├─ [10:00:45.234] Ollama: Generated (1250 tokens)
   ├─ [10:00:45.456] Validator: Checking content quality
   ├─ [10:00:45.567] Validator: - Word count: 1240 ✓
   ├─ [10:00:45.678] Validator: - Structure: 3 headings, 5 sections ✓
   ├─ [10:00:45.789] Validator: - Examples: 2 found ✓
   ├─ [10:00:45.890] Validator: - CTA: weak (missing strong action) ✗
   ├─ [10:00:46.001] Validator: Quality Score: 6.2/10 ❌
   ├─ [10:00:46.112] Validator: Issues:
   │                 - Missing strong call-to-action
   │                 - Could include more real-world examples
   ├─ [10:00:46.223] Refining: Attempting improvement #1/3
   ├─ [10:00:46.334] FastAPI: Refinement prompt sent
   ├─ [10:00:46.445] FastAPI: Focusing on: "Add stronger CTA, more examples"
   ├─ [10:01:15.567] Ollama: Refinement complete
   ├─ [10:01:15.678] Validator: Re-checking refined content
   ├─ [10:01:15.789] Validator: Quality Score: 8.5/10 ✅
   ├─ [10:01:15.890] Validator: All checks passed!
   └─ [10:01:16.001] Generation: SUCCESS after 1 refinement

STAGE 2: IMAGE SEARCH
└─ Source: FeaturedImageService
   │
   ├─ [10:01:20.123] Pexels: Searching for "AI in business"
   ├─ [10:01:20.500] Pexels: Found 247 results
   ├─ [10:01:20.600] Pexels: Filtering for relevance
   ├─ [10:01:20.700] Pexels: Top result: "office-collaboration.jpg"
   ├─ [10:01:20.800] Pexels: Photographer: "John Smith"
   └─ [10:01:20.900] Image: SUCCESS

STAGE 3: STRAPI PUBLISHING
└─ Source: StrapiClient
   │
   ├─ [10:01:25.123] Strapi: Connecting to https://cms.railway.app/api
   ├─ [10:01:25.456] Strapi: Authentication successful
   ├─ [10:01:25.678] Strapi: Creating article
   ├─ [10:01:25.890] Strapi: - Title: "AI in Business" (42 chars)
   ├─ [10:01:26.001] Strapi: - Content: (1250 words, formatted)
   ├─ [10:01:26.112] Strapi: - Tags: ["AI", "business", "automation"]
   ├─ [10:01:26.223] Strapi: - Featured image: pexels.com/photo/12345
   ├─ [10:01:26.334] Strapi: POST /articles
   ├─ [10:01:26.789] Strapi: Response: 201 Created
   ├─ [10:01:26.890] Strapi: Post ID: 456
   ├─ [10:01:27.001] Strapi: Post URL: https://cms.../articles/456
   └─ [10:01:27.112] Publishing: SUCCESS

COMPLETION
└─
   ├─ [10:01:30.123] Task: Saving results to PostgreSQL
   ├─ [10:01:30.456] Database: task updated (status=completed)
   ├─ [10:01:30.567] Database: storing metrics
   ├─ [10:01:30.678] Cache: cleaning up Redis
   ├─ [10:01:30.789] Task: All cleanup complete
   └─ [10:01:30.900] ✅ COMPLETE in 90 seconds
```

---

## Real-Time WebSocket Message Examples

```json
// Message 1: Task started
{
  "type": "started",
  "task_id": "abc-123",
  "timestamp": "2025-11-03T10:00:00Z",
  "message": "Task started - processing queued content request"
}

// Message 2: Progress update
{
  "type": "progress",
  "task_id": "abc-123",
  "stage": "generating_content",
  "percent": 15,
  "timestamp": "2025-11-03T10:00:30Z",
  "message": "Content generation in progress (Ollama:mistral)"
}

// Message 3: Quality score update
{
  "type": "quality",
  "task_id": "abc-123",
  "attempt": 1,
  "quality_score": 6.2,
  "is_passing": false,
  "issues": [
    "Missing strong call-to-action",
    "Could include more real-world examples"
  ],
  "feedback": "✗ Content needs improvement (6.2/10, threshold: 7.0)",
  "timestamp": "2025-11-03T10:00:45Z"
}

// Message 4: Refinement started
{
  "type": "refinement_started",
  "task_id": "abc-123",
  "attempt": 1,
  "timestamp": "2025-11-03T10:00:46Z",
  "message": "Attempting refinement based on feedback"
}

// Message 5: Quality improved
{
  "type": "quality",
  "task_id": "abc-123",
  "attempt": 2,
  "quality_score": 8.5,
  "is_passing": true,
  "feedback": "✓ Content approved (quality score: 8.5/10)",
  "timestamp": "2025-11-03T10:01:15Z"
}

// Message 6: Stage complete
{
  "type": "stage_complete",
  "task_id": "abc-123",
  "stage": "generating_content",
  "percent": 25,
  "result": {
    "content_length": 1250,
    "final_quality_score": 8.5,
    "refinements_used": 1,
    "model_used": "Ollama - mistral",
    "total_time_seconds": 75
  },
  "timestamp": "2025-11-03T10:01:16Z"
}

// Message 7: Progress to stage 2
{
  "type": "progress",
  "task_id": "abc-123",
  "stage": "searching_images",
  "percent": 35,
  "timestamp": "2025-11-03T10:01:20Z",
  "message": "Searching for featured images"
}

// Message 8: Image found
{
  "type": "image_found",
  "task_id": "abc-123",
  "image_url": "https://images.pexels.com/photo/12345.jpeg",
  "photographer": "John Smith",
  "timestamp": "2025-11-03T10:01:21Z"
}

// Message 9: Publishing started
{
  "type": "progress",
  "task_id": "abc-123",
  "stage": "publishing_to_strapi",
  "percent": 50,
  "timestamp": "2025-11-03T10:01:25Z",
  "message": "Publishing content to Strapi CMS"
}

// Message 10: Published success
{
  "type": "published",
  "task_id": "abc-123",
  "strapi_post_id": "post-456",
  "strapi_url": "https://cms.railway.app/admin/content-manager/collection-types/api::article.article/456",
  "public_url": "https://example.com/blog/ai-in-business",
  "timestamp": "2025-11-03T10:01:27Z"
}

// Message 11: Task complete
{
  "type": "complete",
  "task_id": "abc-123",
  "status": "success",
  "summary": {
    "total_time_seconds": 90,
    "content_quality_score": 8.5,
    "refinements_used": 1,
    "strapi_post_id": "post-456",
    "public_url": "https://example.com/blog/ai-in-business"
  },
  "timestamp": "2025-11-03T10:02:30Z"
}

// Message 12: Error example
{
  "type": "error",
  "task_id": "abc-123",
  "stage": "publishing_to_strapi",
  "error_message": "Connection timeout to Strapi API after 30 seconds",
  "recovery": "Retrying... (attempt 1/3)",
  "timestamp": "2025-11-03T10:01:35Z"
}
```

---

## Frontend Component Structure

```jsx
<Oversight Hub>
  <ContentGenerator>
    ├─ <GenerationForm>
    │  ├─ Topic input
    │  ├─ Style dropdown
    │  ├─ Tone dropdown
    │  ├─ Length slider
    │  └─ Tags input
    │
    ├─ <ProgressCard> (while task running)
    │  ├─ Task ID
    │  ├─ Progress Bar (0-100%)
    │  ├─ Stage Indicator
    │  ├─ Quality Score Display
    │  │  ├─ Current: 8.5/10 ✅
    │  │  ├─ Issues: (if any)
    │  │  └─ Feedback: "Content approved"
    │  │
    │  ├─ <LogsPanel> (scrollable)
    │  │  ├─ [10:00:15] Ollama: Loading model
    │  │  ├─ [10:00:45] Validator: Quality 6.2/10 ❌
    │  │  ├─ [10:00:46] Refining...
    │  │  ├─ [10:01:15] Validator: Quality 8.5/10 ✅
    │  │  └─ [More logs...]
    │  │
    │  ├─ Source Tabs
    │  │  ├─ FastAPI logs
    │  │  ├─ Ollama logs
    │  │  ├─ Strapi API logs
    │  │  └─ Combined (default)
    │  │
    │  └─ Metrics Summary
    │     ├─ Generation time: 75s
    │     ├─ Image search: 5s
    │     ├─ Strapi publish: 10s
    │     └─ Total: 90s
    │
    └─ <CompletionCard> (after task done)
       ├─ ✅ Success!
       ├─ Quality: 8.5/10
       ├─ Post Title: "AI in Business"
       ├─ Featured Image: [thumbnail]
       ├─ [Button: View in Strapi]
       ├─ [Button: View Public Post]
       └─ [Button: Edit in CMS]
```

---

## Database Record Evolution

```sql
-- Initial creation (POST /api/tasks)
INSERT INTO tasks (
  id, task_name, topic, primary_keyword, target_audience,
  category, status, agent_id, user_id, metadata,
  created_at, updated_at, started_at, completed_at, result
) VALUES (
  'abc-123',
  'Generate blog post: AI in Business',
  'AI in Business',
  'artificial intelligence',
  'business owners',
  'technology',
  'pending',          ← Status: pending
  'content_agent',
  'user-001',
  '{"style":"professional","tone":"formal","length":1500,"tags":["AI","business"]}',
  '2025-11-03 10:00:00',
  '2025-11-03 10:00:00',
  NULL,               ← Not started yet
  NULL,               ← Not completed yet
  NULL                ← No result yet
);

-- After processing begins (background worker picks up)
UPDATE tasks SET
  status = 'in_progress',
  started_at = '2025-11-03 10:00:15',
  updated_at = '2025-11-03 10:00:15'
WHERE id = 'abc-123';

-- After generation complete
UPDATE tasks SET
  status = 'in_progress',
  metadata = jsonb_set(metadata, '{last_stage}', '"generating_content"'),
  updated_at = '2025-11-03 10:01:16'
WHERE id = 'abc-123';

-- After publishing complete
UPDATE tasks SET
  status = 'completed',
  completed_at = '2025-11-03 10:02:30',
  result = '{
    "content": "...",
    "quality_score": 8.5,
    "final_quality_score": 8.5,
    "model_used": "Ollama - mistral",
    "strapi_post_id": "post-456",
    "strapi_url": "https://cms.../article/456",
    "featured_image_url": "https://pexels.com/photo/12345",
    "generation_time_seconds": 75,
    "image_search_time_seconds": 5,
    "strapi_publish_time_seconds": 10,
    "total_time_seconds": 90,
    "validation_results": [
      {
        "attempt": 1,
        "score": 6.2,
        "issues": ["weak CTA", "missing examples"],
        "passed": false
      },
      {
        "attempt": 2,
        "score": 8.5,
        "issues": [],
        "passed": true,
        "refinement": true
      }
    ]
  }',
  updated_at = '2025-11-03 10:02:30'
WHERE id = 'abc-123';
```

---

## Error Recovery Flow

```
┌─────────────────────────────────────────────────────────┐
│ ERROR SCENARIO: Strapi API Connection Timeout           │
└─────────────────────────────────────────────────────────┘

State: Stage 3 (Publishing) at 60% progress
│
▼
[10:01:35] Error: Connection timeout to Strapi API (30s)
[10:01:35] Recovery: Retrying with exponential backoff (attempt 1/3)
│
▼ Wait 2 seconds
[10:01:37] Strapi: Reconnecting...
[10:01:37] Strapi: Authentication successful
[10:01:37] Strapi: Retrying POST /articles
│
├─ Success?
│  YES → [10:01:40] Published ✅ (continue to stage 4)
│  NO → [10:01:40] Error again, retry 2/3
│
└─ After 3 failed attempts
   [10:01:45] Error: Strapi API permanently unreachable
   [10:01:45] Task Status: FAILED
   [10:01:45] Database: Update task status = "failed"
   [10:01:45] Database: Store error message
   [10:01:45] Frontend: Show error + "Retry" button
   [10:01:45] User: Can retry from Oversight Hub
```
