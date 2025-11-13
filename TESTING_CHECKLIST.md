# 🧪 API Refactoring Testing Checklist

**Status:** Ready for testing  
**Test Date:** [To be filled]  
**Tested By:** [To be filled]

---

## Backend Testing

### 1. Create Task (POST /api/content/tasks)

**Test:** Create a new task with task_type

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI Trends in 2025",
    "task_type": "blog_post",
    "style": "professional",
    "tone": "informative",
    "target_length": 2000,
    "tags": ["AI", "Technology"],
    "request_type": "basic"
  }'
```

**Expected Response:**
- Status: 201 Created
- Response includes: task_id, task_type="blog_post", status="pending"
- Database: Task stored with task_type field

**Checklist:**
- [ ] Returns 201 status
- [ ] Response has task_id
- [ ] Response has task_type: "blog_post"
- [ ] Response has status: "pending"
- [ ] Database stores task with task_type

**Test with Different Types:**
- [ ] task_type: "social_media"
- [ ] task_type: "email"
- [ ] task_type: "newsletter"

---

### 2. Get Task Status (GET /api/content/tasks/{id})

**Test:** Retrieve task details including task_type

```bash
curl http://localhost:8000/api/content/tasks/{task_id}
```

**Expected Response:**
- Status: 200 OK
- Includes: task_id, task_type, status, result (when completed)
- All fields populated correctly

**Checklist:**
- [ ] Returns 200 status
- [ ] Response includes task_id
- [ ] Response includes task_type field
- [ ] Response includes status
- [ ] Response includes result (when completed)
- [ ] Works for all task types

---

### 3. List Tasks (GET /api/content/tasks)

**Test 1: List all tasks**

```bash
curl http://localhost:8000/api/content/tasks
```

**Expected:** All tasks returned, each with task_type field

**Checklist:**
- [ ] Returns 200 status
- [ ] Each task has task_type field
- [ ] Pagination works (limit, offset params)

**Test 2: Filter by task_type**

```bash
curl "http://localhost:8000/api/content/tasks?task_type=blog_post"
```

**Expected:** Only blog_post tasks returned

**Checklist:**
- [ ] Returns only blog_post tasks
- [ ] social_media, email, newsletter filtered out
- [ ] Count matches expected filtered set

**Test 3: Filter by status**

```bash
curl "http://localhost:8000/api/content/tasks?status=completed"
```

**Expected:** Only completed tasks returned

**Checklist:**
- [ ] Returns only completed tasks
- [ ] Filter parameter works
- [ ] Pagination applies to filtered results

**Test 4: Combined filters**

```bash
curl "http://localhost:8000/api/content/tasks?task_type=blog_post&status=completed"
```

**Expected:** Only completed blog_post tasks

**Checklist:**
- [ ] Both filters applied correctly
- [ ] Result is intersection (AND logic)

---

### 4. Approve/Publish Task (POST /api/content/tasks/{id}/approve)

**Test:** Approve task and trigger publishing

```bash
curl -X POST http://localhost:8000/api/content/tasks/{task_id}/approve
```

**Expected Response:**
- Status: 200 OK
- Response includes: approval confirmation, strapi_post_id (if published to Strapi)
- Task status: "approved" or "completed"

**Checklist:**
- [ ] Returns 200 status
- [ ] Task status changes to approved/completed
- [ ] Works for all task types
- [ ] strapi_post_id is integer (not string) - Bug fix verified
- [ ] Database task updated correctly

---

### 5. Delete Task (DELETE /api/content/tasks/{id})

**Test:** Delete a task

```bash
curl -X DELETE http://localhost:8000/api/content/tasks/{task_id}
```

**Expected:**
- Status: 204 No Content or 200 OK
- Task removed from database
- GET /api/content/tasks/{id} returns 404

**Checklist:**
- [ ] Returns 200/204 status
- [ ] Task removed from database
- [ ] Subsequent GET returns 404
- [ ] Works for all task types

---

## Frontend Testing

### Setup
- [ ] Start all services: `npm run dev`
- [ ] Verify TaskManagement component loads
- [ ] Open browser DevTools (F12)

### 1. Create Task Workflow

**Steps:**
1. Click "Create Task" button in Oversight Hub
2. Fill in form with task details
3. Click "Generate"

**Verification:**
- [ ] POST to `/api/content/tasks` (not `/api/content/blog-posts`)
- [ ] Request includes task_type field
- [ ] Response shows task_id and task_type
- [ ] Task appears in task list

**Check Network Tab:**
- [ ] URL: `http://localhost:8000/api/content/tasks`
- [ ] Method: POST
- [ ] Status: 201 Created

---

### 2. View Task Details

**Steps:**
1. Create a task
2. Click task in list to view details
3. Wait for completion

**Verification:**
- [ ] GET to `/api/content/tasks/{id}` (not `/api/content/blog-posts/tasks/{id}`)
- [ ] Response includes task_type
- [ ] Details display correctly

**Check Network Tab:**
- [ ] URL: `http://localhost:8000/api/content/tasks/{id}`
- [ ] Status: 200 OK

---

### 3. Approve/Publish Task

**Steps:**
1. View a completed task
2. Click "Approve" button
3. Verify published

**Verification:**
- [ ] POST to `/api/content/tasks/{id}/approve` (not `/api/content/blog-posts/drafts/{id}/publish`)
- [ ] Task status changes
- [ ] Content published to Strapi (if enabled)

**Check Network Tab:**
- [ ] URL: `http://localhost:8000/api/content/tasks/{id}/approve`
- [ ] Status: 200 OK

---

### 4. Delete Task

**Steps:**
1. View a task
2. Click "Delete" button
3. Confirm deletion

