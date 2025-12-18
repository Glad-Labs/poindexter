# SDXL Image Generation & Approval Workflow - Issues & Solutions

**Date:** December 17, 2025  
**Issues:**

1. Duplicate slug error when creating posts
2. Generated images not stored locally for preview
3. No multi-image generation capability before approval

---

## Issue 1: Duplicate Slug Error

### Root Cause

When a task generates content and creates a post:

- Slug is generated deterministically from title: `"making-delicious-muffins"`
- If the same title is processed twice (retry, duplicate task), the slug already exists in DB
- Database constraint `posts_slug_key` enforces uniqueness
- Current code has NO duplicate checking before insertion

### Current Flow

```python
# task_routes.py:616-618
slug = re.sub(r'[^\w\s-]', '', post_title.lower())
slug = re.sub(r'[-\s]+', '-', slug)
slug = slug.strip('-')

# Then directly INSERT (no duplicate check)
post_result = await db_service.create_post(post_data)
```

### Why It Fails

- No check for existing slug
- Task retry or re-execution hits constraint
- Post creation fails silently (caught exception, logged, but task continues)

### Solution Options

**Option A: Check Before Insert (Recommended)**

```python
# Before creating post, check if slug exists
existing = await db_service.get_post_by_slug(slug)
if existing:
    # Option 1: Skip creating duplicate
    logger.info(f"Post with slug '{slug}' already exists, skipping creation")
    # Option 2: Update existing post instead of creating new
    post_result = await db_service.update_post(existing['id'], post_data)
```

**Option B: Use Upsert (Insert or Update)**

```python
# Database query with ON CONFLICT
INSERT INTO posts (...) VALUES (...)
ON CONFLICT (slug) DO UPDATE SET
    title = EXCLUDED.title,
    content = EXCLUDED.content,
    ...
```

**Option C: Add Uniqueness Counter**

```python
# If slug exists, append counter
slug = "making-delicious-muffins"
if await db_service.get_post_by_slug(slug):
    counter = 2
    while await db_service.get_post_by_slug(f"{slug}-{counter}"):
        counter += 1
    slug = f"{slug}-{counter}"
```

---

## Issue 2: Generated Images Not Stored Locally for Preview

### Current Flow

```
SDXL Generate → Temp file → Upload to Cloudinary/S3 → Return URL → Cleanup temp file
                                    (immediately deleted)
```

**Problem:**

- Image exists only temporarily during generation
- If post approval fails or is delayed, image is gone
- No way to preview image in UI before approval
- No way to see what image will look like in the final post

### Desired Flow

```
SDXL Generate → Save to Downloads Folder → Return local path + URL → Preview in UI
                (persistent, user accessible)
                        ↓
              User approves post
                        ↓
         Upload to Cloudinary → Store CDN URL in DB → Cleanup local file
```

### Why This Matters

1. **User Preview** - See generated image before committing
2. **Iteration** - Generate multiple images, choose best one
3. **Approval** - Can reject/modify before publishing
4. **Audit Trail** - Local copies until approved

### Implementation

**Step 1: Save to User Downloads Folder**

```python
# In media_routes.py generate_featured_image endpoint
import os
from pathlib import Path

# Instead of tempfile, use user downloads
downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
os.makedirs(downloads_path, exist_ok=True)

output_file = f"sdxl-{datetime.now().strftime('%Y%m%d_%H%M%S')}-{request.task_id or 'preview'}.png"
output_path = os.path.join(downloads_path, output_file)

success = await image_service.generate_image(
    prompt=request.prompt,
    output_path=output_path,
    ...
)
```

**Step 2: Return Both Local Path and Metadata**

```python
image_response = ImageGenerationResponse(
    success=True,
    image_url=image_url_cloudinary,  # CDN URL (not yet uploaded)
    local_path=output_path,  # Local file path for preview
    image=ImageMetadata(...),
    preview_mode=True,  # Indicates this is preview, not yet in CDN
    generation_time=elapsed,
)
```

**Step 3: Store Preview Path in Task Metadata**

```python
# In task_routes.py after image generation
task_metadata = {
    "featured_image_url": response.image_url,
    "local_preview_path": response.local_path,  # ← Store for later
    "image_generation_source": response.image.source,
    "generated_at": datetime.now().isoformat(),
    "needs_approval": True,
    "approved": False,
}
```

**Step 4: On Approval, Upload to Cloudinary**

```python
# In approval endpoint (when user approves post)
if task_metadata.get('local_preview_path'):
    local_path = task_metadata['local_preview_path']

    # Now upload to Cloudinary
    cloudinary_url = await upload_to_cloudinary(local_path, task_id)

    # Update post with CDN URL
    post_data['featured_image_url'] = cloudinary_url

    # Cleanup local file (optional - user can keep for records)
    # os.remove(local_path)
```

---

## Issue 3: Multi-Image Generation Before Approval

### Current Flow

```
1 prompt → 1 image → 1 upload → fixed
```

### Desired Flow

```
Generate Image 1 → Save to Downloads (preview)
   ↓ Not satisfied
Generate Image 2 → Save to Downloads (preview) → Choose best one
   ↓ Not satisfied
Generate Image 3 → Save to Downloads (preview) → APPROVE THIS ONE
   ↓
Upload chosen image to Cloudinary → Create post with CDN URL
```

### Implementation

**Step 1: Create Image Generation History Endpoint**

