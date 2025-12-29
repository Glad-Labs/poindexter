# Image Storage & Metadata Implementation - CRITICAL FIXES

**Status**: Root causes identified, ready for implementation  
**Last Updated**: December 17, 2025

---

## 🔴 CRITICAL ISSUES FOUND

### Issue #1: Missing Metadata in Posts Table

```sql
SELECT author_id, category_id, tag_ids, created_by, updated_by
FROM posts
WHERE id = '0c071aef-2d66-4256-87c8-9fab827fd9da';

Results:
author_id: NULL
category_id: NULL
tag_ids: NULL
created_by: NULL
updated_by: NULL
```

**Root Cause**: Metadata fields in task_metadata are NOT being extracted and passed to `create_post()`

### Issue #2: Image Not Stored in posts table

```sql
SELECT featured_image_url FROM posts WHERE id = '0c071aef-2d66-4256-87c8-9fab827fd9da';

Result: NULL
```

**Root Cause**: Image stored in `content_tasks.featured_image_url` is NULL even though task was generated

### Issue #3: Image Generation Response Not Captured

- Images generated successfully (GPU working ✅)
- Progress tracked via WebSocket ✅
- BUT: Image response from `/api/media/generate` not being stored in task_metadata
- When task approved, no image URL found to store in posts table

---

## 📊 Data Flow Analysis

### Current Broken Flow:

```
User generates image
    ↓
POST /api/media/generate
    ↓
Image generated successfully (base64)
    ✗ Response returned but NOT stored in task
    ↓
User approves task
    ↓
Approval endpoint tries to find image in:
  - task_metadata.featured_image_url → NULL
  - task_metadata.image.url → NULL
  - task_metadata.image_url → NULL
  - task_metadata.featured_image.url → NULL
    ↓
featured_image_url written as NULL to posts table
```

### What Should Happen:

```
User generates image
    ↓
POST /api/media/generate with task_id
    ↓
Image generated successfully
    ↓
Response includes:
  - featured_image_url: "/images/generated/abc-123.png"
  - image_metadata: {photographer, source, etc}
    ↓
Store in task_metadata or return to UI
    ↓
UI displays image with metadata
    ↓
User approves task
    ↓
Approval endpoint finds image URL in task_metadata
    ↓
All fields written to posts table:
  - featured_image_url ✓
  - author_id ✓
  - category_id ✓
  - tag_ids ✓
  - created_by ✓
  - updated_by ✓
```

---

## 🔧 FIXES TO IMPLEMENT

### FIX #1: Store Image in Task Metadata During Generation

**File**: `src/cofounder_agent/routes/media_routes.py`

**Current Code** (Lines ~230):

```python
# Image returned to frontend but NOT stored in task_metadata
image_url = f"data:image/png;base64,{image_data}"
return {
    "status": "success",
    "image_url": image_url,
    "image_metadata": {
        "photographer": "SDXL (Local Generation)",
        "width": 1024,
        "height": 1024
    }
}
```

**What's Missing**:

- If `task_id` provided, should update task_metadata with image URL
- Should save image to file system, not just return base64
- Should return file path, not data URI

**Fix Implementation**:

```python
@router.post("/generate")
async def generate_image(
    prompt: str,
    use_generation: bool = True,
    task_id: Optional[str] = None,  # ← ADD THIS
    db: AsyncSession = Depends(get_db_session)
):
    """Generate image and optionally store in task metadata"""

    # Generate image
    image_bytes = ... # SDXL generation

    # Save to file system instead of returning base64
    import uuid
    image_filename = f"post-{uuid.uuid4()}.png"
    image_path = f"/images/generated/{image_filename}"
    full_path = f"web/public-site/public{image_path}"

    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, 'wb') as f:
        f.write(image_bytes)

    # If task_id provided, update task metadata
    if task_id:
        # Update content_tasks.task_metadata
        update_query = """
        UPDATE content_tasks
        SET task_metadata = jsonb_set(
          COALESCE(task_metadata, '{}'::jsonb),
          '{featured_image_url}',
          to_jsonb(%s::text)
        )
        WHERE task_id = %s
        """
        await db.execute(update_query, (image_path, task_id))
        await db.commit()

    # Return file path instead of base64
    return {
        "status": "success",
        "image_url": image_path,  # ← FILE PATH not data URI
        "image_metadata": {
            "photographer": "SDXL (Local Generation)",
            "width": 1024,
            "height": 1024,
            "source": "sdxl",
            "generated_at": datetime.utcnow().isoformat()
        }
    }
```

**Benefits**:

- ✅ Image saved to persistent storage (public-site/public/images/generated/)
- ✅ Can be accessed by public site
- ✅ Can be cached/optimized by CDN
- ✅ Metadata automatically updated in task

---

### FIX #2: Update create_post to Handle Missing Fields

**File**: `src/cofounder_agent/services/database_service.py`

**Current Issue** (Line 889):

