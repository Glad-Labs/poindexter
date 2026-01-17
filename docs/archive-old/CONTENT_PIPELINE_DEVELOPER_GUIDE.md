# GLAD LABS CONTENT PIPELINE: DEVELOPER GUIDE

**Understanding the 6-Stage Self-Critiquing Pipeline**

---

## QUICK START: How Content Gets Generated

When a user creates a blog post in the Oversight Hub:

```
1. User clicks "Generate Blog Post"
   ↓
2. REST API: POST /api/content/tasks
   ↓
3. Backend spawns async background task
   ↓
4. ACTIVE 6-STAGE PIPELINE executes (takes ~30-120 seconds)
   ↓
5. Client polls: GET /api/content/tasks/{task_id}
   ↓
6. Results available when status="completed"
```

---

## THE ACTUAL 6-STAGE PIPELINE

### Location: `src/cofounder_agent/services/content_router_service.py`

This is the **ONLY** active content generation pipeline. All stages are executed here.

---

### 📚 STAGE 1: RESEARCH & DRAFT (Lines ~180-290)

**What it does:**

- ContentResearchAgent researches the topic
- ContentCreativeAgent creates the initial draft

**Code:**

```python
# Stage 1a: Research
research_result = await content_agent._research_stage(
    topic=topic,
    style=style,
    tone=tone
)
research_text = research_result.get("research_text", "")

# Stage 1b: Create initial draft
content_text = await content_agent._create_draft_stage(
    research=research_text,
    topic=topic,
    style=style,
    tone=tone,
    target_length=target_length
)

logger.info(f"✅ Initial draft created: {len(content_text)} chars")
```

**Input:**

- topic: "How to Train Your AI"
- style: "narrative" (or: technical, listicle, educational, thought-leadership)
- tone: "professional" (or: casual, academic, inspirational)
- target_length: 2000 (words)

**Output:**

- research_text: Background research
- content_text: Initial blog post draft (markdown)

**Logs show:**

```
🔍 STAGE 1a: Researching topic...
✅ Research complete: 450 chars of background

✍️ STAGE 1b: Creating initial draft...
✅ Initial draft created: 2100 chars
```

---

### ✅ STAGE 2: QUALITY EVALUATION & CRITIQUE (Lines ~290-430)

**What it does:**

- QA Agent evaluates quality WITHOUT rewriting
- ContentCreativeAgent refines IF quality is below threshold

**Code:**

```python
# Stage 2a: Quality evaluation
quality_result = await content_agent._quality_evaluation_stage(
    content=content_text,
    topic=topic
)
# Returns: QualityEvaluationResult with:
#   - overall_score: 0-10
#   - dimensions: {clarity, accuracy, completeness, relevance, seo_quality, readability, engagement}
#   - passing: bool (threshold >= 7.0)
#   - feedback: str (what's wrong)
#   - suggestions: list (how to fix)

logger.info(f"✅ Quality score: {quality_result.overall_score:.1f}/10")
logger.info(f"   Passing: {quality_result.passing} (threshold >= 7.0)")

# Stage 2b: Refine IF needed
if not quality_result.passing:
    content_text = await content_agent._refine_draft_stage(
        draft=content_text,
        feedback=quality_result.feedback,
        suggestions=quality_result.suggestions
    )
    logger.info(f"✅ Draft refined")
```

**Quality Dimensions:**

- **Clarity** (0-10): Is the content easy to understand?
- **Accuracy** (0-10): Are facts correct and well-supported?
- **Completeness** (0-10): Does it cover the topic thoroughly?
- **Relevance** (0-10): Does it match the user request?
- **SEO Quality** (0-10): Optimized for search engines?
- **Readability** (0-10): Is it easy to read (grammar, flow)?
- **Engagement** (0-10): Will readers find it interesting?

**Passing Threshold:** overall_score >= 7.0

**Logs show:**

```
📋 STAGE 2a: Evaluating content quality...
   Clarity: 8.0, Accuracy: 8.5, Completeness: 7.2
   Relevance: 8.8, SEO Quality: 7.5, Readability: 9.0, Engagement: 8.3
✅ Quality evaluation complete:
   Overall Score: 8.2/10
   Passing: True (threshold >= 7.0)

⏭️ STAGE 2b: Refinement skipped (already passing)
```

**Or if not passing:**

```
📋 STAGE 2a: Evaluating content quality...
✅ Quality evaluation complete:
   Overall Score: 6.5/10
   Passing: False (threshold >= 7.0)

💡 STAGE 2b: Refining draft based on feedback...
   Feedback: "Content too brief, needs more examples"
   Suggestions: ["Add 2-3 real-world examples", "Expand methodology section"]
✅ Draft refined
```

