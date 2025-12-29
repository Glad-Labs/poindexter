# 🚀 End-to-End Blog Pipeline Test

**Goal:** Test blog post generation (Oversight Hub) → storage (DB) → display (Public Site)  
**Time:** 20 minutes  
**Auth:** Using existing mock authentication in Oversight Hub

---

## ✅ Step 1: Start All Three Services (3 minutes)

Open **three separate terminals** and start each service:

### Terminal 1: FastAPI Backend

```bash
cd src/cofounder_agent
python main.py
```

**Expected output:**

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### Terminal 2: Oversight Hub (React)

```bash
cd web/oversight-hub
npm start
```

**Expected output:**

```
Compiled successfully!
You can now view oversight-hub in the browser.
  Local:            http://localhost:3001
```

### Terminal 3: Public Site (Next.js)

```bash
cd web/public-site
npm run dev
```

**Expected output:**

```
▲ Next.js running on http://localhost:3000
```

### ✅ Verify All Services

```bash
# Test backend health
curl -s http://localhost:8000/api/health | jq .

# Test Oversight Hub is up
curl -s http://localhost:3001 | head -5

# Test Public Site is up
curl -s http://localhost:3000 | head -5
```

---

## ✅ Step 2: Login to Oversight Hub (1 minute)

1. **Open** http://localhost:3001 in your browser
2. **You should see:** LoginForm component
3. **Click:** "Login with Mock Auth" button (or similar)
   - This uses the existing mock authentication in `authService.js`
   - Mock auth code: `mock_auth_code_dev`
4. **Expected:** Dashboard loads with your user info

---

## ✅ Step 3: Create Blog Post Task in Oversight Hub (5 minutes)

### Find the Task Creation UI

1. Look for a **"Create Task"** button or **"New Post"** button
2. Or navigate to **Tasks** section

### Fill Out Task Form

```
Title:          "E2E Test: AI in 2025"
Description:    "Testing end-to-end blog pipeline"
Type:           "content_generation"
Topic:          "Artificial Intelligence Trends 2025"
Length:         "800 words"
Style:          "Professional/Technical"
```

### Submit Task

1. Click **"Create"** or **"Submit"**
2. **Expected:** Task appears in Active Tasks with ID

### Monitor Task Progress

Watch the task status change:

- `pending` → `in_progress` → `completed`

**Note:** If backend content generation is not connected, the task will still be created but may stay in `pending` state. That's okay for this test.

---

## ✅ Step 4: Verify Post in Database (3 minutes)

### Query PostgreSQL

```bash
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev
```

Inside psql:

```sql
-- View all posts
SELECT id, title, slug, status, created_at FROM posts ORDER BY created_at DESC LIMIT 5;

-- View post content
SELECT id, title, content, slug FROM posts WHERE title ILIKE '%AI%' LIMIT 1;

-- View content generation tasks
SELECT id, title, status, created_at FROM content_generation_tasks ORDER BY created_at DESC LIMIT 5;
```

**Expected:** See your new post(s) in the database

---

## ✅ Step 5: Verify Post Displays on Public Site (5 minutes)

### Open Public Site

1. Go to http://localhost:3000 in a new tab
2. **Expected:** Homepage with list of blog posts

### Find Your New Post

1. Look for your post title: "E2E Test: AI in 2025"
2. It should appear in the post list
3. **If not visible:**
   - Refresh the page (Ctrl+R or Cmd+R)
   - Check browser DevTools (F12) console for errors
   - Verify post is in database (Step 4)

### Click on Your Post

1. Click the post title or read more link
2. **Expected:** Single post page loads with:
   - ✅ Post title
   - ✅ Content (if generated)
   - ✅ Creation date
   - ✅ Category/tags (if assigned)

### Verify Formatting

- ✅ Title displays correctly
- ✅ Content is readable
- ✅ Images load (if included)
- ✅ Markdown formatting applied correctly
- ✅ Layout is responsive

---

## ✅ Step 6: Test Create Multiple Posts (3 minutes)

### Back in Oversight Hub

1. Create **3 more** blog posts with different topics:
   - "Machine Learning Basics"
   - "Cloud Computing Guide"
   - "Data Science 101"

### Verify All Display on Public Site

1. Go back to http://localhost:3000
2. **Without refreshing**, check if new posts appear
3. Or **refresh** to see latest posts
4. **Expected:** All 4+ posts visible in list

---

## ✅ Step 7: Test API Endpoints Directly (3 minutes)

### Get All Posts via API

```bash
curl -s http://localhost:8000/api/posts | jq '.data | length'
```

