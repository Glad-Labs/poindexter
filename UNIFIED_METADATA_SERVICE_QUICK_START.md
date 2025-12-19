# Quick Start - Unified Metadata Service

## 🚀 Basic Usage

### Import the service

```python
from services.unified_metadata_service import get_unified_metadata_service

service = get_unified_metadata_service()
```

### Generate all metadata at once (Recommended)

```python
metadata = await service.generate_all_metadata(
    content="Your blog post content here...",
    topic="Optional topic/subject",
    title="Optional stored title",
    excerpt="Optional stored excerpt",
    featured_image_url="https://example.com/image.jpg",
    available_categories=[{"id": "...", "name": "...", "description": "..."}],
    available_tags=[{"id": "...", "name": "...", "slug": "..."}],
    author_id="optional-author-uuid"  # Defaults to Poindexter AI
)

# Access the generated metadata
print(f"Title: {metadata.title}")
print(f"Slug: {metadata.slug}")
print(f"Excerpt: {metadata.excerpt}")
print(f"SEO Title: {metadata.seo_title}")
print(f"SEO Description: {metadata.seo_description}")
print(f"Category: {metadata.category_name}")
print(f"Tags: {metadata.tags}")
print(f"Featured Image: {metadata.featured_image_url}")
```

---

## 📋 Individual Operations (If Needed)

### Extract Title

```python
title = await service.extract_title(
    content="...",
    topic="Optional topic",
    stored_title="Optional title"  # If already have one
)
```

### Generate Excerpt

```python
excerpt = await service.generate_excerpt(
    content="...",
    stored_excerpt="Optional excerpt",
    max_length=200  # Characters
)
```

### Generate SEO Metadata

```python
seo = await service.generate_seo_metadata(
    title="Blog title",
    content="Blog content",
    stored_seo={"seo_title": "...", "seo_description": "..."}  # Optional
)
# Returns: {"seo_title": "...", "seo_description": "...", "seo_keywords": [...]}
```

### Generate Slug

```python
slug = service.generate_slug("Your Blog Title Here")
# Returns: "your-blog-title-here"
```

### Match Category

```python
category = await service.match_category(
    content="...",
    available_categories=[...],
    title="Optional title"
)
# Returns: {"id": "...", "name": "..."}
```

### Extract Tags

```python
tag_ids = await service.extract_tags(
    content="...",
    available_tags=[...],
    title="Optional title",
    max_tags=5
)
# Returns: ["tag-id-1", "tag-id-2", ...]
```

### Generate Featured Image Prompt

```python
prompt = service.generate_featured_image_prompt(
    title="Blog Title",
    content="Blog content",
    category="Tech"
)
# Returns: Detailed prompt with "NO PEOPLE" requirement
```

### Generate Social Metadata

```python
social = service.generate_social_metadata(
    title="Blog Title",
    excerpt="Blog excerpt",
    image_url="https://..."
)
# Returns: OG tags and Twitter card metadata
```

---

## 📊 Data Structure

### UnifiedMetadata Object

All metadata is returned in a single `UnifiedMetadata` dataclass:

```python
metadata = UnifiedMetadata(
    # Core content
    title: str
    excerpt: str
    slug: str

    # SEO optimization
    seo_title: str
    seo_description: str
    seo_keywords: List[str]

    # Organization
    category_id: Optional[str]
    category_name: str
    tag_ids: List[str]
    tags: List[str]
    author_id: str  # Defaults to Poindexter AI

    # Media
    featured_image_prompt: str
    featured_image_url: Optional[str]
    featured_image_alt_text: str

    # Social media sharing
    og_title: str
    og_description: str
    og_image: Optional[str]
    twitter_title: str
    twitter_description: str
    twitter_card: str

    # Structured data
    json_ld_schema: Optional[Dict[str, Any]]

    # Analytics
    word_count: int
    reading_time_minutes: int
)
```

---

## 💡 Real-World Example

```python
# In your approval endpoint
from services.unified_metadata_service import get_unified_metadata_service

async def approve_and_publish(task_id: str, content: str, task_metadata: Dict):
    service = get_unified_metadata_service()

    # Get database objects
    categories = await db_service.get_all_categories()
    tags = await db_service.get_all_tags()

    # Generate complete metadata in one call
    metadata = await service.generate_all_metadata(
        content=content,
        topic=task_metadata.get("topic"),
        available_categories=categories,
        available_tags=tags
    )

    # Build post data for database
    post_data = {
        "title": metadata.title,                  # ✅ Extracted/generated
        "slug": metadata.slug,                    # ✅ Generated
        "content": content,
        "excerpt": metadata.excerpt,              # ✅ Generated
        "featured_image_url": metadata.featured_image_url,
        "author_id": metadata.author_id,          # ✅ Matched or default
        "category_id": metadata.category_id,      # ✅ Matched
        "tag_ids": metadata.tag_ids,              # ✅ Extracted
        "status": "published",
        "seo_title": metadata.seo_title,          # ✅ Generated
        "seo_description": metadata.seo_description,
        "seo_keywords": metadata.seo_keywords,
    }

    # Save to database
    post = await db_service.create_post(post_data)

    return post
```

