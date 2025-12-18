# Image Generation Improvements - NO PEOPLE & CONTENT FILTERING

**Date:** December 17, 2024  
**Issue:** Generated images including inappropriate/NSFW content and irrelevant people  
**Solution:** Improved prompts + Pexels filtering + better search strategies

---

## 🎯 Problem

The image generation was producing:

- ❌ Images with people/portraits (not relevant to tech articles)
- ❌ Inappropriate/NSFW content from Pexels
- ❌ Content mismatched with article topic (e.g., woman in swimsuit for "AI NPCs in Games")

**Root Cause:**

1. Image prompts didn't specify "no people"
2. Pexels search had no content filtering
3. Search terms too generic, matching any remotely related image

---

## ✅ Solutions Implemented

### 1. Enhanced Image Prompt (seo_content_generator.py)

**What Changed:**

- ✅ Added explicit "NO PEOPLE" requirement
- ✅ Focused on concepts, objects, and technology
- ✅ Clear guidance for SDXL generation

**New Prompt Instructions:**

```
⚠️  NO PEOPLE - Do not include any human figures, faces, or portraits
Focus on: objects, nature, technology, abstract concepts, landscapes
If must show scale, use non-human elements (buildings, vehicles, props)

Absolutely NO: People, faces, portraits, humans of any kind
Focus on: The topic/concept, not people
```

**Impact:** SDXL will now generate images focused on topics, not people

---

### 2. Pexels Content Filtering (pexels_client.py)

**What Changed:**

- ✅ Added `_is_content_appropriate()` method
- ✅ Filters out NSFW/inappropriate patterns
- ✅ Logs filtered images for transparency

**Blocked Keywords:**

```
"nsfw", "adult", "nude", "sexy", "lingerie", "bikini",
"swimsuit", "erotic", "sensual", "intimate", "private",
"naked", "bare", "exposed", "provocative", "risqué"
```

**Implementation:**

```python
def _is_content_appropriate(self, photo: Dict[str, Any]) -> bool:
    """Filter out inappropriate content based on metadata"""
    alt = (photo.get("alt", "") or "").lower()
    photographer = (photo.get("photographer", "") or "").lower()

    inappropriate_patterns = [
        "nsfw", "adult", "nude", "sexy", "lingerie", ...
    ]

    for pattern in inappropriate_patterns:
        if pattern in alt or pattern in photographer:
            return False
    return True
```

**Flow:**

1. Fetch 2× more results than needed (to account for filtering)
2. Check each image against inappropriate patterns
3. Return only appropriate results
4. Log number of filtered images

**Impact:** Inappropriate images will be filtered out before display

---

### 3. Improved Search Strategy (image_service.py)

**What Changed:**

- ✅ Multi-level search strategy
- ✅ Concept-based fallbacks
- ✅ Avoids "person/people/portrait" keywords
- ✅ Combines topic + technology/abstract

**Search Order (for "AI-Powered NPCs"):**

1. "AI-Powered NPCs" (direct topic)
2. "technology", "digital", "abstract" (concept fallbacks)
3. "AI-Powered NPCs technology" (combined)
4. "AI-Powered NPCs abstract" (combined alternative)
5. Generic concepts if needed

**Concept Keywords (Always Tried):**

```python
"technology", "digital", "abstract", "modern", "innovation",
"data", "network", "background", "desktop", "workspace",
"object", "product", "design", "pattern", "texture",
"nature", "landscape", "environment", "system", "interface"
```

**Filtering in Search:**

```python
# Skip keywords that suggest people
if not any(term in kw.lower() for term in
    ["person", "people", "portrait", "face", "human"]):
    search_queries.append(kw)
```

**Impact:** Searches will prioritize concept/tech over people

---

## 🔄 Complete Image Generation Flow (Updated)

```
Article: "How AI-Powered NPCs are Making Games More Immersive"
                         │
                         ▼
        ┌─────────────────────────────────────┐
        │ Step 1: Generate Image Prompt       │
        │ (with NO PEOPLE requirement)        │
        └────────────┬────────────────────────┘
                     │
      ┌──────────────┴──────────────┐
      │                             │
      ▼                             ▼
   PEXELS                       SDXL
   (Free)                      (GPU)
      │                             │
      ▼                             ▼
  Search with                Generate with
  improved keywords          NO PEOPLE prompt
  (tech + abstract)              │
      │                          │
      ▼                          ▼
  Filter inappropriate    ✅ Generate image
  content                        │
  ("nsfw", "adult",             ▼
   "nude", etc)             Save to Downloads
      │
      ▼
  Return result OR
  Fallback to SDXL
```

---

## 📋 Files Modified

### 1. seo_content_generator.py

**Method:** `generate_featured_image_prompt()`  
**Changes:** Added "NO PEOPLE" requirement  
**Lines:** ~170-195

### 2. pexels_client.py

**New Method:** `_is_content_appropriate()`  
**Modified Method:** `search_images()`  
**Changes:**

- Added content filtering
- Fetch 2× results to account for filtering
- Return only appropriate images
  **Lines:** ~50-130

### 3. image_service.py

**Modified Method:** `search_featured_image()`  
**Changes:**

