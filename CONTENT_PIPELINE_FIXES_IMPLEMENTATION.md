# Content Pipeline Fixes - Implementation Guide

**Date:** December 17, 2025  
**Status:** Ready to Implement

---

## 🎯 Overview

This document provides specific code changes to fix the 7 critical data flow issues in the content generation pipeline.

---

## FIX #1: Store Content in content_tasks

### File: `src/cofounder_agent/routes/content_routes.py`

### Action: Update task storage to include content field

**Location:** Find where tasks are stored after generation  
**Problem:** Content is only stored in "result" field, not "content" field  
**Solution:** Explicitly store content in content_tasks.content

### Code Change

**Find this code:**

```python
# Update task with generation results
await task_store.update_task(task_id, {
    "result": result,
    "status": "completed",
    "task_metadata": {
        ...
    }
})
```

**Replace with:**

```python
# Update task with generation results
# Store content in BOTH places (content_tasks.content and result)
content_text = result.get("content") if isinstance(result, dict) else result

await task_store.update_task(task_id, {
    "content": content_text,  # ✅ NEW: Store in content field
    "result": result,
    "status": "completed",
    "task_metadata": {
        "content_generated": True,
        "content_length": len(content_text) if content_text else 0,
        ...
    }
})
```

---

## FIX #2: Store Featured Image URL in content_tasks

### File: `src/cofounder_agent/services/image_service.py` or `src/cofounder_agent/routes/content_routes.py`

### Action: Update featured image storage

**Location:** After image generation succeeds  
**Problem:** Generated image URL not stored in content_tasks table  
**Solution:** Save featured_image_url after image is generated/approved

### Code Change

**Find this code:**

```python
# Image generation completes
featured_image = await generate_image(prompt)
# → Image now exists but not stored in task!

return {
    "featured_image_url": featured_image.get("url"),
    "featured_image_local": featured_image.get("local_path"),
    ...
}
```

**Replace with:**

```python
# Image generation completes
featured_image = await generate_image(prompt)

# ✅ NEW: Store featured image URL in content_tasks
featured_image_url = featured_image.get("url")
featured_image_local = featured_image.get("local_path")

# Update task with featured image
await task_store.update_task(task_id, {
    "featured_image_url": featured_image_url,
    "featured_image_data": featured_image,
    "task_metadata": {
        "featured_image": {
            "url": featured_image_url,
            "local_path": featured_image_local,
            "source": featured_image.get("source"),
            "generated_at": datetime.now().isoformat(),
        }
    }
})

return {
    "featured_image_url": featured_image_url,
    "featured_image_local": featured_image_local,
    ...
}
```

---

## FIX #3: Extract Title from Content (NOT Default to "Untitled")

### File: `src/cofounder_agent/routes/content_routes.py`

### Location: Lines 505-520 (in approve_and_publish_task function)

### Problem: Title always defaults to "Untitled"

### Solution: Extract title from content with multiple fallback strategies

### Current (Broken) Code

```python
# Generate slug from title if not provided
title = task_metadata.get("title", "Untitled")
slug = task_metadata.get("slug", "")
if not slug:
    # Generate slug from title
    import re
    import uuid
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
```

### Fixed Code

```python
import re

# ============================================================================
# EXTRACT TITLE WITH MULTIPLE FALLBACK STRATEGIES
# ============================================================================

# Priority order for title extraction:
title = None

# 1. Try stored title from task metadata
title = task_metadata.get("title")
if title and title.lower() != "untitled":
    logger.debug(f"✓ Using stored title: {title[:50]}")
else:
    title = None

# 2. Try task subject/topic
if not title:
    title = task_metadata.get("subject") or task_metadata.get("topic")
    if title:
        logger.debug(f"✓ Using topic as title: {title[:50]}")

# 3. Extract from content (first meaningful line)
if not title:
    lines = content.split('\n')
    for line in lines:
        cleaned = line.strip()
        # Skip empty lines and very short lines
        if cleaned and len(cleaned) > 10 and not cleaned.startswith('-'):
            # Check if it looks like a heading (not too long, no commas)
            if len(cleaned) < 150:
                title = cleaned[:100]
                logger.debug(f"✓ Extracted title from content: {title[:50]}")
                break

# 4. Fallback: Use first paragraph as title (shortened)
if not title:
    paragraphs = content.split('\n\n')
    for para in paragraphs:
        para_clean = para.strip()
        if para_clean and len(para_clean) > 10:
            # Take first sentence or first 100 chars
            sentences = para_clean.split('. ')
            if sentences:
                title = sentences[0][:100]
                logger.debug(f"✓ Using first sentence as title: {title[:50]}")
                break

# 5. Last resort: Date-based title
if not title:
    title = f"Blog Post - {datetime.now().strftime('%B %d, %Y')}"
    logger.debug(f"✓ Using date-based fallback title: {title}")

# Ensure title is not "Untitled"
if not title or title.lower() == "untitled":
    title = task_metadata.get("topic", "Blog Post")

logger.info(f"📝 Final Title: {title[:80]}")

# ============================================================================
# GENERATE SLUG FROM TITLE
# ============================================================================
slug = task_metadata.get("slug", "")
if not slug:
    # Generate slug from title
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)  # Remove special chars
    slug = re.sub(r'\s+', '-', slug)  # Replace spaces with hyphens
    slug = re.sub(r'-+', '-', slug)  # Replace multiple hyphens with single
    slug = slug.strip('-')

    # Add unique suffix if slug already exists
    base_slug = slug
    counter = 1
    while await db_service.slug_exists(slug):
        slug = f"{base_slug}-{counter}"
        counter += 1

    logger.info(f"📝 Generated Slug: {slug}")
```

