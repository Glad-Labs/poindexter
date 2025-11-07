# 🧪 STEP-BY-STEP Testing Guide

**Objective:** Verify that blog post tasks now execute the self-critique loop instead of showing Poindexter assistant

**Status:** Ready to test  
**Estimated Time:** 3-5 minutes

---

## ✅ Pre-Test Verification

Before you start, verify all services are running:

### 1. Check Services Status

```powershell
# Terminal 1: Check FastAPI Backend
curl http://localhost:8000/api/health

# Terminal 2: Check Strapi
curl http://localhost:1337/admin

# Terminal 3: Check Oversight Hub
curl http://localhost:3001
```

**Expected Results:**

- ✅ Backend responds with `{"status":"healthy"}`
- ✅ Strapi admin loads
- ✅ Oversight Hub loads

---

## 🧪 Test Procedure

### Phase 1: Create Blog Post Task (30 seconds)

**Step 1.1: Open Oversight Hub**

1. In browser, go to: **http://localhost:3001**
2. You should see the dashboard with task management interface
3. Click **"Create Task"** button (top right or in TaskManagement panel)

**Step 1.2: Fill Task Form**

The CreateTaskModal should open. Fill in:

| Field      | Value                                              | Notes                       |
| ---------- | -------------------------------------------------- | --------------------------- |
| Task Type  | `Blog Post`                                        | CRITICAL - Must select this |
| Title      | `AI Trends in 2025`                                | Descriptive title           |
| Topic      | `What are the latest AI trends affecting business` | Topic for research          |
| Style      | `Technical` or `Professional`                      | Writing style               |
| Tone       | `Professional`                                     | Leave default               |
| Word Count | `1500`                                             | Target length               |
| Keywords   | `AI, trends, business, 2025`                       | SEO keywords                |

**Step 1.3: Submit Task**

1. Click **"Create Task"** button
2. Modal should close
3. Task appears in queue

### Phase 2: Monitor in Browser Console (5 seconds)

**Step 2.1: Open Browser Console**

1. Press **F12** on keyboard
2. Click **"Console"** tab
3. Clear any existing logs (Ctrl+L or click clear button)

**Step 2.2: Watch for Correct Endpoint**

You should immediately see:

```
📤 Sending to content generation endpoint: {
  topic: "What are the latest AI trends...",
  style: "Technical",
  tone: "professional",
  target_length: 1500,
  tags: ["AI", "trends", "business", "2025"]
}

✅ Task created successfully: {
  task_id: "550e8400-e29b-41d4-a716-446655440000",
  status: "pending"
}
```

**✅ Good Sign:** You see `"Sending to content generation endpoint"`  
**❌ Bad Sign:** You see `"Sending task payload"` or `"Sending generic task"`

### Phase 3: Wait for Pipeline Execution (30 seconds)

**Step 3.1: Monitor Task Status**

In the Oversight Hub, you should see the task:

- Status changes: `pending` → `in_progress` → `completed`
- Progress indicator (if available)

**Step 3.2: Check Console for Status Updates**

In console (F12), you should see periodic updates like:

```
📄 Updated blog post task status: {
  id: "550e8400-...",
  status: "in_progress",
  hasResult: false
}
```

**Step 3.3: Wait for Completion**

Keep watching until you see:

```
📄 Updated blog post task status: {
  id: "550e8400-...",
  status: "completed",
  hasResult: true
}
```

**Timeline:** This should take 20-30 seconds total

### Phase 4: Verify Results Display (1 minute)

**Step 4.1: Click on Completed Task**

1. In TaskManagement, click on the `AI Trends in 2025` task
2. ResultPreviewPanel should display on the right side

**Step 4.2: Verify Content is NOT Poindexter Chat**

**You should NOT see:**

```
❌ [Chat interface]
❌ "Poindexter Assistant"
❌ "Let me help you with..."
❌ "I can assist you..."
❌ Generic chat responses
```

**Step 4.3: Verify Content IS Blog Post**

**You SHOULD see:**

```
✅ Title: "AI Trends in 2025"
✅ Full blog post with multiple sections
✅ Research data integrated in content
✅ Professional writing (from self-critique)
✅ Markdown formatting applied
✅ Potentially code examples or lists
✅ Conclusion or summary section
```

**Step 4.4: Check SEO Metadata**

Scroll down in ResultPreviewPanel to see:

```
✅ SEO Title: [Auto-generated]
✅ SEO Description: [Auto-generated]
✅ Keywords: ["AI", "trends", "business", "2025"]
```

### Phase 5: Advanced Verification (Optional)

**Step 5.1: View Full Pipeline Logs**

Get the task ID from the task (visible in UI or console):

```javascript
// In browser console:
fetch('http://localhost:8000/api/content/status/YOUR_TASK_ID')
  .then((r) => r.json())
  .then((d) => console.log('FULL RESULT:', d));
```

You should see:

```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "result": {
    "content": "[Full blog markdown]",
    "title": "AI Trends in 2025",
    "research_data": {...},
    "seo": {...}
  }
}
```

**Step 5.2: Check Raw Content**

```javascript
// In browser console:
fetch('http://localhost:8000/api/content/status/YOUR_TASK_ID')
  .then((r) => r.json())
  .then((d) => console.log(d.result.content));
```