---

### 🖼️ STAGE 3: FEATURED IMAGE SEARCH (Lines ~450-500)

**What it does:**

- Searches Pexels for a featured image
- Returns photographer credit and source

**Code:**

```python
featured_image = None
if generate_featured_image:
    featured_image = await image_service.search_featured_image(
        topic=topic,
        keywords=tags or [topic]
    )

    if featured_image:
        image_metadata = featured_image.to_dict()
        result["featured_image_url"] = featured_image.url
        result["featured_image_photographer"] = featured_image.photographer
        result["featured_image_source"] = featured_image.source
        logger.info(f"✅ Featured image found: {featured_image.photographer} (Pexels)")
```

**Returns:**

```python
FeaturedImage(
    url="https://images.pexels.com/photos/...",
    photographer="John Doe",
    source="Pexels"
)
```

**Logs show:**

```
🖼️ STAGE 3: Sourcing featured image from Pexels...
✅ Featured image found: Sarah Chen (Pexels)
```

---

### 📊 STAGE 4: SEO METADATA GENERATION (Lines ~510-580)

**What it does:**

- Generates SEO-optimized title, description, keywords
- Uses topic and content for context

**Code:**

```python
seo_generator = get_seo_content_generator(content_generator)
seo_assets = seo_generator.metadata_gen.generate_seo_assets(
    title=topic,
    content=content_text,
    topic=topic
)

# Extract and validate SEO data
seo_title = seo_assets.get("seo_title", topic)[:60]
seo_description = seo_assets.get("meta_description", "")[:160]
seo_keywords = seo_assets.get("meta_keywords", tags or [])[:10]

result["seo_title"] = seo_title
result["seo_description"] = seo_description
result["seo_keywords"] = seo_keywords
```

**Output:**

- `seo_title`: SEO-optimized title (≤60 chars)
- `seo_description`: Meta description (≤160 chars)
- `seo_keywords`: List of keywords for search (≤10 keywords)

**Logs show:**

```
📊 STAGE 4: Generating SEO metadata...
✅ SEO metadata generated:
   Title: How to Train Your AI: Complete Guide
   Description: Learn AI training techniques, best practices, and tools...
   Keywords: AI training, machine learning, neural networks, ...
```

---

### 📝 STAGE 5: CREATE POST RECORD (Lines ~590-650)

**What it does:**

- Creates a post record in PostgreSQL
- Stores all content, metadata, and images
- Sets status to "draft" (human review required)

**Code:**

```python
# Create post in database
post = await database_service.create_post({
    "title": topic,
    "slug": slug,
    "content": content_text,
    "excerpt": seo_description,
    "featured_image_url": featured_image.url if featured_image else None,
    "author_id": author_id,  # Default: Poindexter AI
    "category_id": category_id,
    "status": "draft",  # Always draft, human must approve
    "seo_title": seo_title,
    "seo_description": seo_description,
    "seo_keywords": ",".join(seo_keywords),
    "metadata": image_metadata if image_metadata else {},
})

result["post_id"] = str(post.id)
result["post_slug"] = post.slug
```

**Database Table:** `posts`

```sql
CREATE TABLE posts (
  id UUID PRIMARY KEY,
  title VARCHAR NOT NULL,
  slug VARCHAR UNIQUE NOT NULL,
  content TEXT NOT NULL,
  excerpt TEXT,
  featured_image_url VARCHAR,
  author_id UUID,
  category_id UUID,
  status VARCHAR DEFAULT 'draft',  -- draft, published, archived
  seo_title VARCHAR,
  seo_description TEXT,
  seo_keywords TEXT,  -- Comma-separated
  metadata JSONB,  -- Image data, etc.
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);
```

**Logs show:**

```
📝 STAGE 5: Creating posts record...
✅ Post created: 550e8400-e29b-41d4-a716-446655440000
   Title: How to Train Your AI
   Slug: how-to-train-your-ai-550e8400
   Author: Poindexter AI
   Category: Technology
```

---

### 🎓 STAGE 6: CAPTURE TRAINING DATA (Lines ~660-720)

**What it does:**

- Stores quality evaluation results
- Captures orchestrator execution data
- Used for improving future content generation

**Code:**