```python
# OLD - was looking for wrong field name
featured_image_url = post_data.get("featured_image")  # ← WRONG FIELD

# NEW - look in right places
featured_image_url = (
    post_data.get("featured_image_url")  # From metadata
    or post_data.get("featured_image")   # Fallback (old format)
    or None
)
```

**Already Fixed**: ✅ create_post now includes all 18 columns

**Still Needed**: Verify task_metadata is being passed correctly

```python
async def create_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create post from generated content"""

    # VERIFY we're getting these fields:
    required_metadata_fields = [
        'featured_image_url',
        'author_id',
        'category_id',
        'tag_ids',
        'created_by',
        'updated_by'
    ]

    for field in required_metadata_fields:
        if not post_data.get(field):
            logger.warning(f"Missing {field} in post_data")

    # Continue with insert...
```

---

### FIX #3: Update Approval Endpoint to Extract All Metadata

**File**: `src/cofounder_agent/routes/content_routes.py`

**Current Issue** (Line 520):

- Extracts metadata but may not be finding it in right location
- Need to check multiple fallback locations

**Fix**:

```python
@router.post("/approve")
async def approve_task(request: ApprovalRequest, db: AsyncSession = Depends(get_db_session)):
    """Approve task and publish to posts table"""

    # Get task from content_tasks
    task = await get_task_metadata(request.task_id, db)
    task_metadata = task.get('task_metadata', {})

    # CRITICAL: Find image URL - check all possible locations
    featured_image_url = None
    image_sources = [
        task_metadata.get('featured_image_url'),  # Direct field
        task_metadata.get('image_url'),            # Alternative name
        task_metadata.get('image', {}).get('url'), # Nested object
        task.get('featured_image_url'),            # Task table field
    ]

    for url in image_sources:
        if url and not url.startswith('data:'):  # Skip base64
            featured_image_url = url
            break

    logger.info(f"Found featured_image_url: {featured_image_url}")

    # Extract metadata with fallbacks
    post_data = {
        'featured_image_url': featured_image_url,
        'cover_image_url': task_metadata.get('cover_image_url'),
        'author_id': task_metadata.get('author_id'),
        'category_id': task_metadata.get('category_id'),
        'tag_ids': task_metadata.get('tag_ids') or task_metadata.get('tags') or [],
        'created_by': request.reviewer_id,
        'updated_by': request.reviewer_id,
        'seo_title': task_metadata.get('seo_title'),
        'seo_description': task_metadata.get('seo_description'),
        'seo_keywords': task_metadata.get('seo_keywords', ''),
        # ... other fields
    }

    # Create post
    result = await db_service.create_post(post_data)
    return result
```

---

### FIX #4: Fix Frontend Image Display

**File**: `web/oversight-hub/src/components/TaskManagement.jsx`

**Current Issue**:

- Displaying base64 data URIs (works but not ideal)
- Not showing metadata (title, excerpt)
- Not parsing content

**Fix**:

```jsx
import React, { useState } from 'react';

function TaskManagement() {
  const [task, setTask] = useState(null);

  // Parse generated content to extract title and body
  const parseContent = (fullContent) => {
    if (!fullContent) return { title: '', excerpt: '', body: '' };

    // Split by lines
    const lines = fullContent.split('\n').filter((l) => l.trim());

    // First line with text is usually the title
    let title = '';
    let contentStartIndex = 0;

    for (let i = 0; i < lines.length; i++) {
      // Look for markdown header or first substantial line
      if (lines[i].startsWith('#') || (i === 0 && lines[i].length > 10)) {
        title = lines[i]
          .replace(/^#+\s/, '') // Remove markdown headers
          .trim();
        contentStartIndex = i + 1;
        break;
      }
    }

    // Rest is body
    const body = lines.slice(contentStartIndex).join('\n').trim();

    // Extract excerpt (first 200 chars)
    const excerpt = body.substring(0, 200) + (body.length > 200 ? '...' : '');

    return { title, excerpt, body };
  };

  const handleApprove = async () => {
    try {
      // When approving, send all metadata
      const contentParts = parseContent(task.result.content);

      const response = await fetch('/api/content/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: task.id,
          reviewer_id: currentUser.id,
          metadata: {
            author_id: task.metadata?.author_id,
            category_id: task.metadata?.category_id,
            tags: task.metadata?.tags || [],
            seo_title: task.metadata?.seo_title,
            seo_description: task.metadata?.seo_description,
            seo_keywords: task.metadata?.seo_keywords,
            title: contentParts.title,
            excerpt: contentParts.excerpt,
            featured_image_url: task.result?.image_url,
          },
        }),
      });

      if (response.ok) {
        alert('Task approved and published!');
      }
    } catch (error) {
      console.error('Error approving task:', error);
    }
  };

  // Display task results with proper formatting
  return (
    <div className="task-results">
      {task && (
        <>
          {/* Image Display */}
          {task.result?.image_url && (
            <div className="image-preview">
              <img
                src={task.result.image_url}
                alt="Generated"
                style={{ maxWidth: '100%', height: 'auto' }}
              />
              {task.result?.image_metadata && (
                <p className="image-credit">
                  Photo by {task.result.image_metadata.photographer}
                </p>
              )}
            </div>
          )}

          {/* Content Preview */}
          {task.result?.content && (
            <>
              <h2>{parseContent(task.result.content).title || 'Preview'}</h2>
              <p className="excerpt">
                {parseContent(task.result.content).excerpt}
              </p>
              <button onClick={() => setShowFullContent(true)}>
                Read Full Article
              </button>
            </>
          )}

          {/* Metadata Display */}
          <div className="metadata">
            <div>Author ID: {task.metadata?.author_id || '—'}</div>
            <div>Category: {task.metadata?.category_id || '—'}</div>
            <div>Tags: {(task.metadata?.tags || []).join(', ') || '—'}</div>
          </div>

          {/* Action Buttons */}
          <button onClick={handleApprove} className="btn-approve">
            Approve & Publish
          </button>
          <button onClick={handleReject} className="btn-reject">
            Reject
          </button>
        </>
      )}
    </div>
  );
}

export default TaskManagement;
```