---

## FIX #4: Generate Excerpt

### File: `src/cofounder_agent/routes/content_routes.py`

### Location: Lines 520-540 (after title extraction)

### Problem: Excerpt is empty/NULL

### Solution: Auto-generate excerpt from content

### Code to Add (After Title Extraction)

```python
# ============================================================================
# GENERATE EXCERPT (150-200 chars for social sharing)
# ============================================================================

excerpt = task_metadata.get("excerpt", "")

if not excerpt and content:
    # Strategy 1: Extract first paragraph
    paragraphs = content.split('\n\n')
    for para in paragraphs:
        para_clean = para.strip()
        if para_clean and len(para_clean) > 20:
            # Skip if paragraph is title-like (very short)
            if len(para_clean) > 30:
                excerpt = para_clean[:200]
                if len(para_clean) > 200:
                    excerpt += "..."
                logger.debug(f"✓ Extracted excerpt from first paragraph")
                break

    # Strategy 2: Use first 200 chars if no paragraph found
    if not excerpt:
        excerpt = content[:200]
        if len(content) > 200:
            excerpt += "..."
        logger.debug(f"✓ Generated excerpt from content start")

# Clean up excerpt (remove line breaks)
excerpt = excerpt.replace('\n', ' ').replace('\r', '')
excerpt = ' '.join(excerpt.split())  # Normalize whitespace

# Ensure excerpt is not too long
if len(excerpt) > 500:
    excerpt = excerpt[:500] + "..."

logger.info(f"📝 Generated Excerpt: {excerpt[:100]}...")
```

---

## FIX #5: Match Author from Content

### File: `src/cofounder_agent/routes/content_routes.py`

### Location: Lines 540-560 (after excerpt)

### Problem: author_id is NULL

### Solution: Use system author or match from metadata

### Code to Add

```python
# ============================================================================
# ASSIGN AUTHOR (Default: Poindexter AI)
# ============================================================================

# Priority order for author selection:
author_id = task_metadata.get("author_id")

if not author_id:
    # Try secondary sources
    author_id = task_metadata.get("generated_by_author")

if not author_id:
    # Try to infer from author name if provided
    author_name = task_metadata.get("author_name")
    if author_name:
        author = await db_service.get_author_by_name(author_name)
        if author:
            author_id = author["id"]
            logger.debug(f"✓ Found author by name: {author_name}")

if not author_id:
    # Use system author (Poindexter AI - the AI that generates content)
    author_id = "14c9cad6-57ca-474a-8a6d-fab897388ea8"  # Poindexter AI UUID
    logger.debug(f"✓ Using system author: Poindexter AI")

logger.info(f"👤 Author ID: {author_id}")
```

---

## FIX #6: Match Category from Content

### File: `src/cofounder_agent/routes/content_routes.py`

### Location: Lines 560-590 (after author)

### Problem: category_id is NULL

### Solution: Match category from content keywords

### Code to Add

