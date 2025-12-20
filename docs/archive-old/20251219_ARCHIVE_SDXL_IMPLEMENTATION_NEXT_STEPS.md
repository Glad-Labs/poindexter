# SDXL Image Generation & Approval Workflow - Implementation Next Steps

## Current Status ✅

### Issues FIXED

1. ✅ **Duplicate Slug Error** - Added check before post creation
2. ✅ **Image Local Storage** - Changed to Downloads folder with persistent files
3. ✅ **Response Model** - Updated to include `local_path` and `preview_mode` fields

### Code Changes Made

**Files Modified:**

- `database_service.py` - Added `get_post_by_slug()` method
- `task_routes.py` - Added duplicate checking before post creation (lines ~610-650)
- `media_routes.py` -
  - Changed image save location (lines ~360-390)
  - Updated response model (lines 257-268)
  - Removed immediate CDN upload (lines ~395-445)
  - Updated return statements with `local_path` and `preview_mode` (lines ~440-480)

---

## Critical Context

### Image Generation Flow (CURRENT)

```
generate_featured_image(prompt, task_id)
  ↓
  Save to: ~/Downloads/glad-labs-generated-images/sdxl_{timestamp}_{task_id}.png
  ↓
  Return: ImageGenerationResponse with:
    - image_url = local_path
    - local_path = "/Users/.../sdxl_20240112_153045_task123.png"
    - preview_mode = True
    - source = "sdxl-local-preview"
  ↓
  Frontend stores in task metadata (NOT uploaded to CDN yet)
```

### Image Approval Flow (NEEDS IMPLEMENTATION)

```
user_clicks_approve(task_id, local_image_path)
  ↓
  POST /api/media/approve-image
  ↓
  1. Read image from local_image_path
  2. Upload to Cloudinary/S3
  3. Get CDN URL back
  4. Update posts table with CDN URL
  5. Update task status to "published"
  6. (Optional) Delete local file
  ↓
  Response: { success: True, cdn_url: "https://..." }
```

---

## IMMEDIATE NEXT STEPS (Priority Order)

### Step 1: Test Current Changes ⚙️

**What to test:**

```bash
# Terminal 1: Start backend
cd src/cofounder_agent
python main.py

# Terminal 2: Test in browser
# Navigate to Oversight Hub
# Create a task that generates an image
# Check if image appears in ~/Downloads/glad-labs-generated-images/
# Verify response includes local_path field
```

**Expected Results:**

- ✅ Image file created: `~/Downloads/glad-labs-generated-images/sdxl_*.png`
- ✅ Response includes: `"local_path": "/full/path/to/image.png"`
- ✅ Response includes: `"preview_mode": true`
- ✅ No UniqueViolationError if task retried with same content

---

### Step 2: Create Image Approval Endpoint 📤

**Location:** `src/cofounder_agent/routes/media_routes.py`

**Add this endpoint after `generate_featured_image`:**

```python
@media_router.post("/approve-image")
async def approve_image(request: ApproveImageRequest, db_service: DatabaseService = Depends()):
    """
    Approve and upload a locally-generated image to CDN.
    - Reads image from local_path (Downloads folder)
    - Uploads to Cloudinary/S3
    - Stores CDN URL in posts table
    - Marks task as published
    """
    try:
        logger.info(f"📤 Approving image for post_id={request.post_id}")

        # Validate local file exists
        local_path = Path(request.local_path)
        if not local_path.exists():
            return {
                "success": False,
                "error": f"Local image not found: {request.local_path}",
                "cdn_url": None,
            }

        # Upload to Cloudinary
        with open(local_path, "rb") as image_file:
            result = cloudinary.uploader.upload(
                image_file,
                folder="glad-labs/generated-images",
                resource_type="auto",
                use_filename=True,
                unique_filename=True,
            )

        cdn_url = result.get("secure_url")
        if not cdn_url:
            return {
                "success": False,
                "error": "Cloudinary upload failed",
                "cdn_url": None,
            }

        logger.info(f"✅ Uploaded to CDN: {cdn_url}")

        # Update posts table with CDN URL
        await db_service.update_post(
            post_id=request.post_id,
            updates={
                "featured_image_url": cdn_url,
                "status": "published",
            }
        )

        # Optional: Delete local file after successful upload
        if request.cleanup_local:
            try:
                local_path.unlink()
                logger.info(f"🧹 Cleaned up local file: {request.local_path}")
            except Exception as e:
                logger.warning(f"Failed to delete local file: {e}")

        return {
            "success": True,
            "cdn_url": cdn_url,
            "message": f"✅ Image approved and uploaded to CDN",
        }

    except Exception as e:
        logger.error(f"Error approving image: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "cdn_url": None,
        }


# Add this to pydantic models section (near top of file)
class ApproveImageRequest(BaseModel):
    post_id: int = Field(..., description="ID of post to update")
    local_path: str = Field(..., description="Path to local image file (~/Downloads/...)")
    cleanup_local: bool = Field(False, description="Delete local file after upload")
```