- Multi-level search strategy
- Concept-based fallbacks
- Skip person/people keywords
- Combine topic + technology
  **Lines:** ~304-360

---

## 🧪 Testing the Improvements

### Test 1: Image Prompt Check

```python
# Verify prompt includes NO PEOPLE requirement
generator = SEOContentGenerator()
prompt = generator.generate_featured_image_prompt(
    "AI-Powered NPCs in Games",
    "Content about AI NPCs...",
    "Technology"
)
assert "NO PEOPLE" in prompt
assert "humans" in prompt.lower()
```

### Test 2: Content Filtering

```python
# Verify inappropriate images are filtered
client = PexelsClient(api_key="...")
photo = {
    "alt": "Sexy woman in swimsuit",
    "photographer": "John Doe",
    ...
}
assert not client._is_content_appropriate(photo)

# Appropriate image should pass
photo2 = {
    "alt": "Computer networking concept",
    "photographer": "Jane Smith",
    ...
}
assert client._is_content_appropriate(photo2)
```

### Test 3: Search Strategy

```python
# Verify search tries multiple strategies
image = await image_service.search_featured_image(
    "AI-Powered NPCs in Games",
    keywords=["gaming", "technology"]
)
# Should try:
# 1. "AI-Powered NPCs in Games"
# 2. "technology", "digital", "abstract"
# 3. "AI-Powered NPCs in Games technology"
# 4. "gaming" (filtered for "person/people")
```

### Test 4: Live Test in Browser

1. Create new task with topic: "AI-Powered NPCs in Games"
2. Generate featured image
3. Verify: Image shows concept/technology, NOT people
4. Verify: Image relevant to topic

---

## 🚀 Results You'll See

### Before Fix ❌

- Image search returned "Sexy woman in swimsuit" (TOTALLY INAPPROPRIATE)
- SDXL might generate people-focused images
- Search too generic, matching any vaguely related image

### After Fix ✅

- Inappropriate Pexels images filtered out automatically
- SDXL prompts focused on concepts/technology, not people
- Search strategy tries multiple relevant queries
- All images appropriate and topic-relevant

---

## 📝 Configuration & Customization

### To Add More Blocked Keywords

**File:** `pexels_client.py`, method `_is_content_appropriate()`

```python
inappropriate_patterns = [
    # Current list...
    "nsfw", "adult", "nude", "sexy",

    # Add new ones here:
    "new_keyword", "another_bad_word"
]
```

### To Adjust Concept Keywords

**File:** `image_service.py`, method `search_featured_image()`

```python
concept_keywords = [
    # Current concepts...
    "technology", "digital", "abstract",

    # Add or modify here:
    "your_concept", "another_concept"
]
```

### To Modify No-People Terms

**File:** `image_service.py`, method `search_featured_image()`

```python
if not any(term in kw.lower() for term in
    ["person", "people", "portrait", "face", "human",
     "new_term", "add_more"]):  # Add here
    search_queries.append(kw)
```

---

## 🔍 Logging & Monitoring

### What You'll See in Logs

**When searching Pexels:**

```
Searching Pexels for: 'AI-Powered NPCs'
Searching Pexels for: 'technology'
✅ Found featured image for 'AI-Powered NPCs in Games' using query 'technology'
Pexels search for 'technology' returned 5 results
Filtered out 2 inappropriate images
```

**When generating with SDXL:**

```
Generating image with SDXL: [prompt with NO PEOPLE]
Generated image: /Users/.../sdxl_*.png
```

### Metrics to Monitor

- Number of images filtered per search
- Search query success rate
- SDXL generation success rate
- Pexels vs SDXL usage ratio

---

## 🎓 Best Practices Going Forward

### 1. Always Test New Content

- Generate a few articles per week
- Check if images are appropriate and relevant
- Monitor filtering logs for patterns

### 2. Adjust Keywords as Needed

- If certain inappropriate content still slips through, add keywords
- If too many good images are filtered, review patterns
- Keep log of blocked patterns

### 3. Monitor Feedback

- User reports of inappropriate images
- Mismatched images (wrong topic)
- Image quality issues

### 4. Consider Additional Filters

- Image recognition API for additional validation
- Manual review step for sensitive categories
- Category-specific keyword lists

---

## 🔗 Related Documentation

- **SDXL_IMPLEMENTATION_NEXT_STEPS.md** - Image approval workflow
- **CODE_CHANGES_DETAILED.md** - Original code changes
- **QUICK_REFERENCE.md** - Quick start guide

---

## ✨ Summary

**What Fixed:** 3 layers of improvement

1. ✅ SDXL prompts: Explicit "NO PEOPLE" requirement
2. ✅ Pexels search: Content filtering for inappropriate images
3. ✅ Search strategy: Multi-level approach with concept fallbacks

**Impact:**

- No more inappropriate images
- Fewer people-focused images
- Better topic-relevant results

**Next Steps:**

1. Test with existing content
2. Monitor logs for filtering
3. Adjust keywords if needed
4. Continue with Phase 2 implementation (approval endpoint)

---

**Status:** ✅ Complete  
**Ready for:** Testing in Oversight Hub  
**Next Phase:** Phase 2 Implementation (Approval Endpoint)
