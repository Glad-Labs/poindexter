# Content Generation Pipeline Analysis & Data Flow Debug

**Date:** December 17, 2025  
**Status:** Critical Data Flow Issues Identified

---

## 🔴 CRITICAL ISSUES FOUND

### Issue #1: Title Extraction Failing

**Symptom:** All posts have title = "Untitled"  
**Root Cause:** Task metadata doesn't contain extracted title  
**Location:** `content_routes.py` lines 505-510 defaults to "Untitled"  
**Impact:** Posts are not SEO-friendly, cannot be distinguished

```python
# Current code (WRONG):
title = task_metadata.get("title", "Untitled")  # Falls back to "Untitled"!
```

### Issue #2: Featured Image URL Not Populated

**Symptom:** featured_image_url = None in posts table  
**Root Cause:** Content task doesn't store featured_image_url before approval  
**Evidence:** Database query shows featured_image_url = None for approved tasks  
**Impact:** Posts have no images, affecting engagement and SEO

### Issue #3: Excerpt Not Generated

**Symptom:** excerpt = '' or NULL in posts table  
**Root Cause:** No excerpt generation code in approval flow  
**Impact:** Social media posts can't use excerpt for sharing

### Issue #4: Author/Category/Tags Not Populated

**Symptom:** author_id, category_id, tag_ids = None/empty arrays  
**Root Cause:** No logic to find best matches from existing tables  
**Impact:** Posts not categorized, not attributed, not tagged

### Issue #5: Content Not Stored in content_tasks

**Symptom:** content_tasks.content = NULL for approved items  
**Root Cause:** Content generation stores result in "result" field, not "content" field  
**Impact:** Content loses context through pipeline, approval can't verify it

---

## 📊 DATABASE STATE ANALYSIS

### Current Posts Table Status

```
Total posts with "Untitled": 10+ (all have broken data)

Example post:
ID: e96e5a69-9ff3-4ce0-81a8-4102b0bc5d8f
Title: Untitled ❌
Slug: untitled-6fe0e427 ❌
Content: (checked - exists)
Excerpt: '' ❌
Featured Image URL: None ❌
Author ID: None ❌
Category ID: None ❌
Tag IDs: {} ❌
SEO Title: Untitled ❌
Status: published ✅
```

### Better Post (Partial Data):

```
ID: ad3c684d-51db-4b51-83dd-399ecdbf1754
Title: Untitled (but has good excerpt!)
Slug: untitled-e0d57978
Excerpt: "Discover how generative AI revolutionizes NPC behavior..." ✅
Featured Image URL: None ❌
Author ID: 14c9cad6... (Poindexter AI) ✅
Category ID: 7d520158... (exists) ✅
Tag IDs: None ❌
```

### Content Tasks Status

```
Total approved tasks: 3
Content stored: 0 of 3 (ALL NULL!)
Featured image URL: 0 of 3 (ALL NULL!)

Task d314c061...:
Topic: "How AI-Powered NPCs are Making Games More Immersive"
Content: NULL (should have content!)
Featured Image URL: NULL (should have image!)
Task Metadata: Has approval info but NO content
```

---

## 🔍 DATA FLOW ANALYSIS

### Current Flow (BROKEN):

```
1. Oversight Hub creates task
   ↓ POST /api/content/tasks
   ↓ body: { prompt, style, tone }

2. Backend generates content
   ↓ Stored in: content_tasks.result (NOT content field!)
   ↓ Image generation happens but NOT stored

3. Content task stored in DB
   ↓ content_tasks.content = NULL ❌
   ↓ content_tasks.featured_image_url = NULL ❌

4. Oversight Hub shows task in ApprovalQueue
   ↓ Need to fetch content to show preview
   ↓ But content is NULL in DB!

5. Human approves in ApprovalQueue
   ↓ POST /api/tasks/{task_id}/approve
   ↓ body: { approved: true, human_feedback, reviewer_id }

6. Backend tries to publish to posts table
   ↓ Looks for: title (not found) → defaults to "Untitled"
   ↓ Looks for: featured_image_url (NULL) → NULL stored
   ↓ Looks for: excerpt (not generated) → empty
   ↓ Looks for: author (guessing from content?) → NULL
   ↓ Looks for: category (guessing from content?) → NULL
   ↓ Looks for: tags (no extraction logic) → NULL

7. Post published to posts table
   ↓ title = "Untitled" ❌
   ✓ slug = auto-generated from title = "untitled-XXXX" ✓
   ❌ featured_image_url = NULL
   ❌ excerpt = empty
   ❌ author_id = NULL
   ❌ category_id = NULL
   ❌ tag_ids = NULL

8. Post appears on website (but worthless!)
   ✓ Content is there
   ❌ No image
   ❌ No metadata
   ❌ No author attribution
   ❌ No categories/tags
```

