# Complete Database Migration: tasks → content_tasks

**Date:** November 12, 2025  
**Status:** Ready to Execute  
**Impact:** All frontend task management features

---

## 🎯 Executive Summary

Glad Labs has **two separate database tables** for tasks:

| Aspect           | `tasks` (Legacy)             | `content_tasks` (Active)         |
| ---------------- | ---------------------------- | -------------------------------- |
| **Primary Key**  | `id` (UUID)                  | `task_id` (varchar)              |
| **Row Count**    | 132 rows                     | ~20 rows                         |
| **Last Updated** | 2025-11-11 00:32:03          | 2025-11-13 02:02:11              |
| **Status**       | STALE - Not in use           | ACTIVE - Current                 |
| **Purpose**      | Legacy FastAPI test data     | Active content generation        |
| **API Endpoint** | `/api/tasks/{id}`            | `/api/content/blog-posts/drafts` |
| **Key Problem**  | All `result` fields are NULL | All `content` fields are NULL    |

**Root Cause of Empty Preview:**

1. TaskManagement.jsx fetches list from `/api/content/blog-posts/drafts` ✅ (CORRECT)
2. When View Details clicked, calls `/api/tasks/{taskId}` to get full data ❌ (WRONG)
3. `/api/tasks/{taskId}` returns data from STALE `tasks` table, which has NULL `result` field
4. ResultPreviewPanel receives NULL data, so nothing displays

---

## 🔍 Complete Field Mapping

### `content_tasks` Table (ACTIVE - Used by Content Creation Pipeline)

**Core Task Fields:**

- `task_id` (PK): blog_20251113_52c861ee (varchar 64)
- `request_type`: "blog_post" (varchar 50)
- `status`: "completed" | "draft" | "error" (varchar 50)
- `topic`: "AI in Gaming" (varchar 500)
- `created_at`: 2025-11-13 02:02:11 (timestamp)
- `updated_at`: 2025-11-13 02:02:11 (timestamp)
- `completed_at`: NULL | timestamp (timestamp)

**Content Fields:**

- `content`: NULL ❌ (Should contain generated blog post markdown)
- `excerpt`: NULL ❌ (Should contain summary)
- `featured_image_prompt`: JSON task metadata
- `featured_image_url`: NULL (Image URL if generated)
- `featured_image_data`: JSON image data

**Configuration Fields:**

- `style`: "technical" (varchar 50)
- `tone`: "professional" (varchar 50)
- `target_length`: 1501 (integer)

**Publishing Fields:**

- `publish_mode`: "draft" | "published" (varchar 50)
- `strapi_id`: NULL (Strapi CMS document ID after publish)
- `strapi_url`: NULL (Strapi public URL)

**Metadata & Quality:**

- `tags`: JSON array of tags
- `task_metadata`: JSON object with request parameters
- `model_used`: LLM model identifier
- `quality_score`: 0-100
- `progress`: JSON with generation progress
- `error_message`: NULL | error description

---

### `tasks` Table (LEGACY - Should be Dropped)

**Fields:**

- `id`: UUID (Primary key)
- `task_name`: varchar 255
- `agent_id`: varchar 255
- `status`: varchar 50
- `topic`: varchar 255
- `primary_keyword`: varchar 255
- `target_audience`: varchar 255
- `category`: varchar 255
- `created_at`: timestamp with timezone
- `updated_at`: timestamp with timezone
- `started_at`: timestamp with timezone
- `completed_at`: timestamp with timezone
- `task_metadata`: jsonb
- `result`: jsonb (ALL NULL - NO DATA)
- `user_id`: varchar 255
- `metadata`: jsonb

**Status:** 132 rows, all from Nov 11, 2025 FastAPI test runs. **NOT USED BY CURRENT SYSTEM.**

---

## 🔧 Problems to Fix

### Problem 1: Preview Panel Shows Empty

**Current Flow:**

```
User clicks "View Details" on row
  → setSelectedTask(task) directly from table list
  → Task object only has: id, task_name, topic, status, created_at, word_count, summary
  → ResultPreviewPanel opens but has NO content field
  → Shows "No task selected" or empty content
```

**Why:**

- The table list comes from `/api/content/blog-posts/drafts` which returns a LIMITED response
- Limited response doesn't include full `content`, `excerpt`, or `featured_image_url`
- We need to fetch the FULL draft object from a dedicated endpoint

### Problem 2: fetchContentTaskStatus Uses Wrong Endpoint

**Current Code:**

```javascript
const response = await fetch(
  `http://localhost:8000/api/tasks/${taskId}`, // ❌ Wrong - legacy table
  { headers, signal: AbortSignal.timeout(5000) }
);
const data = await response.json();
return {
  status: data.status || 'completed',
  result: data.result || {}, // ❌ Always NULL from tasks table
  error_message: data.error_message,
};
```

**Should Be:**

```javascript
const response = await fetch(
  `http://localhost:8000/api/content/blog-posts/drafts/${taskId}`, // ✅ Correct
  { headers, signal: AbortSignal.timeout(5000) }
);
```

### Problem 3: Data Not Persisted to `content_tasks.content`

**Issue:** Generated content isn't being saved to the database

- Status: `completed` (generation finished)
- `content`: NULL (but should have markdown)
- `excerpt`: NULL (but should have summary)

**Root Cause:** Backend task orchestration needs to save generated content to `content_tasks.content` field

---

## ✅ Solutions

### Solution 1: Fix fetchContentTaskStatus Endpoint

**File:** `web/oversight-hub/src/components/tasks/TaskManagement.jsx` (line 60)

**Change From:**

```javascript
const response = await fetch(`http://localhost:8000/api/tasks/${taskId}`, {
  headers,
  signal: AbortSignal.timeout(5000),
});
```

**Change To:**

```javascript
const response = await fetch(
  `http://localhost:8000/api/content/blog-posts/drafts/${taskId}`,
  { headers, signal: AbortSignal.timeout(5000) }
);
```

**Why:** Direct query to `content_tasks` table instead of stale `tasks` table

---

### Solution 2: Ensure Backend Persists Content

**File:** `src/cofounder_agent/routes/content_routes.py` (or wherever content is generated)

**Must Do:**

1. After content generation completes
2. Save to `content_tasks.content` field (not just in response)
3. Also save `excerpt` (summary)
4. Also save `featured_image_url` if image generated

**SQL Should Include:**

```sql
UPDATE content_tasks
SET
  content = %s,
  excerpt = %s,
  featured_image_url = %s,
  status = 'completed',
  completed_at = NOW(),
  updated_at = NOW()
