# 🧪 Endpoint Verification Test Plan

**Last Updated:** November 13, 2025  
**Purpose:** Verify all endpoints and data flow after frontend fixes  
**Status:** Ready to execute

---

## 📋 Test Overview

This document provides step-by-step tests to verify:

1. ✅ Backend endpoints exist and return correct data
2. ✅ Frontend correctly calls endpoints with task IDs
3. ✅ ResultPreviewPanel receives and displays populated data
4. ✅ All CRUD operations work end-to-end
5. ✅ Data persistence through workflow

---

## 🔍 Test Sequence

### Test 1: Create a New Blog Post Task

**Goal:** Generate a task with a valid task_id

**Steps:**

1. Open Oversight Hub: http://localhost:3001
2. Navigate to Task Management
3. Click "Create Task" button
4. Fill in form:
   - Topic: "Test Blog Post for Verification"
   - Style: "Technical"
   - Tone: "Professional"
   - Target Length: "1500"
5. Click "Generate"
6. **Record the task_id** from the console or task table
   - Look for task_id format: `blog_20251113_xxxxxxxx`

**Expected Result:**

- Task appears in table with status "generating" or "completed"
- Console logs show task creation
- Task ID is available for next tests

**Success Criteria:**

- ✅ Task created successfully
- ✅ Task ID captured (format: blog_YYYYMMDD_hash)

---

### Test 2: Verify Backend Endpoint Exists

**Goal:** Confirm `/api/content/blog-posts/tasks/{task_id}` endpoint works

**Steps:**

1. Open browser console (F12)
2. Use the task_id from Test 1
3. Execute this command:

```javascript
// Test the endpoint directly
const taskId = 'blog_20251113_52c861ee'; // Replace with actual task_id
const response = await fetch(
  `http://localhost:8000/api/content/blog-posts/tasks/${taskId}`
);
const data = await response.json();
console.log('✅ Endpoint Response:', JSON.stringify(data, null, 2));
```

**Expected Response Structure:**

```json
{
  "task_id": "blog_20251113_52c861ee",
  "status": "completed",
  "progress": null,
  "result": {
    "title": "Test Blog Post for Verification",
    "content": "Generated blog post content here...",
    "excerpt": "Brief summary...",
    "style": "Technical",
    "tone": "Professional",
    "target_length": 1500,
    "featured_image_url": "...",
    "strapi_post_id": "...",
    ...
  },
  "error": null,
  "created_at": "2025-11-13T02:01:57.743759"
}
```

**Success Criteria:**

- ✅ Endpoint responds with 200 OK
- ✅ Response includes `task_id` field
- ✅ Response includes `status` field
- ✅ Response includes `result` object
- ✅ `result.content` is NOT null (contains generated text)

---

### Test 3: Verify Frontend Calls Correct Endpoint

**Goal:** Confirm TaskManagement.jsx calls the correct endpoint

**Steps:**

1. Open browser DevTools (F12)
2. Go to Network tab
3. Click "View Details" (pencil icon) on any task in Task Management
4. Look for a fetch request to `/api/content/blog-posts/tasks/`
5. Check the response

**Expected Network Request:**

```
GET http://localhost:8000/api/content/blog-posts/tasks/blog_20251113_52c861ee
Status: 200
Response Type: JSON
```

**Success Criteria:**

- ✅ Request goes to `/api/content/blog-posts/tasks/{taskId}` (NOT `/api/tasks/`)
- ✅ Request method is GET
- ✅ Response status is 200
- ✅ Response includes complete task data

---

### Test 4: Verify ResultPreviewPanel Receives Data

**Goal:** Confirm ResultPreviewPanel displays populated content

**Steps:**

1. Click "View Details" on any task
2. Observe the ResultPreviewPanel that opens
3. Check the following fields are populated:
   - Title: Should show blog post title ✅
   - Content: Should show full blog post text ✅
   - Excerpt: Should show summary ✅
   - Featured Image URL: Should show image (if generated) ✅
   - Style: Should show selected style ✅
   - Tone: Should show selected tone ✅

**Console Verification:**
Open browser console and check for logs like:

```
✅ ResultPreviewPanel loaded content from content_tasks: {
  hasContent: true,
  contentLength: 1247,
  title: "Test Blog Post for Verification",
  hasExcerpt: true
}
```

**Success Criteria:**

- ✅ Panel opens without errors
- ✅ Title field is populated
- ✅ Content field shows generated blog post text
- ✅ Excerpt field is populated
- ✅ At least 3 metadata fields show values
- ✅ Console shows "ResultPreviewPanel loaded content" log

---

### Test 5: Verify Data Persists in Database

**Goal:** Confirm generated content is actually saved to database

**Steps:**

1. Use Database IDE or psql to connect to database
2. Run this query:

```sql
SELECT
  task_id,
  topic,
  status,
  LENGTH(content) as content_length,
  excerpt,
  featured_image_url,
  created_at