---

### Step 3: Add Multi-Image Generation Endpoint 🖼️

**Location:** `src/cofounder_agent/routes/media_routes.py`

**Add this endpoint after `generate_featured_image`:**

```python
@media_router.post("/generate-image-variations")
async def generate_image_variations(request: GenerateImageVariationsRequest):
    """
    Generate multiple SDXL image variations for comparison.
    - Generates N images with same prompt
    - Saves all to ~/Downloads/glad-labs-generated-images/
    - Returns list of local paths
    - Frontend presents selector for user to choose best one
    """
    try:
        task_id = request.task_id or "no-task"
        num_variations = min(request.num_variations, 5)  # Max 5 to avoid GPU overload

        logger.info(f"🖼️ Generating {num_variations} image variations for task_id={task_id}")

        # Create downloads directory
        downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
        os.makedirs(downloads_path, exist_ok=True)

        start_time = time.time()
        generated_images = []

        # Generate each variation
        for i in range(num_variations):
            try:
                logger.info(f"  Generating variation {i+1}/{num_variations}...")

                # Generate image via SDXL
                image_data = generate_sdxl_image(request.prompt)

                if image_data:
                    # Save to Downloads
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    variation_num = i + 1
                    filename = f"sdxl_{timestamp}_{task_id}_var{variation_num}.png"
                    filepath = os.path.join(downloads_path, filename)

                    image_data.save(filepath)
                    logger.info(f"  ✅ Saved: {filepath}")

                    generated_images.append({
                        "path": filepath,
                        "filename": filename,
                        "variation_number": variation_num,
                        "generated_at": datetime.now().isoformat(),
                    })

            except Exception as e:
                logger.error(f"  Error generating variation {i+1}: {e}")
                continue

        elapsed = time.time() - start_time

        if not generated_images:
            return {
                "success": False,
                "images": [],
                "message": "❌ Failed to generate any variations",
                "elapsed": elapsed,
            }

        logger.info(f"✅ Generated {len(generated_images)} variations in {elapsed:.1f}s")

        return {
            "success": True,
            "images": generated_images,
            "message": f"✅ Generated {len(generated_images)} variations. Choose your favorite.",
            "elapsed": elapsed,
            "total_generated": len(generated_images),
        }

    except Exception as e:
        logger.error(f"Error generating variations: {e}", exc_info=True)
        return {
            "success": False,
            "images": [],
            "error": str(e),
            "elapsed": 0,
        }


# Add this to pydantic models section
class GenerateImageVariationsRequest(BaseModel):
    prompt: str = Field(..., description="Image prompt for SDXL")
    task_id: Optional[str] = Field(None, description="Task ID for file naming")
    num_variations: int = Field(3, ge=1, le=5, description="Number of variations (1-5)")
```

---

### Step 4: Update Task Routes to Store Image Metadata 💾

**Location:** `src/cofounder_agent/routes/task_routes.py`

**Modify the response to include image approval info:**

```python
# In _execute_and_publish_task, after image generation section:

if image_result.get("success"):
    # Store image info in response for frontend
    response["featured_image"] = {
        "local_path": image_result.get("local_path"),
        "preview_mode": image_result.get("preview_mode"),
        "image_url": image_result.get("image_url"),
        "source": "sdxl-local-preview",  # Indicates needs approval
        "awaiting_approval": True,
    }

    logger.info(f"Image stored locally for approval: {image_result.get('local_path')}")
```

---

### Step 5: Create Approval UI in Oversight Hub 🎨

**Location:** `web/oversight-hub/src/components/TaskDetail.tsx` or similar

**Add image preview section:**

