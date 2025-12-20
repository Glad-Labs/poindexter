# Quick Test Guide - Content Pipeline Fixes

**Time to test:** ~10 minutes  
**Status:** Ready to run immediately

---

## Step 1: Start Backend (if not running)

```bash
cd src/cofounder_agent
python main.py
# Should see: "Application startup complete" on port 8000
```

---

## Step 2: Create a Content Task

```bash
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "The Future of AI in Healthcare",
    "style": "technical",
    "tone": "professional",
    "target_length": 1500
  }'
```

Expected response:

```json
{
  "task_id": "12345678-90ab-cdef-1234-567890abcdef",
  "status": "pending",
  "created_at": "2025-12-17T..."
}
```

**Save the task_id for next steps!**

---

## Step 3: Monitor Task Generation

```bash
curl http://localhost:8000/api/content/tasks/12345678-90ab-cdef-1234-567890abcdef
```

Watch for status progression:

- `pending` → `processing` → `completed`

This takes ~1-2 minutes depending on model (Ollama/Gemini).

Example completed response:

```json
{
  "task_id": "12345678...",
  "status": "completed",
  "content": "# The Future of AI in Healthcare\n\nArtificial Intelligence is...",
  "topic": "The Future of AI in Healthcare",
  "featured_image_url": "https://images.pexels.com/...",
  "task_metadata": {
    "topic": "The Future of AI in Healthcare",
    ...
  }
}
```

---

## Step 4: Approve the Task (THIS RUNS ALL THE FIXES!)

```bash
curl -X POST http://localhost:8000/api/content/tasks/12345678-90ab-cdef-1234-567890abcdef/approve \
  -H "Content-Type: application/json" \
  -d '{
    "approved": true,
    "human_feedback": "Looks great! Ready to publish.",
    "reviewer_id": "test-admin"
  }'
```

Expected response:

```json
{
  "task_id": "12345678...",
  "approval_status": "approved",
  "published_url": "/posts/the-future-of-ai-in-healthcare-abc123",
  "message": "✅ Task approved by test-admin"
}
```

Look at the logs during approval:

```
📝 Final Title: The Future of AI in Healthcare
📝 Generated unique slug: the-future-of-ai-in-healthcare-abc123
📝 Generated Excerpt: Artificial Intelligence is transforming healthcare...
✓ Keyword matched category: Healthcare
✓ Extracted 3 tags
📝 Generated SEO metadata
✅ Post published to CMS database with ID: xyz789
```

---

## Step 5: Verify Post in Database

```sql
-- Query the posts table
SELECT
  id,
  title,
  slug,
  excerpt,
  featured_image_url,
  author_id,
  category_id,
  tag_ids,
  seo_title,
  seo_description,
  seo_keywords
FROM posts
ORDER BY created_at DESC
LIMIT 1;
```

### ✅ SUCCESS CRITERIA

All fields should be populated:

| Field                | Expected                        | Status |
| -------------------- | ------------------------------- | ------ |
| `title`              | NOT "Untitled"                  | ✅     |
| `slug`               | "the-future-of-ai-..."          | ✅     |
| `excerpt`            | "Artificial Intelligence is..." | ✅     |
| `featured_image_url` | URL from approval               | ✅     |
| `author_id`          | "14c9cad6-57ca-..."             | ✅     |
| `category_id`        | NOT NULL                        | ✅     |
| `tag_ids`            | ["tag-1", "tag-2", ...]         | ✅     |
| `seo_title`          | Generated title                 | ✅     |
| `seo_description`    | Generated description           | ✅     |
| `seo_keywords`       | keyword1, keyword2, ...         | ✅     |

---

## Step 6: Check Logs for LLM Activity

Look in backend logs for:

```
✓ Using LLM to generate title
✓ LLM generated excerpt
✓ LLM matched category: Healthcare
✓ LLM extracted 3 tags
✓ LLM generated SEO metadata
```

Or if LLM unavailable:

```
✓ Using topic as title
✓ Extracted title from content
✓ Using fallback keyword matching
```

Both work! Fallback ensures zero failures.

---

## Troubleshooting

### "title": "Untitled"

- ❌ Fix didn't apply
- Check: Did you restart backend after code changes?
- Check: Are you calling `/approve` endpoint?

### featured_image_url: NULL

- This is okay - image generation is a separate fix
- Should be populated if approval included featured_image_url
- Check content_tasks.featured_image_url in database

### category_id: NULL

- ❌ Category matching failed
- Check: Do categories exist in database?
  ```sql
  SELECT * FROM categories LIMIT 1;
  ```
- Check: Does LLM API key work? (If not, keyword matching should still work)

### tag_ids: NULL or []

- ⚠️ Tags not extracted
- This is okay - optional feature
- Check: Do tags exist in database?
  ```sql
  SELECT * FROM tags LIMIT 1;
  ```

---

## Quick SQL Checks

### See all posts (newest first)

```sql
SELECT id, title, slug, author_id, category_id, created_at
FROM posts
ORDER BY created_at DESC
LIMIT 5;
```

### Find posts with "Untitled"

```sql
SELECT id, title, slug FROM posts WHERE title = 'Untitled' LIMIT 10;
```

### See task metadata

```sql
SELECT task_id, topic, status, approval_status, task_metadata
FROM content_tasks
WHERE approval_status = 'approved'
LIMIT 1;
```

---

## Test Multiple Posts

To test with variety:

```bash
# Test 1: Minimal metadata
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{"topic": "Minimal test"}'

# Test 2: AI topic
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{"topic": "Machine Learning Basics", "style": "beginner"}'

# Test 3: Business topic
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{"topic": "Startup Fundraising Tips", "tone": "inspiring"}'
```

Then approve each and compare results in posts table!

---

## Performance Notes

- Approval takes 5-10 seconds (LLM calls happen in background)
- Each call to LLM adds ~200-500ms
- First call might be slower (model initialization)
- Subsequent calls are faster (cached connections)

---

## Success Indicators

✅ All fields populated (except optional ones)  
✅ No "Untitled" titles  
✅ Slugs are unique and meaningful  
✅ Excerpts are professional quality  
✅ SEO metadata present  
✅ Tags/categories relevant to content

**If you see all ✅, the implementation is successful!**