FROM content_tasks
WHERE task_id = 'blog_20251113_52c861ee'  -- Use actual task_id
ORDER BY created_at DESC
LIMIT 1;
```

**Expected Result:**

```
task_id: blog_20251113_52c861ee
topic: Test Blog Post for Verification
status: completed
content_length: 1247  -- NOT NULL, actual length
excerpt: Brief summary...  -- NOT NULL
featured_image_url: (URL or NULL if not generated)
created_at: 2025-11-13 02:01:57
```

**Success Criteria:**

- ✅ `content` field is NOT NULL
- ✅ `content_length` > 0 (has actual text)
- ✅ `status` = 'completed'
- ✅ `excerpt` is populated
- ✅ `created_at` is recent

---

### Test 6: Edit Task in Preview Panel

**Goal:** Verify edit functionality works

**Steps:**

1. Click "View Details" on any task
2. In ResultPreviewPanel, click "Edit" button
3. Make changes to:
   - Title: Add " [EDITED]" suffix
   - Content: Edit a paragraph
4. Click "Save Changes"
5. Close and reopen the preview panel
6. Verify changes persisted

**Expected Result:**

- Changes are saved
- Reopening preview shows edited content
- Database reflects changes

**Success Criteria:**

- ✅ Edit mode activates
- ✅ Changes can be typed
- ✅ Save button works
- ✅ Changes persist after refresh

---

### Test 7: Test Approve & Publish Button

**Goal:** Verify task can be published to Strapi

**Steps:**

1. In ResultPreviewPanel, click "Approve & Publish"
2. Select target environment (staging/production)
3. Click "Confirm"
4. Monitor for success message

**Expected Result:**

- Task publishes to Strapi
- Status changes to "published"
- strapi_id and strapi_url fields are populated
- Article is visible in Strapi CMS

**Success Criteria:**

- ✅ Publish endpoint responds
- ✅ Task status updates to "published"
- ✅ strapi_id is populated
- ✅ strapi_url is populated
- ✅ Article appears in Strapi admin

---

### Test 8: Verify Delete Still Works

**Goal:** Confirm delete functionality continues to work

**Steps:**

1. Click "View Details" on any task
2. In ResultPreviewPanel, click "Delete" button
3. Confirm deletion
4. Verify task disappears from table

**Expected Result:**

- Task is deleted
- Removed from database
- Table is refreshed

**Success Criteria:**

- ✅ Delete button works
- ✅ Task removed from table
- ✅ No errors
- ✅ Verified in database (query shows gone)

---

## 🐛 Troubleshooting Guide

### Issue: Endpoint Returns 404

**Symptom:** Network shows 404 error on `/api/content/blog-posts/tasks/`

**Causes:**

1. Backend not running on port 8000
2. Endpoint path incorrect in frontend code
3. Task ID doesn't exist

**Fix:**

```bash
# 1. Verify backend is running
curl http://localhost:8000/api/health

# 2. Check task ID is valid
# Go to database and verify task exists
SELECT task_id FROM content_tasks LIMIT 5;

