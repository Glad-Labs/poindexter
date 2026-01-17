# 🚀 QUICK REFERENCE - Approval Workflow Testing

## Status

✅ **READY TO TEST** - All fixes implemented, test task created

---

## Test Task

```
ID:       a71e5b39-6808-4a0c-8b5d-df579e8af133
Topic:    Emerging AI Trends in 2025
Status:   completed → pending approval
Image:    https://images.pexels.com/photos/8386441/
```

---

## 3-Step Test

### 1️⃣ Open Task

```
http://localhost:3001/tasks
→ Find "Emerging AI Trends in 2025"
→ Click to open
```

### 2️⃣ Approve Task

```
Click "Approve" button
→ Fill reviewer ID (optional)
→ Add feedback (optional)
→ Submit
```

### 3️⃣ Verify

**Backend Log** (should show):

```
COMPLETE POST DATA BEFORE INSERT:
  featured_image_url: https://... ✅
  seo_title: Emerging AI Trends... ✅
  seo_description: Discover the... ✅
  seo_keywords: AI trends... ✅
```

**Database Query**:

```sql
SELECT featured_image_url, seo_title, seo_description, seo_keywords
FROM posts WHERE task_id = 'a71e5b39-6808-4a0c-8b5d-df579e8af133';
```

**Expected Result**:

```
featured_image_url    | https://images.pexels.com/photos/8386441/...
seo_title             | Emerging AI Trends 2025: What to Watch
seo_description       | Discover the top AI trends shaping 2025...
seo_keywords          | AI trends, artificial intelligence, machine learning, 2025...
```

---

## What Was Fixed

| Issue                   | Fix                                         |
| ----------------------- | ------------------------------------------- |
| featured_image_url NULL | Verified UI→DB flow                         |
| seo_title NULL          | Added fallback: metadata→title→"Untitled"   |
| seo_description NULL    | Added fallback: metadata→excerpt→content→"" |
| seo_keywords NULL       | Added fallback: metadata→""                 |
| UnboundLocalError       | Moved var init earlier                      |
| UUID validation error   | Added array conversion                      |

---

## Running Services

```
Backend:     http://localhost:8000  ✅ RUNNING
UI:          http://localhost:3001  ✅ RUNNING
Database:    localhost:5432         ✅ RUNNING
```

---

## Key Files

| File                                           | Purpose                   |
| ---------------------------------------------- | ------------------------- |
| `CREATE_TEST_TASK.py`                          | Create test tasks         |
| `src/cofounder_agent/routes/content_routes.py` | Approval endpoint (fixed) |
| `src/cofounder_agent/services/content_db.py`   | Post creation (fixed)     |
| `TEST_APPROVAL_WORKFLOW_GUIDE.md`              | Detailed steps            |
| `APPROVAL_WORKFLOW_FIXES_SUMMARY.md`           | Technical details         |

---

## Critical Test Points

```
✅ featured_image_url is NOT NULL in:
   - Backend log output
   - Database after insertion

✅ seo_title is NOT NULL in:
   - Backend log output
   - Database after insertion

✅ seo_description is NOT NULL in:
   - Backend log output
   - Database after insertion

✅ seo_keywords is NOT NULL in:
   - Backend log output
   - Database after insertion

✅ Approval request succeeds (HTTP 200)

✅ No errors in backend logs

✅ No errors in browser console (F12)
```

---

## Common Issues & Fixes

**Issue**: Task not showing in UI

- Verify DB connection: `SELECT COUNT(*) FROM content_tasks;`
- Check status is 'completed' and approval_status is 'pending'

**Issue**: Featured image NULL in DB

- Check backend log for "COMPLETE POST DATA BEFORE INSERT"
- Verify featured_image_url is there (not NULL)
- If NULL in log: UI not sending it, or lost in request

**Issue**: SEO fields NULL in DB

- Same as above
- Also verify metadata service is returning values
- Check if fallback chains triggered (logs will show)

**Issue**: Backend error

- Check full error in backend logs
- Search for "ERROR" or "❌" in logs
- Review traceback for exact issue

---

## Database Queries

**View Test Task**:

```sql
SELECT task_id, topic, featured_image_url, seo_title
FROM content_tasks
WHERE task_id = 'a71e5b39-6808-4a0c-8b5d-df579e8af133';
```

**View Published Post** (after approval):

```sql
SELECT id, title, featured_image_url, seo_title, seo_description, seo_keywords
FROM posts
WHERE task_id = 'a71e5b39-6808-4a0c-8b5d-df579e8af133';
```

**Count Posts with Missing SEO**:

```sql
SELECT COUNT(*) FROM posts
WHERE seo_title IS NULL OR seo_description IS NULL;
-- Should be 0 after fixes
```

---

## Logs to Monitor

**Backend Logs Show**:

```
[APPROVAL] Processing approval for task...
[APPROVAL] Building post_data with safeguards...
🔍 COMPLETE POST DATA BEFORE INSERT:
   - featured_image_url: https://... ✅
   - seo_title: "..." ✅
   - seo_description: "..." ✅
   - seo_keywords: "..." ✅
✅ INSERTING POST WITH THESE VALUES:
   INSERT INTO posts (...)
```

---

## Testing Checklist

- [ ] UI shows task in list
- [ ] Task details panel opens
- [ ] Featured image displays in preview
- [ ] Approval button is clickable
- [ ] Approval request succeeds (no errors)
- [ ] Backend log shows non-NULL featured_image_url
- [ ] Backend log shows non-NULL seo_title
- [ ] Backend log shows non-NULL seo_description
- [ ] Backend log shows non-NULL seo_keywords
- [ ] Database query returns complete row
- [ ] featured_image_url is NOT NULL in DB
- [ ] seo_title is NOT NULL in DB
- [ ] seo_description is NOT NULL in DB
- [ ] seo_keywords is NOT NULL in DB

---

## Performance Notes

- Approval should complete in < 5 seconds
- No errors in backend logs
- Database insertion successful on first try
- No retry logic needed (should work immediately)

---

## Success = ✅

When all checks pass:

```
✅ Featured image saved
✅ SEO title saved
✅ SEO description saved
✅ SEO keywords saved
✅ No errors
✅ No NULL values
✅ Complete workflow success!
```

---

## Need More Info?

- **Step-by-step guide**: `TEST_APPROVAL_WORKFLOW_GUIDE.md`
- **Technical details**: `APPROVAL_WORKFLOW_FIXES_SUMMARY.md`
- **Full setup info**: `TEST_APPROVAL_WORKFLOW_COMPLETE_SETUP.md`
- **Code changes**: Review files in `src/cofounder_agent/`

---

**Last Updated**: January 2025  
**Test Status**: Ready ✅  
**All Fixes**: Implemented ✅