```tsx
{
  /* Image Preview & Approval Section */
}
{
  task.featured_image?.preview_mode && (
    <Box sx={{ mt: 3, p: 2, border: '2px solid #FFA500', borderRadius: 1 }}>
      <Typography variant="h6">🖼️ Generated Image (Preview)</Typography>

      <Box sx={{ my: 2, border: '1px solid #ccc', borderRadius: 1, p: 1 }}>
        <img
          src={`file://${task.featured_image.local_path}`}
          alt="Generated preview"
          style={{ maxWidth: '100%', borderRadius: 4 }}
        />
      </Box>

      <Box sx={{ display: 'flex', gap: 2 }}>
        {/* Regenerate Button */}
        <Button
          variant="outlined"
          onClick={() => handleRegenerateImage(task.id)}
        >
          🔄 Regenerate Image
        </Button>

        {/* Approve Button */}
        <Button
          variant="contained"
          color="success"
          onClick={() =>
            handleApproveImage(task.id, task.featured_image.local_path)
          }
        >
          ✅ Approve & Publish
        </Button>
      </Box>
    </Box>
  );
}
```

**Add approval function:**

```tsx
async function handleApproveImage(taskId: string, localPath: string) {
  try {
    const response = await fetch('/api/media/approve-image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        post_id: taskId,
        local_path: localPath,
        cleanup_local: true, // Delete local file after upload
      }),
    });

    const result = await response.json();

    if (result.success) {
      toast.success('✅ Image approved and uploaded!');
      // Update task state, mark as published
    } else {
      toast.error(`Failed to approve: ${result.error}`);
    }
  } catch (error) {
    toast.error(`Error: ${error.message}`);
  }
}
```

---

### Step 6: Testing Checklist ✅

**Basic Image Generation:**

- [ ] Image saved to `~/Downloads/glad-labs-generated-images/`
- [ ] Filename includes timestamp and task_id
- [ ] Response includes `local_path` field
- [ ] Response includes `preview_mode: true`
- [ ] Image file is readable (PNG format)

**Duplicate Slug Prevention:**

- [ ] Generate task with title "Making Delicious Muffins"
- [ ] Generate same task again - should not error
- [ ] Second attempt reuses existing post

**Multi-Image Generation:**

- [ ] Call `/api/media/generate-image-variations?num_variations=3`
- [ ] All 3 images saved to Downloads with `_var1`, `_var2`, `_var3` suffixes
- [ ] Response includes all 3 paths
- [ ] Takes ~60-90 seconds (3 × 20-30s each)

**Image Approval:**

- [ ] POST `/api/media/approve-image` with local_path
- [ ] Image uploaded to Cloudinary
- [ ] Response includes CDN URL
- [ ] Post table updated with CDN URL
- [ ] Status changed to "published"
- [ ] Local file deleted (if cleanup_local=true)

**UI Integration:**

- [ ] Image preview shows in Oversight Hub
- [ ] Regenerate button generates new image
- [ ] Approve button uploads to CDN
- [ ] Task shows as published after approval

---

## File Structure After Implementation

```
~/Downloads/
  └─ glad-labs-generated-images/
     ├─ sdxl_20240112_153045_task123.png          ← Initial image
     ├─ sdxl_20240112_153100_task123_var1.png     ← Variation 1
     ├─ sdxl_20240112_153130_task123_var2.png     ← Variation 2
     ├─ sdxl_20240112_153200_task123_var3.png     ← Variation 3
     └─ [etc...]

// After approval: Local file deleted, CDN URL stored in posts table
// posts.featured_image_url = "https://res.cloudinary.com/glad-labs/..."
```

---

## Code Dependencies

### What's Already Available

- ✅ `cloudinary.uploader.upload()` - configured in environment
- ✅ `DatabaseService` with `create_post()`, `update_post()` methods
- ✅ `generate_sdxl_image()` function for SDXL generation
- ✅ Error handling middleware in FastAPI
- ✅ Logging infrastructure

### What Needs Verification

- Check if `update_post()` method exists in DatabaseService
- Check if Cloudinary credentials set in environment
- Verify SDXL GPU availability on Railway

---

## Expected Timeline

**Testing Current Code:** 5 minutes
**Create Approval Endpoint:** 15 minutes  
**Create Variations Endpoint:** 15 minutes
**Update UI Components:** 20 minutes
**End-to-End Testing:** 15 minutes

**Total:** ~70 minutes for full implementation

---

## Critical Notes

### Performance Considerations

- Multi-image generation takes 60-90 seconds (3 images × 20-30s each)
- Consider showing progress bar in UI
- Generate in background if possible
- Don't block UI during generation

### Storage Considerations

- Each image ~2-5 MB
- Downloads folder could accumulate files
- Implement cleanup strategy (delete after 7 days if not approved)
- Consider maximum images to prevent disk bloat

### Database Considerations

- Need `update_post()` method if it doesn't exist
- Should handle partial failures (image generated but upload fails)
- Transaction should either fully succeed or fully rollback

---

## Next Session Action Plan

If tests pass ✅:

1. Implement `/api/media/approve-image` endpoint
2. Implement `/api/media/generate-image-variations` endpoint
3. Add UI controls in Oversight Hub
4. Run full end-to-end test

If tests fail ❌:

1. Debug issue with local image generation
2. Check if image file is being created
3. Verify response model has local_path
4. Check permissions on Downloads folder
5. Iterate until working

---

**Status:** 🚀 Ready for testing
**Next Step:** Run terminal tests to verify changes
