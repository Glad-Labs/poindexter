# Blog Post Pipeline - Testing Guide

**Last Updated:** January 23, 2026  
**Status:** ✅ All Components Implemented

## Overview

This guide walks through testing the complete blog post creation → approval → publishing → editing pipeline in the Glad Labs system.

---

## 🎯 Complete Pipeline Flow

```
CreateTaskModal (React UI)
    ↓ POST /api/services/tasks/actions/create_task
Database (tasks table, status='pending')
    ↓ AI Agent Execution
Database (task_metadata with generated content)
    ↓ User Action in TaskDetailModal
Approve (status='awaiting_approval' → 'approved')
    ↓ Manual Publish Button
PATCH /api/tasks/{id}/publish (creates entry in posts table)
    ↓ Database (posts table, status='published')
GET /api/posts (fetched by public site)
    ↓ Next.js Public Site
Display Post at /posts/{slug}
    ↓ Post-Publish Editing
Content Page (React UI) - Edit/Delete posts
```

---

## 📋 Pre-Test Checklist

### 1. **Services Running**

```bash
# All three services must be running:
npm run dev

# Verify:
# - Backend: http://localhost:8000/health
# - Public Site: http://localhost:3000
# - Oversight Hub: http://localhost:3001
```

### 2. **Database Connected**

Check `.env.local` has:

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/glad_labs_dev
```

### 3. **At Least One LLM API Key**

```env
OPENAI_API_KEY=sk-...
# OR
ANTHROPIC_API_KEY=sk-ant-...
# OR
OLLAMA_BASE_URL=http://localhost:11434
```

### 4. **Authentication Working**

- Navigate to <http://localhost:3001>
- Login via GitHub OAuth
- Should redirect to dashboard

---

## 🧪 Test Scenarios

### Test 1: Create Blog Post Task

**Steps:**

1. Open Oversight Hub: <http://localhost:3001/tasks>
2. Click "➕ Create Task" button
3. Fill in form:
   - **Task Type:** Blog Post
   - **Topic:** "The Future of AI Agents in Software Development"
   - **Word Count:** 1500
   - **Style:** Professional
   - **Tone:** Informative
   - **Category:** Technology
   - **Tags:** AI, Automation, Development
   - **Generate Featured Image:** ✅ Yes (Pexels)
4. Click "Create Task"

**Expected Result:**

- ✅ Task appears in task list with status "pending"
- ✅ Toast notification: "Task created successfully"
- ✅ Task automatically starts generating (status → "in_progress")

**Troubleshooting:**

- If task fails immediately → Check backend logs for LLM API errors
- If task stuck in "pending" → Check database connection
- If no featured image → Check Pexels API key in `.env.local`

---

### Test 2: Monitor Task Execution

**Steps:**

1. Click on the newly created task in task list
2. TaskDetailModal opens showing:
   - Content tab (generated blog post)
   - Image tab (featured image)
   - Approval tab (approve/reject form)
   - Metadata tab (task details)

**Expected Result:**

- ✅ After 30-120 seconds, status changes to "awaiting_approval"
- ✅ Content tab shows generated markdown blog post
- ✅ Image tab shows featured image with Pexels URL
- ✅ Quality score visible in metadata

**What to Check:**

- Content should be ~1500 words
- Featured image should be relevant to topic
- SEO metadata should be populated (keywords, description)

**Troubleshooting:**

- If status = "failed" → Check task error_message field
- If content is truncated → Check model token limits
- If no image → Pexels search may have failed (check logs)

---

### Test 3: Approve Task (WITHOUT Auto-Publish)

**Steps:**

1. In TaskDetailModal, go to "Approval" tab
2. Review the generated content
3. Add optional feedback: "Great content! Ready for publishing."
4. **IMPORTANT:** Ensure "Auto-publish after approval" is UNCHECKED
5. Click "✅ Approve Task"

**Expected Result:**

- ✅ Task status changes to "approved"
- ✅ Task remains in task list (not yet published)
- ✅ "Publish" button appears in TaskDetailModal
- ✅ Content is saved but NOT yet in posts table

**Verification Query:**

```sql
-- Should show task with status='approved'
SELECT id, topic, status FROM tasks WHERE id='YOUR_TASK_ID';