```python
# ============================================================================
# MATCH CATEGORY (By keyword matching)
# ============================================================================

category_id = task_metadata.get("category_id")

if not category_id:
    # Try to infer from topic/content keywords
    topic = task_metadata.get("topic", "")
    content_start = content[:500] if content else ""  # First 500 chars
    search_text = f"{topic} {content_start}".lower()

    # Get all available categories
    try:
        categories = await db_service.get_all_categories()
        logger.debug(f"📊 Matching category from {len(categories)} available categories")

        # Score each category based on keyword matches
        best_category = None
        best_score = 0

        for cat in categories:
            cat_name = cat.get("name", "").lower()
            cat_desc = cat.get("description", "").lower()
            cat_id = cat.get("id")

            score = 0

            # Check exact name match (high weight)
            if cat_name in search_text:
                score += 10

            # Check description keywords (medium weight)
            for keyword in cat_desc.split():
                if len(keyword) > 3 and keyword in search_text:
                    score += 2

            # Check topic keywords against category (medium weight)
            for keyword in topic.split():
                if len(keyword) > 3:
                    if keyword in cat_name:
                        score += 3
                    if keyword in cat_desc:
                        score += 1

            logger.debug(f"  - {cat_name}: score={score}")

            if score > best_score:
                best_score = score
                best_category = cat

        if best_category and best_score > 0:
            category_id = best_category["id"]
            logger.info(f"✓ Matched category: {best_category.get('name')} (score={best_score})")
        else:
            # Use first category as default
            if categories:
                category_id = categories[0]["id"]
                logger.info(f"✓ Using first category as default: {categories[0].get('name')}")

    except Exception as e:
        logger.warning(f"⚠️  Could not match category: {e}")
        # Category ID will be None (allowed by schema)

logger.info(f"📁 Category ID: {category_id}")
```

---

## FIX #7: Extract Tags from Content

### File: `src/cofounder_agent/routes/content_routes.py`

### Location: Lines 590-620 (after category)

### Problem: tag_ids is empty/NULL

### Solution: Extract tags from content keywords

### Code to Add

```python
# ============================================================================
# EXTRACT TAGS (From content keywords)
# ============================================================================

tag_ids = task_metadata.get("tag_ids") or task_metadata.get("tags") or []

# Convert to list if single string
if isinstance(tag_ids, str):
    tag_ids = [tag_ids]

if not tag_ids:
    # Try to extract tags from content
    try:
        tags_available = await db_service.get_all_tags()
        logger.debug(f"🏷️  Matching tags from {len(tags_available)} available tags")

        # Create search text from topic and content
        search_text = f"{task_metadata.get('topic', '')} {content[:300]}".lower()

        matched_tags = []

        for tag in tags_available:
            tag_name = tag.get("name", "").lower()
            tag_slug = tag.get("slug", "").lower()
            tag_id = tag.get("id")

            # Check if tag name or slug appears in content
            if tag_name in search_text or tag_slug in search_text:
                matched_tags.append(tag_id)
                logger.debug(f"  + Matched tag: {tag.get('name')}")

        # Limit to 5 tags maximum
        tag_ids = matched_tags[:5]

        if tag_ids:
            logger.info(f"✓ Extracted {len(tag_ids)} tags")
        else:
            logger.debug(f"  No tags matched from content")

    except Exception as e:
        logger.warning(f"⚠️  Could not extract tags: {e}")
        tag_ids = []

logger.info(f"🏷️  Tag IDs: {len(tag_ids)} tags")
```

---

## FIX #8: Ensure SEO Fields Are Populated

### File: `src/cofounder_agent/routes/content_routes.py`

### Location: Lines 620-640 (in post_data assembly)

### Problem: seo_title, seo_description, seo_keywords are default/missing

### Solution: Ensure they're filled with good values

### Code in post_data Dictionary

```python
post_data = {
    "id": task_metadata.get("post_id"),
    "title": title,  # ✅ Now populated from Fix #3
    "slug": slug,    # ✅ Now generated from title
    "content": content,
    "excerpt": excerpt,  # ✅ Now populated from Fix #4
    "featured_image_url": featured_image_url,  # ✅ From Fix #2
    "cover_image_url": task_metadata.get("cover_image_url"),
    "author_id": author_id,  # ✅ From Fix #5
    "category_id": category_id,  # ✅ From Fix #6
    "tag_ids": tag_ids if isinstance(tag_ids, list) else [tag_ids] if tag_ids else None,  # ✅ From Fix #7
    "status": "published",

    # SEO Fields
    "seo_title": task_metadata.get("seo_title") or title[:60],  # Use title if not provided
    "seo_description": task_metadata.get("seo_description") or excerpt[:160],  # Use excerpt if not provided
    "seo_keywords": task_metadata.get("seo_keywords") or ",".join([t.get("name") for t in tags_available if tag_id in tag_ids] if tag_ids else []),  # Generate from tags

    "created_by": reviewer_author_id,  # Poindexter AI UUID
    "updated_by": reviewer_author_id,  # Poindexter AI UUID
}
```

