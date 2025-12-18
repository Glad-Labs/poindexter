# Image Generation SEO Enhancement - Quick Reference

## TL;DR (What Changed)

✨ **Image generation now uses SEO keywords to find/generate more relevant images**

**Before:** "Best Eats in Northeast USA" → Generic food images  
**After:** "Best Eats in Northeast USA" + keywords "seafood" → Seafood restaurant images ✅

---

## Files Modified

| File | Change | Impact |
|------|--------|--------|
| `web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx` | Extract SEO keywords from metadata | Frontend now sends keywords to backend |
| `src/cofounder_agent/routes/media_routes.py` | Add `build_enhanced_search_prompt()` + use it | Backend combines title + keywords for better search |

---

## How It Works

```
Blog Post Generation:
┌─────────────────────────────────────────┐
│ Title: "Best Eats in Northeast USA"     │
│ Keywords: ["seafood", "boston", "food"] │
└─────────────────────────────────────────┘
                    ↓
         User clicks "Generate"
                    ↓
┌─────────────────────────────────────────┐
│ Frontend extracts keywords               │
│ Sends: {                                │
│   prompt: "Best Eats in Northeast USA", │
│   keywords: ["seafood", "boston",...]   │
│ }                                       │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ Backend combines title + first keyword  │
│ Enhanced: "Best Eats in Northeast USA   │
│           seafood"                      │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ Pexels search OR SDXL generation        │
│ Uses enhanced prompt                    │
│ Result: Relevant seafood/restaurant img │
└─────────────────────────────────────────┘
```

---

## Frontend Changes Summary

**File:** `web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx`  
**Function:** `generateFeaturedImage()` (Lines 63-100)

**What was added:**
```javascript
// Extract keywords from SEO metadata
let keywords = [];
if (editedSEO?.keywords) {
  if (typeof editedSEO.keywords === 'string') {
    keywords = editedSEO.keywords.split(',').map(kw => kw.trim()).slice(0, 5);
  } else if (Array.isArray(editedSEO.keywords)) {
    keywords = editedSEO.keywords.slice(0, 5).map(kw => String(kw).trim());
  }
}

// Send keywords to backend
requestPayload.keywords = keywords.length > 0 ? keywords : undefined;
```

---

## Backend Changes Summary

**File:** `src/cofounder_agent/routes/media_routes.py`

### New Helper Function (Lines 313-347):
```python
def build_enhanced_search_prompt(base_prompt, keywords=None):
    """Combine title with first keyword for specific search"""
    if not keywords:
        return base_prompt
    primary_keyword = keywords[0]
    enhanced = f"{base_prompt} {primary_keyword}"
    logger.debug(f"📝 Enhanced prompt: '{base_prompt}' → '{enhanced}'")
    return enhanced
```

### Updated Pexels Search (Lines 408-420):
```python
search_prompt = build_enhanced_search_prompt(request.prompt, keywords)
image = await image_service.search_featured_image(
    topic=search_prompt,  # ← Uses enhanced prompt
    keywords=keywords
)
```

### Updated SDXL Generation (Lines 429-461):
```python
generation_prompt = build_enhanced_search_prompt(request.prompt, keywords)
success = await image_service.generate_image(
    prompt=generation_prompt,  # ← Uses enhanced prompt
    output_path=output_path,
    ...
)
```

---

## API Changes

### ImageGenerationRequest (Already Existed)
```python
class ImageGenerationRequest(BaseModel):
    prompt: str  # Title
    keywords: Optional[List[str]] = None  # ← Now used!
    use_pexels: bool = True
    use_generation: bool = False
```

### Request Payload Example
```json
{
  "prompt": "Best Eats in Northeast USA",
  "keywords": ["seafood", "boston", "restaurants"],  // ← Now populated
  "use_pexels": true,
  "use_generation": false
}
```

---

## Testing

### Quick Test (5 minutes)
1. Restart backend: `python src/cofounder_agent/main.py`
2. Restart frontend: `npm start --prefix web/oversight-hub`
3. Generate content with AI
4. Click "Generate" for image
5. Check backend logs for: `"Best Eats in Northeast USA seafood"` (enhanced prompt)

### Verify Image Quality
- **Pexels:** Check if image is more relevant (seafood/restaurant)
- **SDXL:** Check if generated image shows better focus

---

## Backwards Compatibility

✅ **100% backwards compatible**

- Works without keywords (falls back to title only)
- Existing API clients work unchanged
- No database migrations needed
- No environment variable changes

**Example without keywords:**
```json
{
  "prompt": "Best Eats in Northeast USA",
  // keywords not provided → works exactly as before!
  "use_pexels": true
}
```

---

## Logging Examples

### With Keywords (New)
```
🔍 STEP 1: Searching Pexels for: Best Eats in Northeast USA seafood
   Keywords: seafood, boston, restaurants
✅ STEP 1 SUCCESS: Found image via Pexels: https://...
```

### Without Keywords (Old - Still Works)
```
🔍 STEP 1: Searching Pexels for: Best Eats in Northeast USA
✅ STEP 1 SUCCESS: Found image via Pexels: https://...
```

---

## Benefits Summary

| Before | After |
|--------|-------|
| ❌ Generic images | ✅ Specific images matching keywords |
| ❌ Broad search query | ✅ Targeted search query |
| ❌ Limited context for SDXL | ✅ Rich context with keywords |
| ✅ Simple | ✅ Still simple (backwards compatible) |

---

## Deployment Checklist

- [ ] Pull latest code
- [ ] Restart backend: `python src/cofounder_agent/main.py`
- [ ] Restart frontend: `npm start --prefix web/oversight-hub`
- [ ] Test image generation with Pexels
- [ ] Test image generation with SDXL
- [ ] Verify logs show enhanced prompts
- [ ] Check image quality improvement

---

## Troubleshooting

### Issue: No keywords being sent
**Solution:** Check that content was generated with AI (SEO keywords set)  
**Verify:** `editedSEO?.keywords` has values

### Issue: Keywords not improving results
**Solution:** First keyword may not be optimal  
**Next:** Check other keywords in the list

### Issue: Backwards compatibility broken
**Solution:** Not possible - keywords are optional  
**Debug:** Check that keywords are undefined/absent in logs

---

## Code Locations Quick Links

| Component | File | Lines |
|-----------|------|-------|
| Frontend keyword extraction | ResultPreviewPanel.jsx | 63-100 |
| Backend prompt builder | media_routes.py | 313-347 |
| Pexels enhanced search | media_routes.py | 408-420 |
| SDXL enhanced generation | media_routes.py | 429-461 |

---

## Status

✅ **Implementation Complete**  
✅ **Code Compiles**  
✅ **Backwards Compatible**  
✅ **Ready for Testing**  
✅ **Ready for Deployment**  

---

## Next Steps

1. **Test locally** with new code
2. **Monitor image quality** improvements
3. **Collect feedback** on image relevance
4. **Consider enhancements**:
   - Try multiple keywords if first fails
   - Use keyword importance scores
   - Extract keywords from content body

---

## Questions?

**What if content doesn't have SEO keywords?**  
→ Works fine - falls back to title-only search

**Will this affect existing tasks?**  
→ No - only affects new image generations

**Can I disable this feature?**  
→ Not needed - compatible with/without keywords

**How does it affect cost?**  
→ No impact - Pexels and SDXL costs unchanged
