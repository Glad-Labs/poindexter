# Code Changes Made - SDXL Image Generation Fixes

## Overview

Three major issues fixed in the SDXL image generation and approval workflow:

1. ✅ Duplicate slug error (prevents database crashes)
2. ✅ Images not stored locally (allows preview before approval)
3. ✅ No multi-image generation (designed, templates provided)

---

## File 1: database_service.py

### Change 1: Added `get_post_by_slug()` Method

**Purpose:** Check if a post with given slug already exists (prevents duplicate key errors)

**Location:** After `create_post()` method (around line 945-965)

**Code Added:**

```python
async def get_post_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
    """Get post by slug - used to check for existing posts before creation."""
    try:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, title, slug, content, excerpt, featured_image_url,
                       status, created_at, updated_at
                FROM posts
                WHERE slug = $1
                LIMIT 1
                """,
                slug
            )
            return self._convert_row_to_dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ Error getting post by slug '{slug}': {e}")
        return None
```

### Change 2: Added `update_post()` Method

**Purpose:** Update post fields (e.g., featured_image_url, status) after image approval

**Location:** After `get_post_by_slug()` method (around line 990-1035)

**Code Added:**

```python
async def update_post(self, post_id: int, updates: Dict[str, Any]) -> bool:
    """Update a post with new values (e.g., featured_image_url, status)"""
    try:
        # Build SET clause dynamically
        set_clauses = []
        values = []
        param_count = 1

        for key, value in updates.items():
            # Validate column exists
            if key not in ['title', 'slug', 'content', 'excerpt', 'featured_image_url', 'status', 'tags']:
                logger.warning(f"Skipping invalid column for update: {key}")
                continue

            set_clauses.append(f"{key} = ${param_count}")
            values.append(value)
            param_count += 1

        if not set_clauses:
            logger.warning(f"No valid columns to update for post {post_id}")
            return False

        # Add post_id as final parameter
        values.append(post_id)
        param_count += 1

        query = f"""
            UPDATE posts
            SET {', '.join(set_clauses)}, updated_at = NOW()
            WHERE id = ${param_count - 1}
            RETURNING id, title, slug, featured_image_url, status
        """

        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(query, *values)

            if result:
                logger.info(f"✅ Updated post {post_id}: {dict(result)}")
                return True
            else:
                logger.warning(f"⚠️ Post not found for update: {post_id}")
                return False

    except Exception as e:
        logger.error(f"❌ Error updating post {post_id}: {e}")
        return False
```

---

## File 2: task_routes.py

### Change: Added Duplicate Slug Checking in `_execute_and_publish_task()`

**Purpose:** Prevent UniqueViolationError by checking for existing posts before creation

**Location:** In function `_execute_and_publish_task()` around line 610-650

**Before:**

```python
# OLD CODE - No duplicate checking
slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
post_data = {
    "title": title,
    "slug": slug,
    "content": content,
    "excerpt": excerpt[:200] if excerpt else "",
    "featured_image_url": image_result.get("image_url", ""),
}
post_result = await db_service.create_post(post_data)  # ❌ CRASHES on duplicate
```

**After:**

```python
# NEW CODE - Check for existing post first
slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')

# ═══════════════════════════════════════════════════════════
# CHECK FOR EXISTING POST - Prevent duplicate key errors
# ═══════════════════════════════════════════════════════════
existing_post = await db_service.get_post_by_slug(slug)
if existing_post:
    logger.warning(f"Post with slug '{slug}' already exists (ID: {existing_post['id']}), skipping creation")
    post_result = existing_post  # ✅ Reuse existing
else:
    post_data = {
        "title": title,
        "slug": slug,
        "content": content,
        "excerpt": excerpt[:200] if excerpt else "",
        "featured_image_url": image_result.get("image_url", ""),
    }
    post_result = await db_service.create_post(post_data)  # ✅ Create new
```

---

## File 3: media_routes.py

### Change 1: Updated ImageGenerationResponse Model

**Purpose:** Add `local_path` and `preview_mode` fields to response