---

## ⚙️ Configuration

### LLM Selection

The service automatically uses:

1. Claude 3 Haiku (Anthropic) if available
2. GPT-3.5-turbo (OpenAI) if available
3. Falls back to simple extraction if neither available

Set environment variables:

```bash
# For Anthropic
export ANTHROPIC_API_KEY="your-key"

# For OpenAI
export OPENAI_API_KEY="your-key"
```

### Model Override

```python
from services.unified_metadata_service import UnifiedMetadataService

# Use specific model
service = UnifiedMetadataService(model="claude-3-haiku-20240307")
```

---

## 🔍 Fallback Strategies

### For Title

1. Use stored title (if not "Untitled")
2. Use topic (if provided)
3. Extract first meaningful line from content
4. **Use LLM to generate** (intelligent fallback)
5. Use date-based title (last resort)

### For Excerpt

1. Use stored excerpt (if good)
2. Extract first paragraph from content
3. **Use LLM to generate** (intelligent fallback)
4. Use content start (last resort)

### For SEO Metadata

1. Use stored values (if provided)
2. Analyze content + topic
3. **Use LLM for enhancement** (intelligent fallback)
4. Use simple extraction

### For Category

1. **Keyword match** against category names/descriptions
2. **Use LLM to intelligently match** (intelligent fallback)
3. Use first available category

### For Tags

1. **Keyword match** against tag names/slugs
2. **Use LLM to intelligently extract** (intelligent fallback)
3. Return empty list (better than random tags)

---

## 📊 Performance Tips

1. **Use batch operation** `generate_all_metadata()` instead of individual calls
   - More efficient
   - Better organized
   - Single LLM call for all operations

2. **Cache results** for repeated content

   ```python
   # Add to your caching layer
   cache_key = f"metadata:{content_hash}"
   if cached := await cache.get(cache_key):
       return cached
   ```

3. **Batch multiple posts** if processing many
   ```python
   # Better than calling service multiple times
   tasks = [
       service.generate_all_metadata(content1, ...),
       service.generate_all_metadata(content2, ...),
       service.generate_all_metadata(content3, ...),
   ]
   results = await asyncio.gather(*tasks)
   ```

---

## 🐛 Debugging

### Enable detailed logging

```python
import logging
logging.getLogger("services.unified_metadata_service").setLevel(logging.DEBUG)
```

### Check what fallback was used

Logs will show:

- ✓ Using stored title
- ✓ Using topic as title
- ✓ Extracted title from content
- ✓ LLM generated title
- ✓ Using date-based fallback

### Verify LLM availability

```python
from services.unified_metadata_service import ANTHROPIC_AVAILABLE, OPENAI_AVAILABLE

print(f"Anthropic available: {ANTHROPIC_AVAILABLE}")
print(f"OpenAI available: {OPENAI_AVAILABLE}")
```

---

## ✅ Common Issues & Solutions

### Issue: Getting "Untitled" posts

**Solution:** The old code path is still being used

- Verify you're importing from `unified_metadata_service`
- Check that `content_routes.py` has been updated
- Restart the server

### Issue: No category/tags matched

**Solution:** Categories/tags list might be empty or format wrong

```python
# Verify format
for cat in categories:
    assert "id" in cat
    assert "name" in cat
    assert "description" in cat
```

### Issue: Featured image URL is None

**Solution:** Image URL might not be stored in task_metadata

- Check task_metadata keys
- Verify image generation completed
- Look for URL in different field names

### Issue: SEO keywords are generic

**Solution:** Content might be too short or unclear

- Ensure content has substantive text
- Topic should be specific, not generic
- LLM will do best with clear, detailed content

---

## 🚀 Migration Guide

### Before (Old Way)

```python
# Multiple steps, scattered logic
from services.llm_metadata_service import get_llm_metadata_service
from services.seo_content_generator import get_seo_content_generator

llm_service = get_llm_metadata_service()
seo_service = get_seo_content_generator()

title = await llm_service.extract_title(content, topic)
slug = seo_service.metadata_gen._generate_slug(title)
excerpt = await llm_service.generate_excerpt(content)
seo = await llm_service.generate_seo_metadata(title, content)
# ... more scattered calls
```

### After (New Way) ✨

```python
# One call, everything organized
from services.unified_metadata_service import get_unified_metadata_service

service = get_unified_metadata_service()
metadata = await service.generate_all_metadata(
    content=content,
    topic=topic,
    available_categories=categories,
    available_tags=tags
)
# All metadata ready to use!
```

---

**For detailed API documentation, see:** [UNIFIED_METADATA_SERVICE_COMPLETE.md](UNIFIED_METADATA_SERVICE_COMPLETE.md)