**Expected:** Returns number of posts (4+)

### Get Single Post by ID

```bash
# First, get a post ID from above
curl -s http://localhost:8000/api/posts | jq '.data[0].id'

# Then get that post
curl -s http://localhost:8000/api/posts/{POST_ID} | jq '.data.title'
```

### Create Post via API

```bash
curl -X POST http://localhost:8000/api/posts \
  -H "Content-Type: application/json" \
  -d '{
    "title": "API Created Post",
    "content": "# Test\n\nContent created via API",
    "slug": "api-created-post",
    "excerpt": "Testing API post creation"
  }' | jq .
```

### Get Tasks

```bash
curl -s http://localhost:8000/api/tasks | jq '.data | length'
```

---

## ✅ Step 8: Test Error Scenarios (2 minutes)

### Try Creating Invalid Task

```bash
# Missing required field
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"type": "content_generation"}' | jq .
```

**Expected:** 422 error (validation failed)

### Try Getting Non-Existent Post

```bash
curl -s http://localhost:8000/api/posts/invalid-id | jq .
```

**Expected:** 404 error or empty data

---

## ✅ Test Checklist

### Services

- [ ] Backend (8000) responds to health check
- [ ] Oversight Hub (3001) loads dashboard
- [ ] Public Site (3000) loads homepage

### Authentication

- [ ] Mock login works in Oversight Hub
- [ ] User info displays after login

### Blog Post Creation

- [ ] Can create task in Oversight Hub UI
- [ ] Task ID is generated
- [ ] Task status updates (or stays pending)

### Database

- [ ] Post appears in `posts` table
- [ ] Post has correct title, content, slug
- [ ] Task appears in `content_generation_tasks` table

### Public Site Display

- [ ] Post appears in homepage list
- [ ] Can click to view full post
- [ ] Post content displays correctly
- [ ] Multiple posts all visible

### API

- [ ] GET /api/posts returns all posts
- [ ] GET /api/posts/{id} returns single post
- [ ] POST /api/posts creates new post (if body provided)
- [ ] Invalid requests return proper errors

---

## 📊 Success Criteria

| Criterion                         | Status |
| --------------------------------- | ------ |
| All services start without errors | ✅     |
| Mock login works in Oversight Hub | ✅     |
| Can create blog post task         | ✅     |
| Post stored in database           | ✅     |
| Post displays on Public Site      | ✅     |
| Post content renders correctly    | ✅     |
| Multiple posts all visible        | ✅     |
| API endpoints working             | ✅     |

**Result:** ✅ **E2E Pipeline Complete!**

---

## 🐛 Troubleshooting

### Services Won't Start

```bash
# Check if ports are already in use
lsof -i :8000   # Backend
lsof -i :3001   # Oversight Hub
lsof -i :3000   # Public Site

# Kill existing processes
kill -9 {PID}
```

### Backend Health Check Fails

```bash
# Check backend logs
tail -f src/cofounder_agent/logs/*.log

# Try manually
python src/cofounder_agent/main.py
```

### Database Connection Error

```bash
# Verify PostgreSQL running
psql postgresql://postgres:postgres@localhost:5432/glad_labs_dev -c "SELECT 1"

# Check DATABASE_URL
echo $DATABASE_URL
```

### Post Not Showing on Public Site

```bash
# 1. Verify in database
psql -c "SELECT * FROM posts ORDER BY created_at DESC LIMIT 1"

# 2. Check browser console for errors (F12)
# 3. Try hard refresh: Ctrl+Shift+R
# 4. Check API response:
curl -s http://localhost:8000/api/posts | jq '.data[0]'
```

### Oversight Hub Won't Login

```bash
# 1. Check browser console (F12)
# 2. Try hard refresh
# 3. Clear localStorage: DevTools > Storage > Clear All
# 4. Try again
```

---

## 📝 What This Tests

✅ **Backend API** - Creates/retrieves blog posts  
✅ **Database** - Stores posts correctly  
✅ **Oversight Hub** - UI for task creation  
✅ **Public Site** - Displays generated posts  
✅ **End-to-End Flow** - Complete pipeline works

---

## 🎯 Next Steps After Success

- [ ] ✅ E2E pipeline working → Proceed to OAuth integration
- [ ] ✅ All tests passing → Deploy to staging
- [ ] Add real GitHub OAuth credentials
- [ ] Test with real LLM providers
- [ ] Load testing with multiple posts

---

**Let's test! 🚀**

Start the three services in Step 1, then move through each step sequentially.
