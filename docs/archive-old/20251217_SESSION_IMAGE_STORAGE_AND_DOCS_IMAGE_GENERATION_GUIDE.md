# Image Generation Integration Guide

## Overview

The image generation system is now integrated with FastAPI and ready to use. It provides:

- **Pexels API Integration** (Free, unlimited stock images)
- **Stable Diffusion XL** (GPU-based custom generation)
- **Async-first Architecture** (Non-blocking I/O for FastAPI)
- **Health Check Endpoint** (Monitor service status)

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────┐
│ Frontend (Oversight Hub React)                              │
│ - ResultPreviewPanel.jsx calls POST /api/media/generate-image│
└─────────────────┬───────────────────────────────────────────┘
                  │
                  │ HTTP POST with prompt
                  │
┌─────────────────▼───────────────────────────────────────────┐
│ FastAPI Backend (Port 8000)                                 │
│ - POST /api/media/generate-image                            │
│ - GET /api/media/images/search                              │
│ - GET /api/media/health                                     │
└─────────────────┬───────────────────────────────────────────┘
                  │
          ┌───────┴───────┐
          │               │
    ┌─────▼────┐    ┌────▼──────┐
    │  Pexels  │    │    SDXL    │
    │   API    │    │   (GPU)    │
    │  (FREE)  │    │   (FREE)   │
    └──────────┘    └───────────┘
```

### Key Files

| File                                                            | Purpose               | Status               |
| --------------------------------------------------------------- | --------------------- | -------------------- |
| `src/cofounder_agent/services/image_service.py`                 | Unified image service | ✅ Fully implemented |
| `src/cofounder_agent/routes/media_routes.py`                    | API endpoints         | ✅ Created           |
| `src/cofounder_agent/utils/route_registration.py`               | Route registration    | ✅ Updated           |
| `web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx` | Frontend integration  | ✅ Updated           |
| `test_media_endpoints.py`                                       | Test suite            | ✅ Created           |

## Environment Setup

### Step 1: Configure Pexels API Key

Get a free API key from [Pexels](https://www.pexels.com/api/):

1. Visit https://www.pexels.com/api/
2. Sign up for a free account
3. Click "Generate key"
4. Add to your `.env.local` file:

```bash
PEXELS_API_KEY=your_api_key_here
```

**Cost**: FREE (unlimited images)

### Step 2: Verify SDXL (Optional)

If you have a CUDA GPU:

1. SDXL will be automatically detected
2. No additional setup needed
3. Check via health endpoint: `GET /api/media/health`

**Cost**: FREE if GPU available (gracefully skipped otherwise)

### Step 3: Test Setup

```bash
# Run the test suite
python test_media_endpoints.py
```

Expected output:

```
✅ Health Check Passed
  Status: healthy
  Pexels: True
  SDXL: True/False (depends on GPU)

✅ Image Search Passed
  Success: true
  Image URL: https://images.pexels.com/...

✅ Image Generation Passed
  Success: true
  Image URL: https://images.pexels.com/...
  Time: 0.45s
```

## API Endpoints

### 1. POST /api/media/generate-image

Generate or search for a featured image.

**Request:**

```json
{
  "prompt": "AI gaming NPCs futuristic virtual reality",
  "title": "How AI-Powered NPCs are Making Games More Immersive",
  "keywords": ["gaming", "AI", "NPCs"],
  "use_pexels": true,
  "use_generation": false
}
```

**Response:**

```json
{
  "success": true,
  "image_url": "https://images.pexels.com/...",
  "image": {
    "url": "https://images.pexels.com/...",
    "source": "pexels",
    "photographer": "John Smith",
    "photographer_url": "...",
    "width": 1200,
    "height": 630
  },
  "message": "✅ Image found via pexels",
  "generation_time": 0.45
}
```

**Parameters:**

- `prompt` (required): Image search/generation prompt
- `title` (optional): Blog post title (used as fallback)
- `keywords` (optional): Additional search terms (max 5)
- `use_pexels` (default: true): Try Pexels search first
- `use_generation` (default: false): Fall back to SDXL if GPU available

**Strategy:**

1. If `use_pexels=true`: Search Pexels for free stock image
2. If not found AND `use_generation=true`: Generate custom image with SDXL
3. Return image URL

**Cost:**

- Pexels: FREE (unlimited)
- SDXL: FREE if GPU available
- vs DALL-E: $0.02/image

### 2. GET /api/media/images/search

Search-only endpoint for finding images.

**Query Parameters:**

- `query` (required): Search query (e.g., "AI gaming")
- `count` (default: 1): Number of images to return (1-20)

**Example:**

```bash
curl "http://localhost:8000/api/media/images/search?query=AI%20gaming&count=1"
```

**Response:**
Same as POST endpoint but search-only.

### 3. GET /api/media/health

Check image service health.

**Response:**

```json
{
  "status": "healthy",
  "pexels_available": true,
  "sdxl_available": true,
  "message": "✅ Pexels API available | ✅ SDXL GPU available"
}
```

**Status Values:**

- `healthy`: All services available
- `degraded`: Some services unavailable
- `error`: Critical error

## Frontend Integration

### ResultPreviewPanel.jsx

The "Generate Featured Image" button automatically:

1. Takes the article title
2. Calls `POST /api/media/generate-image`
3. Updates the featured image URL field
4. Shows success/error messages

**Button Behavior:**

```jsx
// User clicks "Generate Featured Image"
// → Sends POST request with article title
// → Pexels search returns image in ~0.5s
// → Image URL populates the form field
// → User can preview and approve
```

### JavaScript Code

```javascript
// In ResultPreviewPanel.jsx
const generateFeaturedImage = async () => {
  const response = await fetch(
    'http://localhost:8000/api/media/generate-image',
    {
      method: 'POST',
      body: JSON.stringify({
        prompt: editedTitle,
        title: editedTitle,
        use_pexels: true,
        use_generation: false,
      }),
    }
  );

  const result = await response.json();
  if (result.success) {
    setFeaturedImageUrl(result.image_url);
  }
};
```

## Usage Examples

### Example 1: Generate Image for Blog Post

**Frontend:**

1. User enters article title: "How AI-Powered NPCs are Making Games More Immersive"
2. User clicks "Generate Featured Image" button
3. System searches Pexels and returns image in ~0.5 seconds
4. Image URL auto-populates in the form
5. User clicks "Approve" to save post

**API Call:**

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "How AI-Powered NPCs are Making Games More Immersive",
    "title": "How AI-Powered NPCs are Making Games More Immersive",
    "use_pexels": true,
    "use_generation": false
  }'
```