---

## DATABASE SERVICE Helper Methods

### File: `src/cofounder_agent/services/database_service.py`

### Action: Add helper methods for matching

```python
async def get_all_categories(self) -> List[Dict[str, Any]]:
    """Get all categories for matching"""
    async with self.pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, slug, description FROM categories ORDER BY name")
        return [self._convert_row_to_dict(row) for row in rows]

async def get_all_tags(self) -> List[Dict[str, Any]]:
    """Get all tags for matching"""
    async with self.pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, slug, description FROM tags ORDER BY name")
        return [self._convert_row_to_dict(row) for row in rows]

async def get_author_by_name(self, name: str) -> Optional[Dict[str, Any]]:
    """Get author by name"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, slug, email FROM authors WHERE LOWER(name) = LOWER($1)",
            name
        )
        return self._convert_row_to_dict(row) if row else None

async def slug_exists(self, slug: str) -> bool:
    """Check if slug already exists in posts"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM posts WHERE slug = $1",
            slug
        )
        return row is not None
```

---

## Testing Changes

### Test Scenario 1: Title Extraction

```python
# Create a task with no title metadata
response = client.post("/api/content/tasks", json={
    "prompt": "Write about AI and machine learning",
    "style": "technical",
    "tone": "professional"
})

# Approve it
task_id = response.json()["task_id"]
response = client.post(f"/api/content/tasks/{task_id}/approve", json={
    "approved": True,
    "human_feedback": "Looks good",
    "reviewer_id": "admin"
})

# Check posts table
post = db.query("SELECT title, slug FROM posts ORDER BY created_at DESC LIMIT 1")
assert post.title != "Untitled"
assert post.slug != "untitled-*"
print(f"✓ Title: {post.title}")
```

### Test Scenario 2: Featured Image

```python
# Generate content with image
response = client.post("/api/content/tasks", json={
    "prompt": "Write about AI and machine learning",
    "generate_featured_image": True
})

task_id = response.json()["task_id"]
# Wait for generation...

# Check content_tasks has featured_image_url
task = db.query("SELECT featured_image_url FROM content_tasks WHERE task_id = $1", task_id)
assert task.featured_image_url is not None
print(f"✓ Featured Image: {task.featured_image_url}")

# Approve and check posts
response = client.post(f"/api/content/tasks/{task_id}/approve", ...)
post = db.query("SELECT featured_image_url FROM posts ORDER BY created_at DESC LIMIT 1")
assert post.featured_image_url == task.featured_image_url
print(f"✓ Image in post: {post.featured_image_url}")
```

### Test Scenario 3: Metadata Extraction

```python
# Approve any content and verify all fields
response = client.post(f"/api/content/tasks/{task_id}/approve", json={
    "approved": True,
    "human_feedback": "Good",
    "reviewer_id": "admin"
})

# Check posts has all fields
post = db.query("""
    SELECT
        title, excerpt, featured_image_url,
        author_id, category_id, tag_ids,
        seo_title, seo_description, seo_keywords
    FROM posts
    WHERE id = (SELECT post_id FROM content_tasks WHERE task_id = $1)
""", task_id)

assert post.title != "Untitled" and post.title != ""
assert post.excerpt != "" and post.excerpt is not None
assert post.featured_image_url is not None
assert post.author_id is not None
assert post.category_id is not None
assert len(post.tag_ids or []) >= 0

print(f"✓ All fields populated")
```

---

## Summary of Changes

| Issue                | File              | Fix                                       |
| -------------------- | ----------------- | ----------------------------------------- |
| Content not stored   | content_routes.py | Store in content_tasks.content            |
| Image URL not stored | image_service.py  | Store featured_image_url after generation |
| Title = "Untitled"   | content_routes.py | Extract from content with fallbacks       |
| Excerpt empty        | content_routes.py | Generate from first paragraph             |
| Author NULL          | content_routes.py | Use Poindexter AI default                 |
| Category NULL        | content_routes.py | Match from keywords                       |
| Tags empty           | content_routes.py | Extract from keywords                     |
| SEO fields           | content_routes.py | Use title/excerpt if not provided         |

---

**Status:** Ready to Implement  
**Estimated Time:** 2-3 hours  
**Difficulty:** Medium  
**Risk:** Low (non-breaking changes)