-- Should show NO entry yet
SELECT * FROM posts WHERE slug LIKE '%future-of-ai%';
```

---

### Test 4: Publish Approved Task

**Steps:**

1. In TaskDetailModal (task still open), find "Publish" button
2. Click "📤 Publish"
3. Confirm publish action if prompted

**Expected Result:**

- ✅ Task status changes to "published"
- ✅ Entry created in `posts` table
- ✅ Post slug generated (e.g., `the-future-of-ai-agents-550e8400`)
- ✅ published_at timestamp set
- ✅ Post immediately available via GET /api/posts

**Verification Query:**

```sql
-- Should show published post
SELECT id, title, slug, status, published_at FROM posts
WHERE status='published'
ORDER BY published_at DESC
LIMIT 1;
```

**API Test:**

```bash
curl http://localhost:8000/api/posts?status=published
# Should include your new post in response
```

---

### Test 5: View Published Post on Public Site

**Steps:**

1. Note the post slug from TaskDetailModal or database
2. Open new tab: <http://localhost:3000/posts/YOUR-SLUG>
3. Should display the full blog post with:
   - Title
   - Featured image
   - Full markdown-rendered content
   - Author info
   - Published date

**Expected Result:**

- ✅ Post renders correctly with styling
- ✅ Images load properly
- ✅ SEO meta tags visible in page source
- ✅ Breadcrumbs and navigation work

**Troubleshooting:**

- 404 Error → Check slug is correct, verify post exists in database
- No styling → Next.js CSS not loaded, check console for errors
- Missing image → Check featured_image_url in database

---

### Test 6: Edit Published Post in Oversight Hub

**Steps:**

1. Navigate to <http://localhost:3001/content>
2. See list of all published posts (fetched from /api/posts)
3. Find your published post in the table
4. Click "✏️ Edit" button

**Expected Result:**

- ✅ PostEditor modal opens
- ✅ All fields pre-populated with current post data:
  - Title
  - Slug (disabled/read-only)
  - Content (markdown)
  - Excerpt
  - Featured image URL
  - SEO title, description, keywords
  - Status

---

### Test 7: Make Post Edits

**Steps:**

1. In PostEditor modal, make changes:
   - Edit title: Add " - 2026 Edition"
   - Update content: Add a new paragraph at the end
   - Change SEO description
   - Update keywords
2. Toggle "👁️ Preview" to see rendered markdown
3. Click "💾 Save Changes"

**Expected Result:**

- ✅ Modal closes
- ✅ Alert: "Post updated successfully!"
- ✅ Content page refreshes and shows updated post
- ✅ Database updated via PATCH /api/posts/{id}

**Verification:**

```sql
-- Check updated_at timestamp changed
SELECT title, updated_at FROM posts WHERE id=YOUR_POST_ID;
```

**Refresh Public Site:**

- Go back to <http://localhost:3000/posts/YOUR-SLUG>
- Hard refresh (Ctrl+Shift+R)
- Should show updated content (may take up to 1 hour due to ISR caching)

---

### Test 8: View Post on Public Site

**Steps:**

1. In Content page, click "👁️ View" button on your post
2. Opens new tab to <http://localhost:3000/posts/YOUR-SLUG>

**Expected Result:**

- ✅ Opens public site in new tab
- ✅ Post displays with all content
- ✅ Changes from Test 7 visible

---

### Test 9: Delete Published Post

**⚠️ WARNING: This is permanent!**

**Steps:**

1. In Content page (<http://localhost:3001/content>)
2. Find a test post to delete
3. Click "🗑️ Delete" button
4. Confirm deletion

**Expected Result:**

- ✅ Confirmation dialog appears
- ✅ After confirm, post removed from database
- ✅ Content page refreshes, post no longer in list
- ✅ Public site returns 404 for deleted post slug

---

## 🔍 Troubleshooting Guide

### Issue: Task Stuck in "Pending"

**Possible Causes:**

1. Database connection lost
2. Orchestrator not initialized
3. Agent execution crashed

**Debug Steps:**

```bash
# Check backend logs
# Terminal running npm run dev:cofounder