### Example 2: Custom Generation (GPU Required)

If you have a CUDA GPU and want custom images:

```bash
curl -X POST http://localhost:8000/api/media/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "futuristic AI NPCs in gaming, cyberpunk style, high quality",
    "use_pexels": false,
    "use_generation": true
  }'
```

### Example 3: Batch Generate for All Posts

```bash
#!/bin/bash

# Get list of posts without featured images
posts=$(curl -s http://localhost:8000/api/content/posts?has_image=false)

# Generate image for each post
for post in $posts; do
  title=$(echo $post | jq -r '.title')
  post_id=$(echo $post | jq -r '.id')

  curl -X POST http://localhost:8000/api/media/generate-image \
    -H "Content-Type: application/json" \
    -d "{
      \"prompt\": \"$title\",
      \"post_id\": \"$post_id\",
      \"use_pexels\": true
    }"
done
```

## Troubleshooting

### Issue: "PEXELS_API_KEY not set"

**Solution:**

```bash
# Add to .env.local
PEXELS_API_KEY=your_key_here

# Restart FastAPI server
```

### Issue: "Image generation failed"

**Check:**

```bash
# Test health endpoint
curl http://localhost:8000/api/media/health

# Expected output if working:
# {
#   "status": "healthy",
#   "pexels_available": true,
#   "message": "✅ Pexels API available"
# }
```

### Issue: SDXL Not Available

**This is normal if:**

- You don't have a CUDA GPU
- NVIDIA drivers not installed

**Status:**

- Pexels will still work (fall back strategy)
- System logs: "SDXL not available (requires CUDA GPU)"

### Issue: Slow Image Generation

**Expected timings:**

- Pexels search: 0.3-0.5 seconds
- SDXL generation: 10-30 seconds (depends on GPU)

**Optimization:**

- Use `use_pexels: true` (recommended, faster)
- Use `use_generation: false` (unless you need custom)

## Database Integration (Optional)

The endpoint can optionally update the database:

```json
{
  "prompt": "AI gaming NPCs",
  "post_id": "post-123",
  "use_pexels": true
}
```

This will update the `posts` table with the image URL.

**Note:** Currently manual via SQL or API updates. Auto-update feature can be added if needed.

## Performance Notes

### Cost Comparison

| Service  | Cost       | Speed  | Quality   |
| -------- | ---------- | ------ | --------- |
| Pexels   | FREE       | ~0.5s  | Very High |
| SDXL     | FREE (GPU) | 10-30s | High      |
| DALL-E 3 | $0.02/img  | ~5s    | Very High |

### Architecture Benefits

✅ **Async-first**: Non-blocking I/O (doesn't hang FastAPI)
✅ **Graceful Fallback**: Works with or without GPU
✅ **Free**: Unlimited Pexels + free SDXL if GPU available
✅ **Fast**: Pexels search in 0.5 seconds
✅ **Scalable**: Can handle multiple simultaneous requests

## Next Steps

1. ✅ Set `PEXELS_API_KEY` in `.env.local`
2. ✅ Run `python test_media_endpoints.py` to verify setup
3. ✅ Test in Oversight Hub: Click "Generate Featured Image" button
4. ✅ For all 8 posts: Either click button in each or run batch script
5. ⏳ (Optional) Upload SDXL-generated images to CDN for production

## Files Modified

### Backend

- ✅ `src/cofounder_agent/routes/media_routes.py` (Created)
- ✅ `src/cofounder_agent/utils/route_registration.py` (Updated)

### Frontend

- ✅ `web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx` (Updated)

### Testing

- ✅ `test_media_endpoints.py` (Created)

## References

- [Pexels API Documentation](https://www.pexels.com/api/documentation/)
- [Stable Diffusion XL](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