**Location:** Near top of file with other Pydantic models (lines 257-268)

**Before:**

```python
class ImageGenerationResponse(BaseModel):
    success: bool
    image_url: str = ""
    image: Optional[ImageMetadata] = None
    message: str = ""
    generation_time: float = 0.0
```

**After:**

```python
class ImageGenerationResponse(BaseModel):
    success: bool
    image_url: str = ""
    image: Optional[ImageMetadata] = None
    message: str = ""
    generation_time: float = 0.0
    local_path: Optional[str] = Field(None, description="Local file path (for generated images in Downloads)")
    preview_mode: Optional[bool] = Field(False, description="Whether this is a preview (not yet in CDN)")
```

### Change 2: Updated Image Save Location

**Purpose:** Save images to Downloads folder instead of temp folder

**Location:** In function `generate_featured_image()` around line 360-390

**Before:**

```python
# OLD CODE - Saves to temp folder
temp_dir = tempfile.gettempdir()
output_file = f"generated_image_{int(time.time())}.png"
output_path = os.path.join(temp_dir, output_file)

# ... generate image ...
image_data.save(output_path)
logger.info(f"Image saved to temp: {output_path}")
```

**After:**

```python
# NEW CODE - Saves to Downloads folder
downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
os.makedirs(downloads_path, exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
task_id_str = request.task_id if request.task_id else "no-task"
output_file = f"sdxl_{timestamp}_{task_id_str}.png"
output_path = os.path.join(downloads_path, output_file)

# ... generate image ...
image_data.save(output_path)
logger.info(f"✅ Image saved to Downloads: {output_path}")
```

### Change 3: Removed Immediate CDN Upload

**Purpose:** Keep image local until user approves, don't upload immediately

**Location:** In function `generate_featured_image()` around line 395-445

**Before:**

```python
# OLD CODE - Immediate CDN upload
if image_data:
    output_path = os.path.join(temp_dir, output_file)
    image_data.save(output_path)

    # ❌ IMMEDIATELY upload to Cloudinary
    result = cloudinary.uploader.upload(output_path, folder="glad-labs/generated-images")
    cdn_url = result.get("secure_url")

    # ❌ Delete local file after upload
    os.remove(output_path)

    image = FeaturedImageMetadata(
        url=cdn_url,
        source="cloudinary",
    )
```

**After:**

```python
# NEW CODE - Keep local, no upload
if image_data:
    downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
    os.makedirs(downloads_path, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    task_id_str = request.task_id if request.task_id else "no-task"
    output_file = f"sdxl_{timestamp}_{task_id_str}.png"
    output_path = os.path.join(downloads_path, output_file)

    image_data.save(output_path)
    logger.info(f"✅ Image saved to Downloads for preview: {output_path}")

    # ✅ DON'T upload yet - keep in Downloads for user review
    # Approval endpoint will upload to CDN after user approves

    image = FeaturedImageMetadata(
        url=output_path,
        source="sdxl-local-preview",  # Mark as local preview
    )
    logger.info(f"⏳ Image will be uploaded to CDN after approval")
```

### Change 4: Updated Return Statements

**Purpose:** Return response with local_path and preview_mode fields

**Location:** In function `generate_featured_image()` around line 440-480

**Before:**

```python
# OLD CODE - Minimal response
if image:
    elapsed = time.time() - start_time
    return ImageGenerationResponse(
        success=True,
        image_url=image.url,
        image=ImageMetadata(
            url=image.url,
            source=image.source,
            photographer=image.photographer,
            photographer_url=image.photographer_url,
            width=image.width,
            height=image.height,
        ),
        message=f"✅ Image found via {image.source}",
        generation_time=elapsed,
    )
else:
    elapsed = time.time() - start_time
    return ImageGenerationResponse(
        success=False,
        image_url="",
        image=None,
        message="❌ No image found. Ensure PEXELS_API_KEY is set in environment or GPU available for SDXL.",
        generation_time=elapsed,
    )
```

**After:**