### What SHOULD Happen:

```
1-3. Same as above, BUT:
   ✓ Store content in content_tasks.content
   ✓ Store featured_image_url in content_tasks.featured_image_url

4. ApprovalQueue shows task with:
   ✓ Content visible (for preview)
   ✓ Generated featured image visible (for preview/approval)

5. Human approves with:
   ✓ Ability to see what they're approving

6. Backend publishes with:
   ✓ title = extracted from content (H1 or first sentence)
   ✓ featured_image_url = from content_tasks.featured_image_url
   ✓ excerpt = auto-generated 100-150 char summary
   ✓ author_id = matched from content themes
   ✓ category_id = matched from content keywords
   ✓ tag_ids = extracted tags from content

7. Post published with ALL fields populated
   ✓ Professional, complete blog post
   ✓ Ready for social sharing
   ✓ Indexed properly for SEO
```

---

## 🔧 REQUIRED FIXES

### Fix #1: Store Content in content_tasks

**Where:** Content generation routes (where task is created)  
**What:** Ensure `content` field is populated when storing task result  
**File to modify:** `src/cofounder_agent/routes/content_routes.py`  
**Code location:** Where content is generated and task stored

**Before:**

```python
# Content stored only in "result" field
await task_store.update_task(task_id, {
    "result": generated_content
})
```

**After:**

```python
# Store in BOTH places
await task_store.update_task(task_id, {
    "content": generated_content,  # Add this!
    "result": generated_content,   # Keep for backward compat
    "task_metadata": {
        "content_preview": generated_content[:200]  # Preview in metadata
    }
})
```

### Fix #2: Store Featured Image URL in content_tasks

**Where:** Image generation completes  
**What:** Save featured_image_url to content_tasks table  
**File to modify:** `src/cofounder_agent/services/image_service.py` or similar

**Before:**

```python
# Image generated but not saved to task
featured_image = await generate_image(prompt)
# → image saved to Downloads folder
# → NOT saved to content_tasks!
```

**After:**

```python
# Save to task after generation
featured_image = await generate_image(prompt)
featured_image_url = featured_image.get("url")  # CDN or local path

await task_store.update_task(task_id, {
    "featured_image_url": featured_image_url,
    "featured_image_data": featured_image,  # Metadata for preview
    "task_metadata": {
        "featured_image": featured_image_url
    }
})
```

### Fix #3: Extract Title from Content

**Where:** Approval endpoint when publishing to posts  
**What:** Extract H1 or first meaningful sentence as title  
**File to modify:** `src/cofounder_agent/routes/content_routes.py` (lines 505-530)

**Before:**

```python
title = task_metadata.get("title", "Untitled")  # Always fails!
```

**After:**

```python
# Priority order for title extraction:
title = task_metadata.get("title")  # 1. Stored title
if not title:
    title = task_metadata.get("subject")  # 2. Task subject
if not title:
    # 3. Extract from content (first line or H1)
    lines = content.split('\n')
    for line in lines:
        if line.strip() and len(line.strip()) > 10:
            title = line.strip()[:100]  # First substantive line
            break
if not title:
    title = f"Article from {datetime.now().strftime('%B %d, %Y')}"  # 4. Fallback date-based

# Ensure title is not "Untitled"
title = title if title != "Untitled" else task_metadata.get("topic", "Untitled")
```

### Fix #4: Generate Excerpt

**Where:** Approval endpoint when publishing to posts  
**What:** Auto-generate 150-char excerpt from content  
**File to modify:** `src/cofounder_agent/routes/content_routes.py` (lines 530-550)

**Before:**

```python
excerpt = task_metadata.get("excerpt", "")  # Always empty!
```