# 3. If endpoint doesn't exist, check routes:
grep -n "blog-posts/tasks" src/cofounder_agent/routes/content_routes.py
```

---

### Issue: ResultPreviewPanel Shows Empty Content

**Symptom:** Panel opens but content area is blank

**Causes:**

1. `content` field is NULL in database
2. Backend returns empty/null result
3. Frontend not parsing response correctly

**Fix:**

```javascript
// Check what the endpoint returns
const response = await fetch(
  'http://localhost:8000/api/content/blog-posts/tasks/blog_20251113_52c861ee'
);
const data = await response.json();
console.log('Full response:', JSON.stringify(data, null, 2));
console.log('Result object:', data.result);
console.log('Content:', data.result?.content);
```

**Database Check:**

```sql
SELECT content, excerpt FROM content_tasks WHERE task_id = 'blog_20251113_52c861ee';
-- If content is NULL, backend is not saving it
```

---

### Issue: Endpoint Calls Wrong URL

**Symptom:** Console shows request to `/api/tasks/` instead of `/api/content/blog-posts/tasks/`

**Causes:**

1. Old code path still calling wrong endpoint
2. Code not reloaded after changes

**Fix:**

```bash
# 1. Clear browser cache
# Ctrl+Shift+Delete or Cmd+Shift+Delete

# 2. Restart React app
npm start --workspace=web/oversight-hub

# 3. Verify code has correct endpoint
grep -n "api/content/blog-posts/tasks" web/oversight-hub/src/components/tasks/TaskManagement.jsx
```

---

### Issue: Console Shows Old Endpoint in URL

**Symptom:** Console logs show `/api/content/blog-posts/drafts/` instead of `/api/content/blog-posts/tasks/`

**Causes:**

1. Frontend code wasn't updated with correct endpoint
2. Browser cache serving old code

**Fix:**

1. Hard refresh browser: **Ctrl+Shift+R** (Windows/Linux) or **Cmd+Shift+R** (Mac)
2. Clear browser cache completely
3. Verify TaskManagement.jsx line ~72:

   ```javascript
   // Should be:
   const response = await fetch(`http://localhost:8000/api/content/blog-posts/tasks/${taskId}`, ...)

   // NOT:
   const response = await fetch(`http://localhost:8000/api/content/blog-posts/drafts/${taskId}`, ...)
   ```

---

## 📊 Success Checklist

After running all tests, verify:

- [ ] Test 1: Task created successfully
- [ ] Test 2: Backend endpoint responds with populated data
- [ ] Test 3: Frontend calls correct endpoint path
- [ ] Test 4: ResultPreviewPanel displays content
- [ ] Test 5: Database has non-NULL content field
- [ ] Test 6: Edit functionality works
- [ ] Test 7: Approve & Publish works
- [ ] Test 8: Delete still works
- [ ] Console shows no errors
- [ ] Network tab shows correct endpoints

---

## 🎯 If All Tests Pass

**Status:** ✅ COMPLETE - Frontend fixes working correctly

**Next Steps:**

1. Update DATABASE_MIGRATION_PLAN.md verification checklist
2. Prepare for tasks table drop (if all criteria met)
3. Create migration script for production

---

## 🚨 If Tests Fail

**Critical Failure:** Content field NULL in database

**Action Required:**

1. Backend generation pipeline is not saving content
2. Requires backend orchestration fix
3. Check: `/src/cofounder_agent/services/content_generation.py` or equivalent
4. Verify: Content is generated AND saved with UPDATE statement
5. Fix: Add database persistence in generation workflow

**Example Backend Fix Pattern:**

```python
async def save_generated_content(task_id, generated_content):
    """Save generated content to content_tasks table"""
    sql = """
    UPDATE content_tasks
    SET content = %s,
        excerpt = %s,
        status = 'completed',
        completed_at = NOW()
    WHERE task_id = %s
    """
    # Execute SQL update with content values
```

---

## 📞 Additional Resources

- **Frontend Code:** `web/oversight-hub/src/components/tasks/TaskManagement.jsx`
- **Backend Routes:** `src/cofounder_agent/routes/content_routes.py`
- **Database Schema:** `DATABASE_MIGRATION_PLAN.md`
- **Architecture:** `docs/02-ARCHITECTURE_AND_DESIGN.md`

---

**Test Created:** November 13, 2025  
**Next Review:** After all tests complete
**Maintainer:** AI Development Team