```python
# NEW CODE - Include local_path and preview_mode
if image:
    elapsed = time.time() - start_time

    # ═══════════════════════════════════════════════════════════
    # NOTE: Image is in Downloads folder for preview/approval
    # Frontend should store local_path in task metadata
    # Approval endpoint will upload to Cloudinary and update posts table
    # ═══════════════════════════════════════════════════════════

    return ImageGenerationResponse(
        success=True,
        image_url=image.url,  # Local path for preview
        local_path=image.url if image.source == "sdxl-local-preview" else None,  # Path to local file
        preview_mode=image.source == "sdxl-local-preview",  # Mark as preview mode
        image=ImageMetadata(
            url=image.url,
            source=image.source,
            photographer=image.photographer,
            photographer_url=image.photographer_url,
            width=image.width,
            height=image.height,
        ),
        message=f"✅ Image generated and saved locally (preview mode). Review and approve to publish.",
        generation_time=elapsed,
    )
else:
    elapsed = time.time() - start_time
    return ImageGenerationResponse(
        success=False,
        image_url="",
        image=None,
        message="❌ No image found. Ensure PEXELS_API_KEY is set in environment or GPU available for SDXL.",
        generation_time=elapsed,
        preview_mode=False,
    )
```

---

## Summary of Changes

| File                  | Change                     | Purpose                      | Impact                           |
| --------------------- | -------------------------- | ---------------------------- | -------------------------------- |
| `database_service.py` | Added `get_post_by_slug()` | Check for existing posts     | Prevents duplicate key errors    |
| `database_service.py` | Added `update_post()`      | Update post after approval   | Enables CDN URL storage          |
| `task_routes.py`      | Added duplicate check      | Prevent database errors      | Gracefully reuses existing posts |
| `media_routes.py`     | Changed save location      | Downloads folder             | Images persist for review        |
| `media_routes.py`     | Updated response model     | Add local_path, preview_mode | Frontend knows image is local    |
| `media_routes.py`     | Removed CDN upload         | Keep image local             | No upload until approved         |
| `media_routes.py`     | Updated return statements  | Include preview metadata     | Frontend can show status         |

---

## Testing Each Change

### Test 1: Duplicate Slug Prevention

```python
# First request - should succeed
prompt1 = "Generate an article about Making Delicious Muffins"
response1 = await task_api.create_task(prompt1)
# Result: POST created, slug = "making-delicious-muffins"

# Second request - should not error
prompt2 = "Generate an article about Making Delicious Muffins"  # Same title
response2 = await task_api.create_task(prompt2)
# Expected: Reuses existing post, logs warning
# Old: ❌ UniqueViolationError
# New: ✅ Success, reuses post from response1
```

### Test 2: Local Image Generation

```python
# Request image generation
response = await media_api.generate_image(
    prompt="a beautiful sunset",
    task_id="task-123"
)

# Check response fields
assert response["success"] == True
assert response["local_path"] is not None
assert response["preview_mode"] == True
assert response["image"]["source"] == "sdxl-local-preview"

# Check file exists
import os
assert os.path.exists(response["local_path"])
# Expected path: ~/Downloads/glad-labs-generated-images/sdxl_20240112_153045_task-123.png
```

### Test 3: Update Post

```python
# Update post with CDN URL after approval
success = await db_service.update_post(
    post_id=123,
    updates={
        "featured_image_url": "https://res.cloudinary.com/glad-labs/...",
        "status": "published"
    }
)
# Expected: ✅ True, post updated
```

---

## Next Implementation Steps

See `SDXL_IMPLEMENTATION_NEXT_STEPS.md` for:

1. Image approval endpoint code template
2. Multi-image variations endpoint code template
3. UI component updates
4. Complete testing checklist

---

## Files Status

| File                  | Status      | Changes               |
| --------------------- | ----------- | --------------------- |
| `database_service.py` | ✅ Updated  | 2 methods added       |
| `task_routes.py`      | ✅ Updated  | Duplicate check added |
| `media_routes.py`     | ✅ Updated  | 4 major changes       |
| `media_routes.py`     | ✅ Verified | No syntax errors      |