Should output full markdown blog post (not a chat message)

**Step 5.3: Backend Verification**

In the backend/terminal where FastAPI is running, look for logs like:

```
[INFO] POST /api/content/generate - Task created: task_id
[INFO] Starting research agent...
[INFO] Starting creative agent (draft)...
[INFO] Starting QA agent (critique)...
[INFO] Starting creative agent (refined)...
[INFO] Starting image agent...
[INFO] Starting publishing agent...
[INFO] Task completed successfully
```

---

## ✔️ Success Criteria Checklist

| Criteria                 | Check | Details                                                |
| ------------------------ | ----- | ------------------------------------------------------ |
| **Endpoint Routing**     | ✅    | Console shows "Sending to content generation endpoint" |
| **Task ID Returned**     | ✅    | Console shows task_id immediately                      |
| **Pipeline Executes**    | ✅    | Status changes from pending → in_progress → completed  |
| **Results Display**      | ✅    | ResultPreviewPanel shows blog post (not chat)          |
| **No Poindexter Chat**   | ✅    | No chat assistant interface appears                    |
| **Blog Content Quality** | ✅    | Multiple paragraphs, sections, professional writing    |
| **SEO Metadata**         | ✅    | Title, description, keywords populated                 |
| **Processing Time**      | ✅    | Total time 20-35 seconds (research+creative+QA+etc)    |
| **Markdown Formatting**  | ✅    | Headers (#, ##), bold, lists properly formatted        |

---

## 🐛 Troubleshooting

### Issue: Console shows "Sending generic task payload"

**Problem:** Task went to wrong endpoint  
**Solution:** Clear browser cache and reload page

```javascript
// In console:
window.location.reload(true);
```

### Issue: Task status stays "pending" for >1 minute

**Problem:** Pipeline not executing  
**Solution:**

1. Check backend logs: `railway logs` or Python terminal
2. Verify `/api/content/generate` endpoint exists
3. Check if backend is running: `curl http://localhost:8000/api/health`

### Issue: ResultPreviewPanel shows Poindexter chat

**Problem:** Still using wrong endpoint for results fetch  
**Solution:**

1. Check TaskManagement.jsx was updated
2. Verify `fetchContentTaskStatus()` function exists
3. Reload page and try again

### Issue: Content doesn't display fully

**Problem:** JSON parsing error or incomplete response  
**Solution:**
Check raw response in console:

```javascript
fetch('http://localhost:8000/api/content/status/TASK_ID')
  .then((r) => r.json())
  .then((d) => console.log(d.result));
```

If result.content is empty/null, backend pipeline issue

### Issue: Task appears but no results after 60 seconds

**Problem:** Pipeline timeout or backend error  
**Solution:**

1. Check backend logs for errors
2. Verify all agents are running (research, creative, QA, image, publishing)
3. Try with shorter word count (500 instead of 1500)

---

## 📊 Expected Console Output

### Good Execution:

```
📤 Sending to content generation endpoint: {topic: "What are the latest AI trends...", style: "Technical", tone: "professional", target_length: 1500, tags: Array(4)}

✅ Task created successfully: {task_id: "550e8400-e29b-41d4-a716-446655440000", status: "pending"}

📄 Updated blog post task status: {id: "550e8400-...", status: "in_progress", hasResult: false}

[wait 20-30 seconds]

📄 Updated blog post task status: {id: "550e8400-...", status: "completed", hasResult: true}
```

### Bad Execution:

```
❌ "Failed to create task"
❌ "Cannot connect to http://localhost:8000"
❌ Status stays "pending" after 2 minutes
❌ Task shows "failed" with error message
```

---

## 📸 Expected Visual Output

### Oversight Hub Dashboard (After task creation):

```
┌─ Tasks Queue ──────────────────────┐
│ ID: 550e8400...                    │
│ Title: AI Trends in 2025           │
│ Status: ⏳ in_progress              │
│ Progress: ████████░░ 80%           │
│ Created: 2 min ago                 │
└────────────────────────────────────┘

┌─ Result Preview ───────────────────┐
│ ✓ Results Preview                  │
│                                    │
│ Title: AI Trends in 2025           │
│                                    │
│ # AI Trends in 2025                │
│                                    │
│ ## Research Background              │
│ - Finding 1: AI adoption...        │
│ - Finding 2: Market growth...      │
│                                    │
│ ## Main Content                     │
│ The landscape of artificial...     │
│ [... full blog content ...]        │
│                                    │
│ [✓ Approve] [Edit] [Reject]        │
└────────────────────────────────────┘
```

---

## ✨ Next Steps After Successful Test

1. ✅ **Commit Changes:**

   ```bash
   git add web/oversight-hub/src/components/tasks/CreateTaskModal.jsx
   git add web/oversight-hub/src/components/tasks/TaskManagement.jsx
   git commit -m "fix: route blog_post tasks to /api/content/generate for self-critique loop"
   git push origin dev
   ```

2. ✅ **Test Other Task Types:** Image generation, social media, etc. should still work

3. ✅ **Try Different Topics:** Test with various blog topics

4. ✅ **Monitor Performance:** Check if 20-30s timeline is consistent

5. ✅ **Check Results Quality:** Do the blogs look good and relevant?

---

**Ready to test?** 🚀 Start with Phase 1 above!