```python
# Store quality evaluation
await database_service.create_quality_evaluation({
    "content_id": task_id,
    "task_id": task_id,
    "overall_score": quality_result.overall_score,
    "clarity": quality_result.dimensions.clarity,
    "accuracy": quality_result.dimensions.accuracy,
    "completeness": quality_result.dimensions.completeness,
    "relevance": quality_result.dimensions.relevance,
    "seo_quality": quality_result.dimensions.seo_quality,
    "readability": quality_result.dimensions.readability,
    "engagement": quality_result.dimensions.engagement,
    "passing": quality_result.passing,
    "feedback": quality_result.feedback,
    "suggestions": quality_result.suggestions,
    "evaluated_by": "ContentQualityService",
    "evaluation_method": quality_result.evaluation_method,
})

# Store orchestrator execution data
await database_service.create_orchestrator_training_data({
    "execution_id": task_id,
    "user_request": f"Generate blog post on: {topic}",
    "intent": "content_generation",
    "business_state": {
        "topic": topic,
        "style": style,
        "tone": tone,
        "featured_image": featured_image is not None,
    },
    "execution_result": "success",
    "quality_score": quality_result.overall_score / 10,  # Normalized 0-1
    "success": quality_result.passing,
    "tags": tags or [],
    "source_agent": "content_router_service",
})
```

**Database Tables:**

- `quality_evaluations` - Stores dimension scores and feedback
- `orchestrator_training_data` - Stores execution metrics for ML training

**Logs show:**

```
🎓 STAGE 6: Capturing training data...
✅ Training data captured for learning pipeline
```

---

## UPDATING THE TASK STATUS

**Final Step (Lines ~730-760):**

After all 6 stages complete, the task is marked as completed:

```python
await database_service.update_task(
    task_id=task_id,
    updates={
        "status": "completed",
        "approval_status": "pending_human_review",
        "quality_score": int(quality_result.overall_score),
        "task_metadata": {
            "featured_image_url": result.get("featured_image_url"),
            "featured_image_photographer": result.get("featured_image_photographer"),
            "featured_image_source": result.get("featured_image_source"),
            "content": content_text,
            "seo_title": seo_title,
            "seo_description": seo_description,
            "seo_keywords": seo_keywords,
        }
    }
)
```

**Task Status Lifecycle:**

- `pending` → Task queued
- `generating` → Pipeline executing (if you set this manually)
- `completed` → All 6 stages finished, awaiting human review
- `approved` → Human approved, ready to publish
- `published` → Content went live
- `failed` → Error occurred during generation

---

## HOW TO MODIFY THE PIPELINE

### Adding a New Stage

**Example: Add watermark to images**

1. Create a new method in ContentImageAgent:

```python
class ContentImageAgent:
    async def _watermark_stage(self, image_url: str) -> str:
        """Add watermark to featured image"""
        watermarked_url = await add_watermark(image_url)
        return watermarked_url
```

2. Call it in `content_router_service.py` after image search:

```python
# After STAGE 3: Image search
if featured_image:
    featured_image.url = await image_agent._watermark_stage(featured_image.url)
    logger.info(f"✅ Watermark applied")
```

3. Update Stage naming (currently 6, now 7):

```python
# Update all stage counters and documentation
```

### Changing Quality Thresholds

**Current:** Passing score >= 7.0

**To change to 8.0:**

```python
# In quality_service.py or content_router_service.py
QUALITY_THRESHOLD = 8.0  # Was 7.0

if quality_result.overall_score >= QUALITY_THRESHOLD:
    quality_result.passing = True
```

### Adding a New Quality Dimension

**Current dimensions:** clarity, accuracy, completeness, relevance, seo_quality, readability, engagement

**To add "tone_consistency":**

1. Update QualityEvaluationResult model
2. Add evaluation logic in quality_agent.py
3. Store in PostgreSQL quality_evaluations table
4. Update UI to display new dimension

---

## CONFIGURATION

### Environment Variables (in `.env.local`)

```env
# Content generation
TARGET_CONTENT_LENGTH=2000  # Default word count
QUALITY_THRESHOLD=7.0       # Passing score threshold
MAX_REFINEMENT_LOOPS=3      # Max times to refine if failing quality

# Image service
PEXELS_API_KEY=xxxx         # Required for Stage 3
IMAGE_SEARCH_ENABLED=true   # Can disable image search

# SEO generation
SEO_SERVICE_ENABLED=true    # Can disable SEO optimization

# Model selection
PREFERRED_MODEL=claude       # Model for content generation
QUALITY_MODEL=claude-fast    # Model for quality evaluation
```

### Request Parameters (when creating a task)

