# Task-to-Post Publishing Pipeline - FIXED ✓

## Problem Identified
The `create_post()` function in `database_service.py` was trying to insert posts using columns that didn't exist in the actual database schema.

### Schema Mismatch
**Code was trying to use:**
```python
INSERT INTO posts (id, title, slug, content, excerpt, category, status, featured_image, ...)
```

**Actual database schema has:**
- ✓ id, title, slug, content, excerpt, status, featured_image_url, cover_image_url
- ✓ seo_title, seo_description, seo_keywords
- ✗ NO "category" column (has category_id instead)
- ✗ NO "featured_image" column (has featured_image_url)

## Root Cause
The database schema was updated for SEO optimization and normalized categories, but the `create_post()` function was never updated to match the new schema. This caused **silent INSERT failures** - the exception was caught but logged without stopping the task.

## Solution Implemented

### 1. Fixed `database_service.py` - `create_post()` method
Updated to use correct column names and schema:
```python
async def create_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create new post in posts table"""
    post_id = post_data.get("id") or str(uuid4())
    
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO posts (
                id, title, slug, content, excerpt, 
                featured_image_url,
                status, seo_title, seo_description, seo_keywords,
                created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
            RETURNING id, title, slug, content, excerpt, status, created_at, updated_at
            """,
            post_id,
            post_data.get("title"),
            post_data.get("slug"),
            post_data.get("content"),
            post_data.get("excerpt"),
            post_data.get("featured_image"),
            post_data.get("status", "draft"),
            post_data.get("seo_title") or post_data.get("title"),
            post_data.get("seo_description") or post_data.get("excerpt"),
            post_data.get("seo_keywords", ""),
        )
        return dict(row)
```

### 2. Fixed `task_routes.py` - `_execute_and_publish_task()` function
Updated Step 5 to use correct field names:
```python
# Create post data structure matching actual posts table schema
post_data = {
    "id": str(uuid_lib.uuid4()),
    "title": post_title,
    "slug": slug,
    "content": generated_content,
    "excerpt": (generated_content[:200] + "...") if len(generated_content) > 200 else generated_content,
    "seo_title": post_title,
    "seo_description": (generated_content[:150] + "...") if len(generated_content) > 150 else generated_content,
    "seo_keywords": topic or "generated,content,ai",
    "status": "published",  # Auto-publish generated posts
    "featured_image": task.get('featured_image'),
}
```

## Verification - Tests Passed ✓

### Test 1: Direct Function Call
```python
# Created post directly via create_post()
result = await db_service.create_post(post_data)
# Result: SUCCESS - Post created with ID, title, slug, status
```

### Test 2: End-to-End Workflow
```
1. Task created: "The Impact of AI on Modern Development"
   - Status: PENDING → COMPLETED
   - Result: post_created = true

2. Database verification:
   - Query: SELECT * FROM posts WHERE title = 'The Impact of AI on Modern Development'
   - Result: FOUND - 3552 chars of content, status=published

3. Second test: "Future of Machine Learning"
   - Task Status: COMPLETED, post_created = true
   - Database: FOUND - 3266 chars of content, status=published
```

### Database Results
```sql
SELECT id, title, slug, LENGTH(content) as content_length, status, created_at
FROM posts
WHERE created_at > NOW() - INTERVAL '5 minutes'
ORDER BY created_at DESC;

-- Results (2 posts created from tasks):
id                                   | title                                  | slug                                    | content_length | status    | created_at
c7985ef2-0820-4610-81d2-2234e546e0be | Future of Machine Learning             | future-of-machine-learning              | 3266           | published | 2025-12-02 22:21:03
cdd26719-be7e-4293-b84c-7084a39d9da1 | The Impact of AI on Modern Development | the-impact-of-ai-on-modern-development | 3552           | published | 2025-12-02 22:17:46
```

## Impact
✅ **Task-to-Post Publishing Pipeline NOW WORKS**
- Tasks complete → Posts automatically created in database
- Content preserved with full markdown formatting
- Posts auto-published (status="published")
- SEO fields populated (title, description, keywords)
- Slug generation working correctly (lowercase, hyphens, no special chars)

## Files Modified
1. `src/cofounder_agent/services/database_service.py` - `create_post()` method (35 lines)
2. `src/cofounder_agent/routes/task_routes.py` - `_execute_and_publish_task()` Step 5 (30 lines)

## Testing Commands
```bash
# Create a test task
curl -X POST "http://localhost:8000/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"topic": "Your Topic", "task_name": "Test", "type": "content_generation"}'

# Wait 45+ seconds for completion, then verify
curl -s "http://localhost:8000/api/tasks/{task_id}" | grep -A 2 '"post_created"'

# Query database for posts
SELECT * FROM posts WHERE created_at > NOW() - INTERVAL '5 minutes' ORDER BY created_at DESC;
```

## Status
**✅ COMPLETE AND VERIFIED**
- Pipeline working end-to-end
- Multiple tests passed
- Database inserts confirmed
- Ready for production use
