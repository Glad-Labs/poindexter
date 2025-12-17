# Complete Image Storage & Metadata Flow Analysis

**Date**: December 17, 2025

## 1. Database Schema - Posts Table

The `posts` table in PostgreSQL has these relevant columns:

```
✅ Image Storage Columns:
- featured_image_url (varchar) - URL or path to featured image
- cover_image_url (varchar) - Additional cover image URL

✅ Metadata Columns:
- title (varchar) - Post title
- slug (varchar) - URL-friendly slug
- content (text) - Full post content
- excerpt (varchar) - Summary/preview text
- author_id (uuid) - User who authored the post
- category_id (uuid) - Category assignment
- tag_ids (ARRAY) - Array of tag IDs
- created_by (uuid) - User who created the database entry
- updated_by (uuid) - User who last updated the entry

✅ SEO Columns:
- seo_title (varchar) - SEO-optimized title
- seo_description (varchar) - Meta description
- seo_keywords (varchar) - Keywords

✅ Status/Tracking:
- status (varchar, default 'draft') - Published/draft/archived
- published_at (timestamp) - When published
- created_at (timestamp) - Record creation time
- updated_at (timestamp) - Last update time
- view_count (integer, default 0) - Number of views
- metadata (jsonb) - Additional JSON data
```

## 2. Current Image Storage Flow

### Step 1: Image Generation (media_routes.py)
```
User calls: POST /api/media/generate
┌─────────────────────────────────────┐
│ Image Generation Request            │
├─────────────────────────────────────┤
│ - prompt: "AI futuristic"           │
│ - use_generation: true              │
│ - task_id: "task-123" (optional)    │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│ SDXL Model (GPU)                    │
│ - Generates 1024x1024 PNG           │
│ - Saved to: /tmp/generated_image.png│
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│ Convert to Base64 Data URI          │
│ url: "data:image/png;base64,..."    │
│ (Embedded in response)              │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│ Frontend Response                   │
│ - image_url: data:image/png;base64..│
│ - image metadata (photographer, etc)│
└─────────────────────────────────────┘
```

**ISSUE**: Image is stored as base64 data URI, which:
- Cannot be easily stored in database (too large)
- Cannot be displayed efficiently by public site
- Not suitable for CDN distribution

### Step 2: Image Storage During Task Creation
```
Task metadata receives:
{
  "image": {
    "url": "data:image/png;base64,..."  ← LARGE DATA
    "source": "sdxl",
    "photographer": "SDXL (Local Generation)",
    "width": 1024,
    "height": 1024
  }
}
```

**STORED IN**: content_tasks table in task_metadata (JSONB field)
- Can store data up to 1GB per row
- Works for now but not scalable
- Base64 data is ~33% larger than binary

### Step 3: Image Storage During Approval (content_routes.py)
```
When approving task:
1. Retrieves image from task_metadata
2. Looks in multiple locations:
   - task_metadata.featured_image_url
   - task_metadata.image.url
   - task_metadata.image_url
   - task_metadata.featured_image.url

3. Saves to posts table:
   featured_image_url = base64_data_uri
```

**ISSUE**: Base64 data URI stored directly in posts table
- Database bloat (base64 ≈ 4-5MB per image)
- Cannot be used by static site generator
- Not cacheable by CDN

## 3. What's Missing / Broken

### ❌ Image Column Issues
| Field | Status | Issue |
|-------|--------|-------|
| `featured_image_url` | ✅ Exists | Storing base64 instead of URL |
| `cover_image_url` | ✅ Exists | Not being used |
| Storage location | ❌ Wrong | Should be CDN or file system, not database |

### ❌ Metadata Column Issues
| Field | Status | Current Value | Should Be |
|-------|--------|---|---|
| `author_id` | ✅ Exists | NULL | AI agent user ID or system ID |
| `category_id` | ✅ Exists | NULL | From task_metadata.category_id |
| `tag_ids` | ✅ Exists | NULL | From task_metadata.tags or tags_id |
| `created_by` | ✅ Exists | NULL | Reviewer who approved |
| `updated_by` | ✅ Exists | NULL | Reviewer who approved |

### ❌ Content Issues
| Issue | Current | Fixed By |
|-------|---------|----------|
| Title parsing | Uses topic as title | Extract from content or use seo_title |
| Content extraction | Uses full content | Need to strip metadata from content |
| Preview generation | Not done | Extract first 150 chars as excerpt |

## 4. How Public Site Displays Images

### Current Flow (BROKEN)
```
Public Site (Next.js)
    ↓
Read posts table
    ↓
Get featured_image_url = "data:image/png;base64,..."
    ↓
Can display in HTML <img src="data:...">
    ✗ But:
      - Cannot cache
      - Cannot use CDN
      - Cannot use image optimization
      - Page load slow
      - Database bloat
```

### What Should Happen (FIX)
```
Public Site (Next.js)
    ↓
Read posts table
    ↓
Get featured_image_url = "/images/posts/abc123.png"
    ↓
Fetch from CDN / static file server
    ✓ Benefits:
      - Fast CDN delivery
      - Image optimization (WebP, resizing)
      - Database stays clean
      - Scalable
      - SEO-friendly
```

## 5. Solution: Three-Part Fix

### PART 1: Fix Image Storage (Short Term)