**After:**

```python
# Generate excerpt if not provided
excerpt = task_metadata.get("excerpt", "")
if not excerpt and content:
    # Extract first paragraph
    paragraphs = content.split('\n\n')
    for para in paragraphs:
        para_clean = para.strip()
        if para_clean and len(para_clean) > 20:
            # Take first 150 chars of first real paragraph
            excerpt = para_clean[:150]
            if len(excerpt) < len(para_clean):
                excerpt += "..."
            break

if not excerpt:
    # Fallback: use first 150 chars of content
    excerpt = content[:150]
    if len(content) > 150:
        excerpt += "..."
```

### Fix #5: Match Author from Content

**Where:** Approval endpoint when publishing to posts  
**What:** Find best matching author based on content keywords/topic  
**File to modify:** `src/cofounder_agent/routes/content_routes.py` (lines 550-565)

**Before:**

```python
author_id = task_metadata.get("author_id")  # Usually None!
```

**After:**

```python
# Priority for author selection:
author_id = task_metadata.get("author_id")

if not author_id:
    # Try to find from task metadata
    author_id = task_metadata.get("generated_by_author")

if not author_id:
    # Use system author (Poindexter AI)
    author_id = "14c9cad6-57ca-474a-8a6d-fab897388ea8"  # Default AI author

    # In future: could use LLM to determine best author based on content
```

### Fix #6: Match Category from Content

**Where:** Approval endpoint when publishing to posts  
**What:** Find best matching category based on content keywords  
**File to modify:** `src/cofounder_agent/routes/content_routes.py` (lines 565-575)

**Before:**

```python
category_id = task_metadata.get("category_id")  # Usually None!
```

**After:**

```python
# Try to find category
category_id = task_metadata.get("category_id")

if not category_id:
    # Try to infer from topic/content
    # Use keyword matching to find best category
    topic = task_metadata.get("topic", content[:100]).lower()

    # Query categories table and find best match
    categories = await db_service.get_all_categories()
    best_category = None
    best_score = 0

    for cat in categories:
        cat_name = cat.get("name", "").lower()
        cat_desc = cat.get("description", "").lower()

        # Simple keyword matching (could be improved with LLM)
        score = 0
        for keyword in topic.split():
            if len(keyword) > 3:
                if keyword in cat_name: score += 3
                if keyword in cat_desc: score += 1

        if score > best_score:
            best_score = score
            best_category = cat

    if best_category:
        category_id = best_category["id"]
    else:
        # Use first/default category
        if categories:
            category_id = categories[0]["id"]
```

### Fix #7: Extract Tags from Content

**Where:** Approval endpoint when publishing to posts  
**What:** Extract relevant tags from content keywords  
**File to modify:** `src/cofounder_agent/routes/content_routes.py` (lines 575-590)

**Before:**

```python
tag_ids = task_metadata.get("tag_ids") or task_metadata.get("tags") or []  # Usually empty!
```

**After:**

```python
# Try to find tags
tag_ids = task_metadata.get("tag_ids") or task_metadata.get("tags") or []

if not tag_ids:
    # Extract tags from content keywords
    # Could use LLM or keyword extraction

    # Query tags table to find matches
    tags_available = await db_service.get_all_tags()
    topic = task_metadata.get("topic", "").lower()

    matched_tags = []
    for tag in tags_available:
        tag_name = tag.get("name", "").lower()
        tag_slug = tag.get("slug", "").lower()

        # Match if tag name appears in topic or content
        if tag_name in topic.lower() or tag_slug in topic.lower():
            matched_tags.append(tag["id"])

    tag_ids = matched_tags[:3]  # Limit to 3 tags
```

---

## 🧪 VERIFICATION STEPS

### Step 1: Verify Data Path

```sql
-- Check if content is being stored
SELECT
    task_id,
    LENGTH(content) as content_chars,
    LENGTH(featured_image_url) as image_url_len,
    task_metadata->>'topic' as topic
FROM content_tasks
WHERE approval_status = 'approved'
LIMIT 1;
```

Expected: All fields should have data, not NULL

### Step 2: Verify Post Publishing

