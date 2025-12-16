# Image Generation Testing Guide

## Quick Start

### Prerequisites

✅ FastAPI backend running on port 8000
✅ Oversight Hub frontend running  
✅ Pexels API key configured (or disable Pexels to use SDXL only)

---

## Test Scenario 1: Generate Featured Image (Approval Panel)

### Steps:

1. **Open Oversight Hub** → http://localhost:3000
2. **Create a test task** (or find existing pending task)
3. **Click task to open** in approval panel
4. **Edit the title** (required for image generation)
5. **Find "Featured Image URL" section**
6. **Select image source:**
   - 🖼️ Pexels (Free, Fast) - 0.3-0.5 seconds
   - 🎨 SDXL (GPU-based) - 10-30 seconds
   - 🔄 Try Both (Pexels first, fallback to SDXL) - Recommended

### Expected Results:

**If using Pexels (Fast):**

```
✅ Image from pexels in 0.45s
```

- URL should populate immediately
- Image should load without CORS errors

**If using SDXL (Slow):**

```
✅ Image from sdxl in 12.34s
```

- Loading animation spins
- URL populates after GPU generation completes

**If error occurs:**

```
❌ Failed: Image generation failed: 404 Not Found
❌ Failed: PEXELS_API_KEY not configured
❌ Failed: GPU unavailable
```

---

## Test Scenario 2: Create Image Generation Task

### Steps:

1. **Click "Create New Task"** button
2. **Select "🖼️ Image Generation"** from task type list
3. **Fill the form:**
   - Image Description: "A serene mountain landscape at sunrise"
   - Number of Images: 1
   - Style: "realistic"
   - Resolution: "1024x1024"
4. **Click "Create Task"** button
5. **Wait for generation** (status bar shows progress)

### Expected Results:

**Success:**

```
✅ Task created successfully
```

- Task appears in task list
- Status shows "completed" (not pending)
- Task metadata contains generated image

**Failure:**

```
Failed to create task: Image generation failed: [error]
```

- Modal shows error message
- Can retry after fixing issue

### Verify Task Metadata:

1. Click on created task
2. Open browser DevTools → Console
3. Look for task object with:
   ```javascript
   metadata: {
     image_url: "https://...",
     image_source: "pexels" | "sdxl",
     generation_time: 0.45,
     status: "completed"
   }
   ```

---

## Test Scenario 3: Try Different Sources

### Test Matrix:

| Source | Expected Behavior                  | Time     |
| ------ | ---------------------------------- | -------- |
| Pexels | Fast, might not match exactly      | <1s      |
| SDXL   | Slower, more creative              | 10-30s   |
| Both   | Try Pexels first, fallback to SDXL | ~0.5-30s |

### Test Prompts:

1. "A modern office with large windows"
2. "Abstract watercolor painting"
3. "Cute cartoon character"
4. "Futuristic technology"
5. "Nature landscape with forest"

---

## Debugging Guide

### Issue: "Image generation failed: 404 Not Found"

**Causes:**

- FastAPI backend not running
- CORS configuration issue
- Wrong endpoint URL

**Fix:**

1. Check backend is running: `python main.py` in `src/cofounder_agent/`
2. Verify port 8000 is accessible
3. Check browser console for CORS errors
4. Verify endpoint URL: http://localhost:8000/api/media/generate-image

### Issue: "PEXELS_API_KEY not configured"

**Causes:**

- Missing environment variable
- Empty API key

**Fix:**

1. Set PEXELS_API_KEY in `.env` or environment
2. Restart FastAPI backend
3. Or try "SDXL" source instead (doesn't need API key)

### Issue: "GPU unavailable"

**Causes:**

- SDXL requires GPU
- No CUDA available
- Model not downloaded

**Fix:**

1. Use Pexels source instead
2. Or install GPU drivers and CUDA
3. Check backend logs for model download status

### Issue: Images not loading in UI

**Causes:**

- Image URL is invalid
- CORS headers missing
- Image URL protocol is HTTP in HTTPS environment

**Fix:**

1. Check image URL in browser console
2. Try opening URL directly in new tab
3. Check FastAPI CORS configuration

---

## Backend Testing (curl)

### Test Pexels Generation:

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "mountain landscape",
    "title": "Mountain",
    "use_pexels": true,
    "use_generation": false
  }'
```

**Expected Response:**

```json
{
  "success": true,
  "image_url": "https://images.pexels.com/...",
  "image": {
    "source": "pexels",
    "photographer": "...",
    "attribution_url": "..."
  },
  "generation_time": 0.45
}
```

### Test SDXL Generation:

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "abstract art",
    "title": "Abstract",
    "use_pexels": false,
    "use_generation": true
  }'
```

**Expected Response:**

```json
{
  "success": true,
  "image_url": "data:image/png;base64,...",
  "image": {
    "source": "sdxl",
    "model": "stabilityai/stable-diffusion-xl-base-1.0"
  },
  "generation_time": 15.32
}
```

### Test Both (Fallback):

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "nature",
    "title": "Nature",
    "use_pexels": true,
    "use_generation": true
  }'
```

---

## Performance Metrics

### Pexels (Should be <1 second)

- Network: API call to pexels.com
- No GPU needed
- Real stock photos
- May not match prompt exactly
- Includes photographer attribution

### SDXL (Should be 10-30 seconds)

- GPU required (NVIDIA CUDA)
- Generative AI model
- Creates new image based on prompt
- More creative matches
- Requires model downloaded locally

### Typical Times:

- Pexels: 0.3-0.8 seconds
- SDXL: 10-30 seconds (first run), 5-15s (cached)
- Both (Pexels succeeds): ~0.5s
- Both (Pexels fails, SDXL): ~15s

---

## Success Checklist

- [ ] Can generate images from approval panel
- [ ] Image source dropdown appears
- [ ] Status messages display with color coding
- [ ] Pexels images load quickly
- [ ] SDXL images generate (or graceful error if no GPU)
- [ ] Featured image URL auto-populates
- [ ] Can create image generation tasks
- [ ] Task metadata contains image results
- [ ] Error messages are helpful
- [ ] Can retry generation on failure
- [ ] Images display correctly in UI

---

## Rollback Steps (If Needed)

### Revert CreateTaskModal.jsx:

Remove the image_generation handler and revert to generic task creation.

### Revert ResultPreviewPanel.jsx:

Remove imageSource and imageGenerationMessage state, revert generateFeaturedImage to original.

---

## Support

**Check logs:**

- Backend logs: `src/cofounder_agent/logs/`
- Browser console: DevTools → Console tab
- Network tab: DevTools → Network tab

**Common issues:**

- Missing API keys → Check `.env` file
- Port conflicts → Kill existing processes
- Module not found → Run `pip install -r requirements.txt`
- CORS errors → Check FastAPI middleware configuration