**Verification:**
- [ ] DELETE to `/api/content/tasks/{id}` (not `/api/content/blog-posts/drafts/{id}`)
- [ ] Task removed from list

**Check Network Tab:**
- [ ] URL: `http://localhost:8000/api/content/tasks/{id}`
- [ ] Method: DELETE
- [ ] Status: 200 or 204

---

## Database Testing

### 1. Verify task_type Column Exists

**Steps:**
1. Connect to database
2. Check ContentTask table schema

```sql
-- PostgreSQL
\d content_tasks

-- SQLite
PRAGMA table_info(content_tasks);
```

**Verification:**
- [ ] task_type column exists
- [ ] Type: VARCHAR or STRING
- [ ] Default value: "blog_post"

---

### 2. Verify Data is Stored

```sql
SELECT id, task_type, status FROM content_tasks LIMIT 5;
```

**Verification:**
- [ ] All tasks have task_type value
- [ ] Values are one of: blog_post, social_media, email, newsletter
- [ ] Defaults show "blog_post"

---

### 3. Verify Filtering Works

```sql
SELECT COUNT(*) FROM content_tasks WHERE task_type = 'blog_post';
SELECT COUNT(*) FROM content_tasks WHERE task_type = 'social_media';
```

**Verification:**
- [ ] Queries execute successfully
- [ ] Results match expected counts
- [ ] Filtering by task_type is functional

---

## Integration Testing

### 1. End-to-End Workflow

**Complete workflow test:**

1. [ ] Create blog_post task → Verify POST to /api/content/tasks
2. [ ] Task appears in list → Verify GET /api/content/tasks
3. [ ] Filter by blog_post type → Verify filtering works
4. [ ] View details → Verify GET /api/content/tasks/{id}
5. [ ] Generate content → Task completes
6. [ ] Approve task → Verify POST to /api/content/tasks/{id}/approve
7. [ ] Delete task → Verify DELETE /api/content/tasks/{id}

**Overall Status:**
- [ ] All steps succeed
- [ ] All endpoints use new /api/content/tasks pattern
- [ ] No errors in console or logs

### 2. Multi-Type Workflow

**Test each task type:**

1. [ ] Create and complete blog_post task
2. [ ] Create and complete social_media task
3. [ ] Create and complete email task
4. [ ] Create and complete newsletter task

**Verify:**
- [ ] Each type creates successfully
- [ ] task_type field stored correctly
- [ ] Filtering by type works for all 4

---

## Error Handling Testing

### 1. Missing task_type Parameter

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{"topic": "Test"}'
```

**Expected:** 
- [ ] Request still succeeds (defaults to "blog_post")
- OR request is rejected with 400

---

### 2. Invalid task_type Value

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{"topic": "Test", "task_type": "invalid_type"}'
```

**Expected:**
- [ ] Request rejected with 400 or 422
- [ ] Error message indicates invalid task_type

---

### 3. Non-existent Task ID

```bash
curl http://localhost:8000/api/content/tasks/nonexistent-id
```

**Expected:**
- [ ] Returns 404 Not Found
- [ ] Error message is clear

---

## Browser Console Testing

### While running frontend tests, check browser console (F12)

**Expected:**
- [ ] No errors related to API calls
- [ ] No warnings about deprecated endpoints
- [ ] All fetch/axios calls succeed

**Look for:**
- [ ] API response data logged correctly
- [ ] task_type present in responses
- [ ] No references to old /api/content/blog-posts paths

---

## Performance Testing

### 1. List Query Performance

**Test:** Listing 1000+ tasks

```bash
curl "http://localhost:8000/api/content/tasks?limit=1000"
```

**Expected:**
- [ ] Response time < 2 seconds
- [ ] All tasks returned with task_type

---

### 2. Filter Performance

**Test:** Filtering large dataset

```bash
curl "http://localhost:8000/api/content/tasks?task_type=blog_post&limit=1000"
```

**Expected:**
- [ ] Response time < 1 second
- [ ] Uses database index on task_type
- [ ] Accurate result count

---

## Final Verification Checklist

### Code Changes Applied
- [ ] content_routes.py: All 5 endpoints updated
- [ ] task_store_service.py: task_type field added and integrated
- [ ] TaskManagement.jsx: All 4 API calls updated
- [ ] Database schema: task_type column created

### API Behavior
- [ ] POST /api/content/tasks works (not /api/content/blog-posts)
- [ ] GET /api/content/tasks/{id} works (not /api/content/blog-posts/tasks/{id})
- [ ] GET /api/content/tasks works (not /api/content/blog-posts/drafts)
- [ ] POST /api/content/tasks/{id}/approve works (not /api/content/blog-posts/drafts/{id}/publish)
- [ ] DELETE /api/content/tasks/{id} works (not /api/content/blog-posts/drafts/{id})

### Task Type Support
- [ ] blog_post type supported
- [ ] social_media type supported
- [ ] email type supported
- [ ] newsletter type supported
- [ ] Filtering by task_type works

### Bug Fixes
- [ ] strapi_post_id type bug fixed (now int, not string)

### Documentation
- [ ] API_REFACTOR_ENDPOINTS.md created and complete

---

## Sign-Off

**Testing Completed By:** ________________  
**Date:** ________________  
**All Tests Passed:** [ ] YES [ ] NO

**Issues Found:** (List any failures or concerns)

```
1. 
2. 
3. 
```

**Notes:**

```


```

---

**Next Steps After Testing:**
1. [ ] Fix any issues found
2. [ ] Commit changes: `git commit -m "refactor: migrate /api/content/blog-posts to /api/content/tasks"`
3. [ ] Push to dev branch
4. [ ] Create PR to main
5. [ ] Deploy to staging
6. [ ] Final production deployment