```sql
-- Check if posts are getting populated fields
SELECT
    title,
    LENGTH(excerpt) as excerpt_len,
    featured_image_url,
    author_id,
    category_id,
    array_length(tag_ids, 1) as tag_count
FROM posts
WHERE status = 'published'
LIMIT 5;
```

Expected: All fields populated, titles not "Untitled"

### Step 3: Test End-to-End

1. Create task in UI
2. Generate image
3. Check content_tasks: Should have content + featured_image_url
4. Approve in UI
5. Check posts: Should have all fields populated

---

## 📋 IMPLEMENTATION PRIORITY

### P0 - Critical (Breaks Publishing)

- [ ] Fix: Store content in content_tasks.content
- [ ] Fix: Store featured_image_url in content_tasks
- [ ] Fix: Extract title from content (not default to "Untitled")

### P1 - High (Missing Data)

- [ ] Fix: Generate excerpt
- [ ] Fix: Match author from content
- [ ] Fix: Match category from content
- [ ] Fix: Extract tags from content

### P2 - Medium (Nice to Have)

- [ ] Improve category matching (use LLM similarity)
- [ ] Improve tag extraction (NLP)
- [ ] Populate SEO fields (seo_title, seo_description, seo_keywords)

---

## 🔄 DATA FLOW AFTER FIXES

```
1. Task Created with prompt
   ↓
2. Content generated
   ↓ STORE: content_tasks.content = generated content ✅
   ↓
3. Image generated
   ↓ STORE: content_tasks.featured_image_url = image_url ✅
   ↓
4. Task status = awaiting_approval
   ↓
5. ApprovalQueue retrieves task
   ↓ SHOW: content (from content_tasks.content)
   ↓ SHOW: featured image (from content_tasks.featured_image_url)
   ↓
6. Human reviews and approves
   ↓ POST /api/tasks/{task_id}/approve
   ↓
7. Backend publishes to posts
   ✅ title = extracted from content
   ✅ slug = generated from title
   ✅ content = from content_tasks.content
   ✅ excerpt = generated (150 chars)
   ✅ featured_image_url = from content_tasks.featured_image_url
   ✅ author_id = Poindexter AI
   ✅ category_id = matched from content
   ✅ tag_ids = extracted from content
   ✅ seo_title = from content_tasks.seo_title or generated
   ✅ seo_description = from content_tasks.seo_description or excerpt
   ✅ seo_keywords = from content_tasks.seo_keywords or extracted
   ✅ status = published
   ✅ created_by = reviewer_author_id (Poindexter AI)
   ✅ updated_by = reviewer_author_id (Poindexter AI)
   ↓
8. Post published with complete metadata
   ✓ Professional blog post
   ✓ Ready for promotion
   ✓ Properly categorized and tagged
   ✓ SEO optimized
```

---

## 🛠️ CODE LOCATIONS TO MODIFY

1. **Content generation** → Store content in content_tasks.content
   - File: `src/cofounder_agent/routes/content_routes.py`
   - File: `src/cofounder_agent/services/content_router_service.py`

2. **Image generation** → Store featured_image_url in content_tasks
   - File: `src/cofounder_agent/services/image_service.py`
   - File: `src/cofounder_agent/services/seo_content_generator.py`

3. **Approval/Publishing** → Extract/generate metadata fields
   - File: `src/cofounder_agent/routes/content_routes.py` (lines 440-600)

4. **Database service** → Helper methods for matching
   - File: `src/cofounder_agent/services/database_service.py`
   - Add: `get_all_categories()`, `get_all_tags()`, `match_best_category()`, `match_best_tags()`

---

## ✅ SUCCESS CRITERIA

After fixes, when you approve a post:

- [ ] Title is extracted from content (NOT "Untitled")
- [ ] Slug is generated from title (NOT "untitled-XXXX")
- [ ] Excerpt is generated (NOT empty)
- [ ] Featured image URL is populated (NOT NULL)
- [ ] Author is assigned (Poindexter AI or matched)
- [ ] Category is assigned (matched from content)
- [ ] Tags are assigned (extracted from content)
- [ ] All SEO fields populated
- [ ] Post is marked as "published"
- [ ] Post displays correctly on website with all metadata

---

**Status:** Analysis Complete - Ready for Implementation  
**Estimated Effort:** 2-3 hours  
**Impact:** Critical for post quality and SEO
