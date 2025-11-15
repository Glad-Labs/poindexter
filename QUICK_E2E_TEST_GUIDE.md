# 🚀 Quick End-to-End Pipeline Test Guide

**Objective:** Test blog post generation in Oversight Hub → storage in database → display on Public Site  
**Time:** 15-20 minutes  
**OAuth:** Skipping GitHub OAuth for now (we'll use mock auth)

---

## ✅ Step 1: Start Services (2 minutes)

### Terminal 1: Backend (FastAPI)

```bash
cd src/cofounder_agent
python main.py
```

Expected: `Uvicorn running on http://127.0.0.1:8000`

### Terminal 2: Oversight Hub (React)

```bash
cd web/oversight-hub
npm start
```

Expected: `Compiled successfully!` + Opens on http://localhost:3001

### Terminal 3: Public Site (Next.js)

```bash
cd web/public-site
npm run dev
```

Expected: `▲ Next.js running on http://localhost:3000`

### Verify Services

```bash
curl http://localhost:8000/api/health         # Backend
curl http://localhost:3001 -s | head -20      # Oversight Hub
curl http://localhost:3000 -s | head -20      # Public Site
```

---

## ✅ Step 2: Test Backend API Directly (3 minutes)

### Test Health Endpoint

```bash
curl -s http://localhost:8000/api/health | jq .
```

Expected response:

```json
{
  "status": "healthy",
  "timestamp": "2025-11-14T...",
  "services": {
    "database": "connected",
    "models": "available"
  }
}
```

### Create Test Blog Post via API

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Blog Post",
    "description": "Testing the end-to-end pipeline",
    "type": "content_generation",
    "parameters": {
      "topic": "AI Trends in 2025",
      "length": "500 words"
    }
  }' | jq .
```

Expected: Task created with ID

### Check Task Status

```bash
# Replace {TASK_ID} with ID from previous response
curl -s http://localhost:8000/api/tasks/{TASK_ID} | jq .
```

---

## ✅ Step 3: Test Oversight Hub UI (5 minutes)

### Open Oversight Hub

1. Go to **http://localhost:3001** in browser
2. You should see the dashboard

### Create Blog Post Task

1. Click **"Create Task"** or similar button
2. Fill in:
   - **Title:** "E2E Test: AI Trends"
   - **Type:** "Content Generation"
   - **Topic:** "AI Trends in 2025"
   - **Length:** "500-1000 words"
3. Click **"Submit"**

### Monitor Task

1. Task should appear in **"Active Tasks"** section
2. Watch status change: `pending` → `in_progress` → `completed`
3. Copy **Task ID** for next step

---

## ✅ Step 4: Verify Blog Post in Database (3 minutes)

### Query Database Directly

```bash
# Connect to PostgreSQL
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev

# Inside psql:
SELECT id, title, slug, status FROM posts ORDER BY created_at DESC LIMIT 5;
SELECT id, title, task_id, status FROM content_generation_tasks ORDER BY created_at DESC LIMIT 5;
```

Expected: New post appears in posts table

### Retrieve Post via API

```bash
curl -s http://localhost:8000/api/posts \
  -H "Authorization: Bearer test-token" | jq '.data | first'
```

---

## ✅ Step 5: Display Post on Public Site (5 minutes)

### Open Public Site

1. Go to **http://localhost:3000** in browser
2. Should see homepage with existing posts

### Verify New Post Displays

1. Look for the newly created post in the post list
2. Click on the post
3. Verify:
   - ✅ Title displays correctly
   - ✅ Content renders (markdown formatting)
   - ✅ Images load if included
   - ✅ Category/tags display

### Test Post Navigation

1. Go back to homepage
2. Filter by category or tag
3. Verify new post appears in filtered results

---

## ✅ Step 6: Test Error Scenarios (3 minutes)

### Create Task Without Required Fields

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"type": "content_generation"}' | jq .
```

Expected: 422 error (validation failed)

### Create Task with Invalid Topic

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "",
    "type": "content_generation",
    "parameters": {}
  }' | jq .
```

Expected: 400 error (missing required fields)

### Query Non-Existent Post

```bash
curl -s http://localhost:8000/api/posts/invalid-slug | jq .
```

Expected: 404 error

---

## ✅ Step 7: Test Cross-App Sync (3 minutes)

### Create Post in Oversight Hub

1. Create another task: "E2E Test: Machine Learning"
2. Wait for completion

### Verify on Public Site

1. **Without refreshing**, verify post appears
2. Or **refresh page** to fetch latest posts
3. Verify new post is visible

### Test Cross-Tab Sync

1. Open Public Site in Tab 1: http://localhost:3000
2. Open Public Site in Tab 2: http://localhost:3000
3. Create post in Oversight Hub
4. Verify both tabs show the new post

---

## 📊 Test Success Checklist

- [ ] Backend health endpoint responds
- [ ] Can create task via API
- [ ] Can retrieve task status
- [ ] Oversight Hub dashboard loads
- [ ] Can create task via UI
- [ ] Task progresses through states
- [ ] Post appears in database
- [ ] Post appears on Public Site
- [ ] Post displays correctly with formatting
- [ ] Can navigate/filter posts
- [ ] Error scenarios handled gracefully
- [ ] Cross-app sync works

---

## 🐛 If Something Fails

### Backend Not Responding

```bash
# Check logs
tail -f src/cofounder_agent/logs/*.log

# Restart backend
cd src/cofounder_agent
python main.py
```

### Database Connection Error

```bash
# Verify PostgreSQL is running
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT 1"

# Check DATABASE_URL in .env
echo $DATABASE_URL
```

### Post Not Showing on Public Site

```bash
# Check browser console (F12)
# Check backend logs for errors
# Verify post is in database:
psql -c "SELECT * FROM posts WHERE created_at > NOW() - INTERVAL '5 minutes'"
```

### Oversight Hub Not Loading

```bash
# Check npm logs
cd web/oversight-hub
npm start

# Check browser console (F12) for JavaScript errors
```

---

## 🎯 What We're Testing

This end-to-end test validates:

1. **Backend API** - Creates tasks, stores blog posts
2. **Database** - Persists posts correctly
3. **Oversight Hub** - UI for creating tasks
4. **Public Site** - Displays generated posts
5. **Cross-app Sync** - New posts appear without refresh
6. **Error Handling** - Invalid requests handled gracefully

---

## 📝 Next Steps After Testing

- [ ] ✅ All E2E tests pass → Proceed to Phase 4
- [ ] ❌ Some tests fail → Debug and fix issues
- [ ] 🔐 Add real OAuth (GitHub/Google) credentials
- [ ] 📦 Deploy to staging environment
- [ ] 🚀 Deploy to production

---

## ⏱️ Estimated Time

| Task                  | Time        |
| --------------------- | ----------- |
| Start Services        | 2 min       |
| Backend API Test      | 3 min       |
| Oversight Hub Test    | 5 min       |
| Database Verification | 3 min       |
| Public Site Test      | 5 min       |
| Error Scenarios       | 3 min       |
| Cross-App Sync        | 3 min       |
| **TOTAL**             | **~25 min** |

---

**Ready? Start Terminal 1 and let's go! 🚀**