# Query database
psql $DATABASE_URL
SELECT id, topic, status, error_message FROM tasks WHERE status='pending';
```

**Fix:**

- Restart backend: Ctrl+C, then `npm run dev:cofounder`
- Check DATABASE_URL in .env.local

---

### Issue: Task Fails with "Model Error"

**Possible Causes:**

1. No valid LLM API keys
2. Rate limit exceeded
3. Model not available

**Debug Steps:**

```bash
# Check model health
curl http://localhost:8000/api/models/health

# Check environment
echo $OPENAI_API_KEY
echo $ANTHROPIC_API_KEY
```

**Fix:**

- Add valid API key to .env.local
- Wait for rate limit to reset
- Switch to Ollama (local, no rate limits)

---

### Issue: Published Post Not Appearing on Public Site

**Possible Causes:**

1. Post status not "published" in database
2. Next.js ISR cache not refreshed
3. Public site not running

**Debug Steps:**

```sql
-- Check post status
SELECT id, title, slug, status FROM posts WHERE id=YOUR_ID;
```

```bash
# Check public site running
curl http://localhost:3000
```

**Fix:**

- Verify status='published' in database
- Hard refresh browser (Ctrl+Shift+R)
- Restart Next.js: `cd web/public-site && npm run dev`
- Wait up to 1 hour for ISR cache (or trigger revalidation)

---

### Issue: Content Page Shows "No content found"

**Possible Causes:**

1. No posts in database with status='published'
2. API client error
3. Backend not responding

**Debug Steps:**

```bash
# Test API directly
curl http://localhost:8000/api/posts?status=published

# Check browser console for errors
# Open DevTools (F12) → Console tab
```

**Fix:**

- Publish at least one task
- Check CORS headers (should allow localhost:3001)
- Verify getPosts() function in apiClient.js

---

### Issue: PostEditor Modal Not Opening

**Possible Causes:**

1. PostEditor.jsx not created
2. Import path wrong in Content.jsx
3. CSS not loaded

**Debug Steps:**

```bash
# Check file exists
ls web/oversight-hub/src/components/modals/PostEditor.jsx

# Check browser console for import errors
```

**Fix:**

- Verify PostEditor.jsx and PostEditor.css exist
- Check import path in Content.jsx
- Restart React dev server

---

## 📊 Success Criteria

All tests pass if:

✅ **Test 1:** Task created successfully  
✅ **Test 2:** Content generated within 2 minutes  
✅ **Test 3:** Task approved (status='approved')  
✅ **Test 4:** Task published (entry in posts table)  
✅ **Test 5:** Post visible on public site  
✅ **Test 6:** PostEditor modal opens  
✅ **Test 7:** Edits saved successfully  
✅ **Test 8:** Updated content visible on public site  
✅ **Test 9:** Post deleted successfully

---

## 🎉 Next Steps After Testing

Once all tests pass:

1. **Create Real Content:**
   - Use meaningful topics
   - Set appropriate categories
   - Add relevant tags

2. **Optimize SEO:**
   - Edit SEO titles and descriptions
   - Verify keywords are strategic
   - Check meta tags in page source

3. **Monitor Performance:**
   - Check task execution times
   - Review LLM costs in /costs dashboard
   - Verify public site load times

4. **Production Deployment:**
   - Push to `dev` branch (Railway staging)
   - Test full pipeline on staging
   - Merge to `main` (production deployment)

---

## 📞 Support

**Issues?** Check:

- Backend logs: Terminal running `npm run dev:cofounder`
- Frontend logs: Browser DevTools console (F12)
- Database logs: `psql $DATABASE_URL`

**Documentation:**

- Architecture: `docs/02-ARCHITECTURE_AND_DESIGN.md`
- Agents: `docs/05-AI_AGENTS_AND_INTEGRATION.md`
- Troubleshooting: `docs/troubleshooting/`