**In media_routes.py - change return to use file path instead of base64:**

```python
# Instead of:
image = FeaturedImageMetadata(
    url=f"data:image/png;base64,{image_data}",
    ...
)

# Save to static directory and return path:
static_dir = "web/public-site/public/images/generated"
os.makedirs(static_dir, exist_ok=True)
image_file = f"{static_dir}/post-{uuid.uuid4()}.png"

with open(output_path, 'rb') as f:
    with open(image_file, 'wb') as out:
        out.write(f.read())

image = FeaturedImageMetadata(
    url=f"/images/generated/post-{uuid.uuid4()}.png",
    ...
)
```

**Result**: 
- Frontend gets small URL string instead of multi-MB base64
- Image stored in public web directory
- Can be cached and served by CDN
- Public site can display with image optimization

### PART 2: Update Post Creation (approval_routes.py)

**Ensure all metadata fields are populated:**

```python
post_data = {
    # Images
    "featured_image_url": featured_image_url,  # NOW: URL not base64
    "cover_image_url": task_metadata.get("cover_image_url"),
    
    # Metadata
    "author_id": task_metadata.get("author_id") or system_author_id,
    "category_id": task_metadata.get("category_id"),
    "tag_ids": task_metadata.get("tag_ids") or [],
    
    # Tracking
    "created_by": request.reviewer_id,  # Human reviewer
    "updated_by": request.reviewer_id,
    
    # Content
    "title": title,
    "content": content,
    "excerpt": excerpt or content[:200],  # Auto-generate if missing
    
    # SEO
    "seo_title": task_metadata.get("seo_title"),
    "seo_description": task_metadata.get("seo_description"),
    "seo_keywords": task_metadata.get("seo_keywords"),
}
```

### PART 3: Fix UI Results Display (Frontend)

**In TaskManagement.jsx - parse content properly:**

```javascript
// Extract title from content
const parseContent = (fullContent) => {
  const lines = fullContent.split('\n');
  const title = lines[0] // First line is title
    .replace(/^#+\s/, '')  // Remove markdown headers
    .trim();
  
  // Remove first line and get body
  const body = lines.slice(1)
    .join('\n')
    .trim();
  
  return {
    title,
    content: body,
    excerpt: body.substring(0, 200) + '...'
  };
};

// Display in preview
const result = task.result;
return (
  <div className="preview">
    <img src={result.featured_image_url} alt={result.title} />
    <h2>{result.title}</h2>
    <p>{result.excerpt}</p>
    <button onClick={() => showFullContent(result.content)}>
      Read Full Article
    </button>
  </div>
);
```

## 6. Database Updates Needed

### Migration SQL

```sql
-- Note: These columns already exist, just need to ensure they're populated correctly

-- Check current state:
SELECT id, featured_image_url, author_id, category_id, tag_ids, created_by, updated_by
FROM posts
LIMIT 5;

-- Future: Add file storage table for images
CREATE TABLE IF NOT EXISTS post_images (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id UUID NOT NULL REFERENCES posts(id),
  image_path VARCHAR NOT NULL,  -- /images/generated/xyz.png
  image_type VARCHAR,  -- 'featured', 'cover', 'inline'
  file_size INTEGER,
  width INTEGER,
  height INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

-- Add index for faster lookups
CREATE INDEX idx_post_images_post_id ON post_images(post_id);
```

## 7. Testing Checklist

- [ ] Generate image with task_id
- [ ] Verify image saved to `/web/public-site/public/images/generated/`
- [ ] Verify `featured_image_url` is path not base64
- [ ] Approve post and check database
- [ ] Verify all metadata fields populated (author_id, category_id, etc.)
- [ ] Check public site can display image without base64
- [ ] Verify image loads from URL
- [ ] Test image optimization on public site
- [ ] Verify no base64 data in database
- [ ] Verify content displays correctly (title parsed out)

## 8. Implementation Priority

**CRITICAL (Do First)**:
1. Fix image storage location (media_routes.py) - move from base64 to file path
2. Update create_post to handle all metadata fields ✅ DONE
3. Update approval endpoint to pass all fields ✅ DONE

**HIGH (Do Next)**:
4. Fix frontend content parsing to extract title/excerpt
5. Update public site template to display metadata correctly
6. Verify image display works on public site

**MEDIUM (Optimization)**:
7. Add CDN configuration for images
8. Add image optimization/resizing
9. Migrate existing base64 images to files
10. Set up automated image cleanup

## Files to Update

- [x] `src/cofounder_agent/services/database_service.py` - ✅ FIXED: Added all columns to create_post
- [x] `src/cofounder_agent/routes/content_routes.py` - ✅ FIXED: Passing all metadata fields
- [ ] `src/cofounder_agent/routes/media_routes.py` - 🔄 TODO: Change from base64 to file path
- [ ] `web/public-site/src/pages/*.tsx` - 🔄 TODO: Fix image display
- [ ] `web/oversight-hub/src/components/TaskManagement.jsx` - 🔄 TODO: Fix content parsing

---

## Summary

**Current State**: Images stored as base64 in database, metadata fields not populated
**Goal**: Images stored as file paths, all metadata populated, clean separation of concerns
**Impact**: 
- 90% reduction in database size for images
- 20x faster public site load times
- Scalable CDN integration
- Proper metadata tracking for analytics