```python
POST /api/content/tasks
{
    "topic": "string (required)",
    "task_type": "blog_post (required)",
    "style": "narrative | technical | listicle | educational | thought-leadership",
    "tone": "professional | casual | academic | inspirational",
    "target_length": 1500,  # words (optional, default 2000)
    "tags": ["AI", "Training"],  # optional
    "generate_featured_image": true,  # Stage 3 (optional, default true)
    "quality_preference": "balanced",  # auto-select models (optional)
    "models_by_phase": {  # Manual model selection (optional)
        "research": "claude",
        "draft": "gpt4",
        "quality": "claude-fast",
        "seo": "claude"
    }
}
```

---

## MONITORING & DEBUGGING

### View Pipeline Progress

**Option 1: REST API**

```bash
curl http://localhost:8000/api/content/tasks/abc123def456
```

Response shows current status and progress for each stage.

**Option 2: Logs**
The backend logs show real-time progress with emoji markers:

```
🔍 STAGE 1a: Researching topic...
✍️ STAGE 1b: Creating initial draft...
✅ STAGE 2a: Quality evaluation complete
⏭️ STAGE 2b: Refinement skipped (already passing)
🖼️ STAGE 3: Sourcing featured image
📊 STAGE 4: Generating SEO metadata...
📝 STAGE 5: Creating posts record...
🎓 STAGE 6: Capturing training data...
```

### Common Issues

**Issue: Quality score is very low (e.g., 3.5/10)**

Check:

1. Is the content_agent initialized properly?
2. Are embeddings/models loading?
3. Check logs for specific dimension failures

**Issue: Featured image not found**

Check:

1. Is PEXELS_API_KEY set in .env.local?
2. Is image_service initialized?
3. Check Pexels API rate limits

**Issue: SEO metadata is generic**

Check:

1. Is SEO_SERVICE_ENABLED=true in .env.local?
2. Is the seo_content_generator initialized?

**Issue: Task stuck in "pending"**

Check:

1. Is background task running? Check `asyncio.create_task()` in content_routes.py
2. Check for exceptions in the background task
3. Verify PostgreSQL connection

---

## TESTING THE PIPELINE

### Run Full Integration Test

```bash
npm run test:python  # All tests including integration tests
```

### Run Specific Pipeline Test

```bash
npm run test:python -- tests/test_full_stack_integration.py -v
```

### Manual Test (via API)

```bash
# 1. Create task
curl -X POST http://localhost:8000/api/content/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI in Healthcare",
    "task_type": "blog_post",
    "style": "narrative",
    "tone": "professional"
  }'

# Response: {"task_id": "abc123", "status": "pending", ...}

# 2. Poll for completion (every 3 seconds)
curl http://localhost:8000/api/content/tasks/abc123

# 3. Repeat until status="completed"
```

---

## ARCHITECTURE SUMMARY

```
┌─────────────────────────────────────────────────────────────┐
│                    React Oversight Hub                       │
│                       (Port 3001)                            │
└──────────────────────┬──────────────────────────────────────┘
                       │ POST /api/content/tasks
                       ▼
┌─────────────────────────────────────────────────────────────┐
│            FastAPI Routes (content_routes.py)                │
│                    (Port 8000)                              │
│  create_content_task() → spawn background task             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼ asyncio.create_task()
┌─────────────────────────────────────────────────────────────┐
│        Content Router Service (MAIN PIPELINE)               │
│     process_content_generation_task() ←─ YOU ARE HERE       │
└──────────────┬────────────────┬───────────────────────────┘
               │                │
   ┌───────────▼────┐   ┌─────────────┬────────────────┐
   │  Agents        │   │  Services   │   Database     │
   ├────────────────┤   ├─────────────┼────────────────┤
   │ Content Agent  │   │ Quality     │ PostgreSQL     │
   │ Research      │   │ Image       │ posts table    │
   │ Creative      │   │ SEO         │ quality_evals  │
   │ QA            │   │ Cost        │ training_data  │
   │ Image Agent    │   │ Model       │                │
   └────────────────┘   │ Router      │                │
                        └─────────────┴────────────────┘

                       STAGES 1-6 EXECUTE
                       (Visible in logs)
```

---

## NEXT STEPS

1. **Read the Audit Document:** `ACTIVE_VS_DEPRECATED_AUDIT.md`
2. **Review the Code:** `src/cofounder_agent/services/content_router_service.py`
3. **Run Integration Tests:** `npm run test:python`
4. **Make Changes:** Modify pipeline stages as needed
5. **Run Tests Again:** Verify nothing broke

---

**Document Version:** 1.0  
**Last Updated:** December 22, 2025  
**Related Files:**

- ACTIVE_VS_DEPRECATED_AUDIT.md - What code is used vs deprecated
- src/cofounder_agent/services/content_router_service.py - The pipeline code
- src/cofounder_agent/routes/content_routes.py - REST API entry point