WHERE task_id = %s;
```

---

### Solution 3: Drop Legacy `tasks` Table

**Only After Confirming:**

1. ✅ All code references `/api/content/blog-posts/drafts`
2. ✅ No code queries the `tasks` table directly
3. ✅ Generated content is being saved to `content_tasks.content`
4. ✅ ResultPreviewPanel receives populated data
5. ✅ All CRUD operations tested and working

**Command:**

```sql
DROP TABLE IF EXISTS tasks CASCADE;
```

---

## 📋 Verification Checklist

Before dropping `tasks` table, verify:

- [ ] `fetchContentTaskStatus()` uses `/api/content/blog-posts/drafts/{taskId}` endpoint
- [ ] `/api/tasks` endpoint is NOT called anywhere in frontend code
- [ ] Backend saves generated content to `content_tasks.content`
- [ ] Backend saves excerpt to `content_tasks.excerpt`
- [ ] Backend saves featured image URL to `content_tasks.featured_image_url`
- [ ] ResultPreviewPanel displays populated content when clicking View Details
- [ ] Delete operation works ✅ (already verified)
- [ ] Approve & Publish operation will work once content is populated
- [ ] No database foreign keys reference `tasks` table
- [ ] No API endpoints return data from `tasks` table

---

## 🔄 Complete Workflow After Fixes

```
1. User creates blog post via CreateTaskModal
   → POST /api/content/blog-posts
   → Saves to content_tasks table
   → Task ID: blog_20251113_XXXXX

2. AI generates content in background
   → Saves to content_tasks.content
   → Saves excerpt to content_tasks.excerpt
   → Sets status = 'completed'

3. TaskManagement table shows draft
   → GET /api/content/blog-posts/drafts?limit=100
   → Returns list of all drafts
   → User sees task in table

4. User clicks "View Details"
   → Calls fetchContentTaskStatus(task.id)
   → Fetches from /api/content/blog-posts/drafts/{taskId}
   → Returns full task with content, excerpt, images
   → ResultPreviewPanel displays populated content

5. User clicks "Approve & Publish"
   → POST /api/content/blog-posts/drafts/{id}/publish
   → Publishes to Strapi CMS
   → Updates strapi_id and strapi_url
   → Task moves to published state

6. Drop legacy tasks table
   → No code references it anymore
   → All data migrated to content_tasks
```

---

## 🚀 Implementation Order

1. **FIRST:** Fix `fetchContentTaskStatus()` endpoint in TaskManagement.jsx
2. **SECOND:** Verify backend is saving content to database
3. **THIRD:** Test ResultPreviewPanel displays content
4. **FOURTH:** Test Approve & Publish workflow
5. **LAST:** Drop `tasks` table only when confirmed safe

---

## 📊 Field Usage by Component

### ResultPreviewPanel (Line 25-50 initialization)

**Needs:**

- `title` or `topic` (for display)
- `content` (for markdown preview)
- `excerpt` (metadata)
- `featured_image_url` (if available)
- `seo` object (title, description, keywords)

**Currently Gets:** NULL

**After Fix:** Full data from `content_tasks`

### TaskManagement Table

**Gets:** Basic list from `/api/content/blog-posts/drafts`

- `draft_id` → `id`
- `title` → `task_name`
- `status` → `status`
- `created_at` → `created_at`

**Status:** ✅ WORKING

### Approve Button Handler (ResultPreviewPanel.jsx line 895-932)

**Uses:** Full task object with edited content
**Sends:** POST to `/api/content/blog-posts/drafts/{id}/publish`
**Needs:** Backend to accept and process

---

## 🎯 Success Criteria

- [x] Delete button works (VERIFIED ✅)
- [ ] View Details opens ResultPreviewPanel
- [ ] ResultPreviewPanel shows generated content
- [ ] Approve & Publish sends to Strapi
- [ ] Task status updates after publish
- [ ] tasks table confirmed not in use
- [ ] tasks table can be safely dropped

---

## 📞 Additional Notes

**Why Two Tables Exist:**

- `tasks`: Legacy from initial FastAPI orchestration (Nov 11)
- `content_tasks`: New table for content-specific workflows (Nov 13+)
- Database evolved, old table not cleaned up yet

**Data Discrepancy:**

- `tasks` has 132 rows of test data
- `content_tasks` has ~20 rows of actual content
- Both have NULL result/content fields (different problem - backend not saving)

**Why Not Just One Table:**

- Could consolidate, but `content_tasks` schema is more specialized
- Better to keep content-specific table, drop legacy table
- Less refactoring needed