---

## 📋 Implementation Checklist

### Phase 1: Core Image Storage Fix

- [ ] **FIX #1**: Update `media_routes.py` to:
  - [ ] Save image to file system (public/images/generated/)
  - [ ] Return file path instead of base64
  - [ ] Update task_metadata if task_id provided
  - [ ] Test image storage works

- [ ] **FIX #2**: Verify `create_post` handles all fields
  - [ ] Run create_post with full metadata
  - [ ] Check logs for missing fields warning
  - [ ] Test post created with all fields populated

- [ ] **FIX #3**: Update approval endpoint
  - [ ] Extract image URL from multiple locations
  - [ ] Extract all metadata fields
  - [ ] Log what's found vs what's missing
  - [ ] Test approval stores all metadata

### Phase 2: Frontend Integration

- [ ] **FIX #4**: Update TaskManagement component
  - [ ] Implement content parser
  - [ ] Display title, excerpt, body separately
  - [ ] Show image with metadata
  - [ ] Pass metadata on approval

### Phase 3: Testing & Verification

- [ ] Generate image → check file saved to disk
- [ ] Generate image → check task_metadata updated
- [ ] Approve task → check all posts table fields populated
- [ ] View public site → check image displays
- [ ] Check database: featured_image_url not NULL
- [ ] Check database: author_id populated
- [ ] Check database: category_id populated
- [ ] Check database: tag_ids populated

### Phase 4: Optimization (Post-Fix)

- [ ] Add CDN configuration for images
- [ ] Implement image optimization (WebP, resizing)
- [ ] Add image cleanup for old generated images
- [ ] Migrate existing posts with images

---

## 🧪 SQL Queries to Verify Fixes

### Before Implementation:

```sql
-- Should show NULL values
SELECT
  id, title, featured_image_url, author_id, category_id,
  tag_ids, created_by, updated_by
FROM posts
WHERE status = 'published'
LIMIT 5;
```

### After Implementation:

```sql
-- Should show populated values
SELECT
  id, title, featured_image_url, author_id, category_id,
  tag_ids, created_by, updated_by
FROM posts
WHERE status = 'published'
  AND featured_image_url IS NOT NULL
  AND created_by IS NOT NULL
ORDER BY created_at DESC
LIMIT 5;
```

### Check Image Files:

```bash
# Verify images are being saved
ls -la web/public-site/public/images/generated/
du -sh web/public-site/public/images/generated/
```

---

## 📝 Notes

### Why Image Stored in File System vs Database?

- **File System Advantages**:
  - ✅ Small database footprint
  - ✅ Easy CDN integration
  - ✅ Can use image optimization tools
  - ✅ Can serve at scale
  - ✅ Industry standard approach

- **Database (base64) Disadvantages**:
  - ❌ 33% size overhead
  - ❌ Can't optimize images
  - ❌ Can't use CDN
  - ❌ Database bloat
  - ❌ Slow queries

### Why Image URL in Task Metadata?

- Keeps task self-contained
- When task approved, all needed data is in one place
- Frontend doesn't need separate lookup
- Easy to track what was used to generate post

### Why Missing Metadata in Posts Table?

- Current approval endpoint doesn't extract all fields from task_metadata
- Image generation endpoint doesn't store image URL in task
- No validation that required fields are present before creating post

---

## 🚀 Priority & Timeline

**CRITICAL (Do Today)**:

1. FIX #1: Store image in file system + task metadata
2. FIX #3: Update approval endpoint to extract all fields
3. Verify posts table populated correctly

**HIGH (Do This Week)**: 4. FIX #4: Fix frontend display 5. Test complete workflow end-to-end 6. Update public site template

**MEDIUM (Optimization)**: 7. CDN integration 8. Image optimization 9. Bulk migrate existing posts