```python
# In media_routes.py
@media_router.post("/generate-image-variations")
async def generate_image_variations(
    task_id: str,
    prompt: str,
    num_variations: int = 3,  # Generate 3 variations
    num_inference_steps: int = 50,
):
    """
    Generate multiple image variations and save locally.
    Return list of local paths for preview in UI.
    """
    generated_images = []

    for i in range(num_variations):
        logger.info(f"Generating variation {i+1}/{num_variations}")

        output_file = f"sdxl-{task_id}-variation-{i+1}-{int(time.time())}.png"
        output_path = os.path.join(downloads_path, output_file)

        success = await image_service.generate_image(
            prompt=prompt,
            output_path=output_path,
            num_inference_steps=num_inference_steps,
        )

        if success:
            generated_images.append({
                "index": i + 1,
                "local_path": output_path,
                "filename": output_file,
                "generated_at": datetime.now().isoformat(),
            })

    return {
        "task_id": task_id,
        "variations": generated_images,
        "prompt": prompt,
    }
```

**Step 2: Store All Variations in Task**

```python
# In task metadata
task_metadata = {
    "image_variations": [
        {"index": 1, "local_path": "/Users/.../Downloads/sdxl-xxx-1.png", "selected": False},
        {"index": 2, "local_path": "/Users/.../Downloads/sdxl-xxx-2.png", "selected": False},
        {"index": 3, "local_path": "/Users/.../Downloads/sdxl-xxx-3.png", "selected": True},  # ← Chosen
    ],
    "selected_image_index": 3,
}
```

**Step 3: UI Workflow**

```
Oversight Hub UI Flow:
─────────────────────

1. Task created with prompt
2. Click "Generate Images"
   → Shows 3 previews from Downloads folder
3. User selects best image (radio button)
4. Click "Approve" → Upload selected to Cloudinary → Create post
5. Click "Reject" → Delete local images → Prompt for new prompt
```

---

## Recommended Implementation Order

### Phase 1: Fix Duplicate Slug (IMMEDIATE - Blocks Publishing)

1. Add `get_post_by_slug()` method to database_service
2. Modify task_routes.py to check slug before insert
3. Test: Generate same content twice, verify no error

### Phase 2: Local Preview Storage (HIGH PRIORITY)

1. Modify media_routes.py to save to Downloads folder
2. Update task metadata to store local_path
3. Modify approval endpoint to use local_path for upload
4. Test: Generate image → check Downloads → approve → check Cloudinary

### Phase 3: Multi-Image Generation (NICE TO HAVE)

1. Add generate_image_variations endpoint
2. Modify UI to show preview carousel
3. Add selection mechanism
4. Test: Generate 3 images → choose best → approve

---

## Code Changes Summary

### File 1: database_service.py (NEW METHOD)

```python
async def get_post_by_slug(self, slug: str) -> Optional[dict]:
    """Check if post with slug already exists"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, slug FROM posts WHERE slug = $1",
            slug
        )
        return dict(row) if row else None

async def update_post(self, post_id: str, post_data: dict) -> dict:
    """Update existing post (for upsert behavior)"""
    # Similar to create_post but with UPDATE instead of INSERT
    ...
```

### File 2: task_routes.py (MODIFY \_execute_and_publish_task)

```python
# Before creating post, check for duplicate slug
existing_post = await db_service.get_post_by_slug(slug)
if existing_post:
    logger.warning(f"Post with slug '{slug}' already exists (ID: {existing_post['id']}), skipping post creation")
    post_result = existing_post  # Use existing post instead of creating
else:
    post_result = await db_service.create_post(post_data)
```

### File 3: media_routes.py (MODIFY generate_featured_image)

```python
# Save to Downloads instead of temp
from pathlib import Path
downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
os.makedirs(downloads_path, exist_ok=True)
output_path = os.path.join(downloads_path, output_file)

# Return local_path in response
return ImageGenerationResponse(
    ...
    local_path=output_path,
    preview_mode=True,
)
```

---

## Testing Checklist

- [ ] Generate post twice with same title → no error
- [ ] Generate image → saved to Downloads folder
- [ ] Generated image has proper timestamp/task_id in filename
- [ ] Approve post → local image uploaded to Cloudinary
- [ ] Verify Cloudinary URL in posts table
- [ ] Generate 3 image variations → all in Downloads
- [ ] Select variation 2 → uploads variation 2 to CDN
- [ ] Rejection workflow → can retry with new prompt

---

## Quick Decision: Which Approach for Duplicate Slug?

**RECOMMENDED: Option A (Check Before Insert)**

Why:

- Simplest to implement
- Clear behavior: skip duplicate or update
- No database constraint changes needed
- Easy to understand and debug

Alternative Option B (Upsert) adds complexity, Option C (Counter) changes URL structure.

---

## Current Image Generation URL Flow

**Before (Current - Broken for approval):**

```
POST /api/media/generate-image
→ Generate in temp folder
→ Upload to Cloudinary (or S3, or local)
→ Return URL
→ Temp file deleted
→ If approval fails later, no preview available
```

**After (Proposed - Supports approval workflow):**

```
POST /api/media/generate-image
→ Generate to Downloads folder (PERSISTENT)
→ Return URL + local_path
→ Store both in task metadata
→ User previews image from Downloads
→ On approval: upload local file to Cloudinary
→ Update post with CDN URL
→ Optional: cleanup local file
```

---

## Implementation Priority

**MUST DO (Blocks Release):**

1. Fix duplicate slug error
2. Store images locally for preview

**SHOULD DO (Improves UX):** 3. Multi-image generation

**NICE TO HAVE (Polish):** 4. Image gallery in Oversight Hub 5. Automatic cleanup of old local images
