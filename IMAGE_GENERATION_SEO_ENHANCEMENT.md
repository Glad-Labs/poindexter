# Image Generation SEO Enhancement - Implementation Guide

## Overview

Enhanced the image generation system to automatically extract and use SEO keywords from blog post metadata to create more targeted, specific image generation prompts. This improvement increases the relevance of generated or found images.

---

## Problem Statement

**Before:** Image generation used only the title as the search/generation prompt:
```
Image Title: "Best Eats in the Northeast USA: A Culinary Guide"
→ Search Pexels for: "Best Eats in the Northeast USA: A Culinary Guide"
→ Generate SDXL for: "Best Eats in the Northeast USA: A Culinary Guide"
```

**Result:** Generic, broad images that don't capture the specific focus of the content

**After:** Image generation now combines title + SEO keywords:
```
Image Title: "Best Eats in the Northeast USA: A Culinary Guide"
SEO Keywords: ["seafood", "boston", "food", "restaurant", "culinary"]
→ Search Pexels for: "Best Eats in the Northeast USA seafood"  ← Much more specific!
→ Generate SDXL for: "Best Eats in the Northeast USA seafood"   ← Better focused
```

**Result:** Highly relevant, specific images that match content focus

---

## Changes Made

### 1. Frontend: Extract and Send Keywords

**File:** [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx#L63-L100)

**Change:** Enhanced `generateFeaturedImage()` function to extract SEO keywords from metadata

**Before:**
```javascript
const requestPayload = {
  prompt: editedTitle,
  title: editedTitle,
  use_pexels: usePexels,
  use_generation: useSDXL,
};
```

**After:**
```javascript
// Extract keywords from SEO metadata if available
let keywords = [];
if (editedSEO?.keywords) {
  // Handle both string and array formats
  if (typeof editedSEO.keywords === 'string') {
    keywords = editedSEO.keywords
      .split(',')
      .map((kw) => kw.trim())
      .filter((kw) => kw.length > 0)
      .slice(0, 5); // Limit to top 5 keywords
  } else if (Array.isArray(editedSEO.keywords)) {
    keywords = editedSEO.keywords
      .slice(0, 5)
      .map((kw) => String(kw).trim());
  }
}

const requestPayload = {
  prompt: editedTitle,
  title: editedTitle,
  keywords: keywords.length > 0 ? keywords : undefined,
  use_pexels: usePexels,
  use_generation: useSDXL,
};
```

**Why:**
- Extracts keywords from `editedSEO.keywords` (set by ResultPreviewPanel from task metadata)
- Handles both comma-separated strings (e.g., "seafood, boston, food") and arrays
- Limits to top 5 keywords to avoid noise
- Sends to backend via `keywords` field in request

---

### 2. Backend: Create Prompt Enhancement Helper

**File:** [src/cofounder_agent/routes/media_routes.py](src/cofounder_agent/routes/media_routes.py#L313-L347)

**Added Function:** `build_enhanced_search_prompt()`

```python
def build_enhanced_search_prompt(
    base_prompt: str,
    keywords: Optional[List[str]] = None,
) -> str:
    """
    Build an enhanced search prompt by combining title with SEO keywords.
    
    This creates more specific, targeted search queries that are more likely
    to find relevant images.
    
    Examples:
        >>> build_enhanced_search_prompt("Best Eats in Northeast USA", ["seafood", "boston", "food"])
        "Best Eats in Northeast USA seafood"
        
        >>> build_enhanced_search_prompt("AI Gaming NPCs")
        "AI Gaming NPCs"
    """
    if not keywords or len(keywords) == 0:
        return base_prompt
    
    # Take top keyword for specificity
    primary_keyword = keywords[0] if keywords else None
    
    if not primary_keyword:
        return base_prompt
    
    # Combine title with primary keyword for more specific search
    enhanced = f"{base_prompt} {primary_keyword}"
    
    logger.debug(f"📝 Enhanced prompt: '{base_prompt}' → '{enhanced}' (using keyword: {primary_keyword})")
    
    return enhanced
```

**Why:**
- Takes the first (most important) keyword from the SEO list
- Appends it to the title to create a more specific search query
- Falls back gracefully if no keywords provided
- Logs the enhancement for debugging

---

### 3. Backend: Use Enhanced Prompts in Image Generation

**File:** [src/cofounder_agent/routes/media_routes.py](src/cofounder_agent/routes/media_routes.py#L408-L442)

**Updated:** Image generation endpoint to use enhanced prompts

**Pexels Search (STEP 1):**
```python
if request.use_pexels:
    keywords = request.keywords or []
    
    # Build enhanced search prompt using keywords if available
    search_prompt = build_enhanced_search_prompt(request.prompt, keywords)
    
    logger.info(f"🔍 STEP 1: Searching Pexels for: {search_prompt}")
    if keywords:
        logger.debug(f"   Keywords: {', '.join(keywords)}")
    
    try:
        image = await image_service.search_featured_image(
            topic=search_prompt,  # ← Uses enhanced prompt!
            keywords=keywords
        )
        # ... rest of logic
```

**SDXL Generation (STEP 2):**
```python
if not image and request.use_generation:
    keywords = request.keywords or []
    
    # Build enhanced generation prompt using keywords if available
    generation_prompt = build_enhanced_search_prompt(request.prompt, keywords)
    
    logger.info(f"🎨 STEP 2: Generating image with SDXL: {generation_prompt}")
    if keywords:
        logger.debug(f"   Keywords: {', '.join(keywords)}")
    
    # ... file path setup ...
    
    success = await image_service.generate_image(
        prompt=generation_prompt,  # ← Uses enhanced prompt!
        output_path=output_path,
        # ... other parameters
    )
```

**Why:**
- Uses enhanced prompt for both Pexels search and SDXL generation
- Logs keywords being used for transparency
- Provides better context for image service
- Maintains backwards compatibility (works fine without keywords)

---

## How It Works: Step-by-Step

### 1. User generates content with AI
```
Input: "Write about best restaurants in Northeast USA"
Output:
  - Title: "Best Eats in the Northeast USA: A Culinary Guide"
  - Content: "Boston is renowned for... New York City is a foodie's dream..."
  - SEO Keywords: ["seafood", "boston", "restaurants", "food", "dining"]
```

### 2. User clicks "Generate" for featured image
```
Oversight Hub reads:
  - editedTitle = "Best Eats in the Northeast USA: A Culinary Guide"
  - editedSEO.keywords = "seafood, boston, restaurants, food, dining"
```

### 3. Frontend extracts keywords and sends to backend
```javascript
keywords = ["seafood", "boston", "restaurants", "food", "dining"]

fetch POST /api/media/generate-image {
  prompt: "Best Eats in the Northeast USA: A Culinary Guide",
  keywords: ["seafood", "boston", "restaurants", "food", "dining"],
  use_pexels: true,
  use_generation: false
}
```

### 4. Backend enhances prompt
```python
base_prompt = "Best Eats in the Northeast USA: A Culinary Guide"
keywords = ["seafood", "boston", "restaurants", "food", "dining"]
enhanced = "Best Eats in the Northeast USA: A Culinary Guide seafood"
```

### 5. Pexels search with enhanced prompt
```
Search Query: "Best Eats in the Northeast USA: A Culinary Guide seafood"
Result: High-quality seafood restaurant images ✅
```

### 6. Return to user
```
Image from Pexels showing fresh seafood, coastal restaurants, 
market scenes - highly relevant to the content!
```

---

## API Request Changes

### Before:
```json
{
  "prompt": "Best Eats in the Northeast USA: A Culinary Guide",
  "title": "Best Eats in the Northeast USA: A Culinary Guide",
  "use_pexels": true,
  "use_generation": false
}
```

### After:
```json
{
  "prompt": "Best Eats in the Northeast USA: A Culinary Guide",
  "title": "Best Eats in the Northeast USA: A Culinary Guide",
  "keywords": ["seafood", "boston", "restaurants", "food", "dining"],
  "use_pexels": true,
  "use_generation": false
}
```

**Backwards Compatible:** ✅ Keywords are optional (defaults to None)

---

## Logging & Debugging

### Frontend Logs:
```
📸 Generating image with: {
  prompt: "Best Eats in the Northeast USA",
  keywords: ["seafood", "boston", "restaurants"],
  use_pexels: true,
  use_generation: false
}
```

### Backend Logs:
```
🔍 STEP 1: Searching Pexels for: Best Eats in the Northeast USA seafood
   Keywords: seafood, boston, restaurants
✅ STEP 1 SUCCESS: Found image via Pexels: https://images.pexels.com/...
```

---

## Benefits

### 1. **More Relevant Images**
   - Before: Generic images for broad title
   - After: Specific images matching content focus with keywords

### 2. **Better Pexels Matches**
   - Before: "Best Eats in Northeast USA" → Unclear what "eats" means
   - After: "Best Eats in Northeast USA seafood" → Clear focus on seafood

### 3. **Improved SDXL Generation**
   - Before: Generic prompt → Generic AI images
   - After: Specific prompt with keywords → Focused AI images

### 4. **Backwards Compatible**
   - Without keywords: Works exactly as before
   - With keywords: Enhanced behavior

### 5. **Transparent & Debuggable**
   - Logs show which keywords are being used
   - Easy to verify correct prompt enhancement

---

## Testing the Enhancement

### Test Case 1: With Keywords
```
Title: "Best Eats in the Northeast USA"
Keywords: ["seafood", "boston", "restaurants"]
Expected: Pexels finds seafood/restaurant images
```

**Steps:**
1. Generate content with AI (will include SEO keywords)
2. Select "Pexels (Free, Fast)" source
3. Click "Generate"
4. Check logs for: `"Best Eats in the Northeast USA seafood"`
5. Verify image shows relevant content (seafood/restaurants)

### Test Case 2: Without Keywords
```
Title: "Best Eats in the Northeast USA"
Keywords: [] (none)
Expected: Falls back to original behavior
```

**Steps:**
1. Clear SEO keywords manually
2. Click "Generate"
3. Check logs for: `"Best Eats in the Northeast USA"` (no keywords appended)
4. Verify search works with title only

### Test Case 3: SDXL Generation with Keywords
```
Title: "AI Gaming NPCs"
Keywords: ["gaming", "NPCs", "AI", "virtual reality"]
Expected: SDXL generates AI gaming NPC image
```

**Steps:**
1. Select "SDXL Generation" source
2. Click "Generate"
3. Check logs for: `"AI Gaming NPCs gaming"` (enhanced prompt)
4. Verify generated image shows gaming/NPC content

---

## Code Files Modified

| File | Changes | Lines |
|------|---------|-------|
| [web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx](web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx) | Extract keywords and send to backend | 63-100 |
| [src/cofounder_agent/routes/media_routes.py](src/cofounder_agent/routes/media_routes.py) | Add `build_enhanced_search_prompt()` helper | 313-347 |
| [src/cofounder_agent/routes/media_routes.py](src/cofounder_agent/routes/media_routes.py) | Use enhanced prompts in Pexels search | 408-420 |
| [src/cofounder_agent/routes/media_routes.py](src/cofounder_agent/routes/media_routes.py) | Use enhanced prompts in SDXL generation | 429-442 |
| [src/cofounder_agent/routes/media_routes.py](src/cofounder_agent/routes/media_routes.py) | Update generation_image call with enhanced prompt | 461 |

---

## Deployment Steps

1. **No database migrations needed** ✅
2. **No environment variable changes needed** ✅
3. **Fully backwards compatible** ✅

### To Deploy:
```bash
# Backend
cd src/cofounder_agent
git add routes/media_routes.py
git commit -m "Enhanced image generation with SEO keyword-based prompts"

# Frontend
cd web/oversight-hub
git add src/components/tasks/ResultPreviewPanel.jsx
git commit -m "Extract and send SEO keywords to image generation endpoint"

# Restart services
python src/cofounder_agent/main.py  # Backend
npm start --prefix web/oversight-hub  # Frontend
```

---

## Next Steps

### Optional Enhancements:
1. **Multiple keyword combinations** - Try different keyword combinations if first fails
2. **Keyword weighting** - Use importance scores from SEO metadata
3. **Content-aware keywords** - Extract keywords from blog content body
4. **A/B Testing** - Track which keywords produce best results

### Monitoring:
- Log image search queries to understand what works
- Track success rate of Pexels searches with keywords
- Measure user satisfaction with generated images

---

## Summary

✅ **Implementation Complete**

**What Changed:**
- Frontend now extracts SEO keywords from metadata
- Backend receives keywords in image generation requests
- Enhanced prompt builder combines title + keywords for specificity
- Both Pexels search and SDXL generation use enhanced prompts
- Full logging for debugging and monitoring

**Result:**
- More relevant featured images
- Better Pexels matches
- Improved SDXL generation
- Backwards compatible
- Transparent & debuggable

**Status:** Ready for testing and deployment
