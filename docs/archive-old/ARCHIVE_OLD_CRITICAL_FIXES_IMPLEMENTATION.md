# Critical Fixes Implementation Guide

**Priority: IMMEDIATE**  
**Estimated Effort: 10-15 hours**  
**Expected Impact: Full database population + working content pipeline**

---

## FIX 1: Default Author & Category Setup (30 minutes)

### Problem

Posts are created with `author_id=NULL` and `category_id=NULL`, breaking content discovery.

### Solution

**Step 1: Create Default Author**

```sql
INSERT INTO authors (id, name, slug, email, bio, avatar_url)
VALUES (
    uuid_generate_v4(),
    'Poindexter AI',
    'poindexter-ai',
    'poindexter@glad-labs.ai',
    'AI-powered content creator for Glad Labs. Generates, evaluates, and refines high-quality content across multiple formats.',
    'https://glad-labs.ai/poindexter-avatar.png'
)
ON CONFLICT (slug) DO NOTHING;
```

**Step 2: Ensure Categories Exist**

```sql
INSERT INTO categories (id, name, slug, description)
VALUES
    (uuid_generate_v4(), 'Technology', 'technology', 'AI, Software, Cloud, and technology trends'),
    (uuid_generate_v4(), 'Business', 'business', 'Business strategy, management, and operations'),
    (uuid_generate_v4(), 'Marketing', 'marketing', 'Marketing, growth, and customer acquisition'),
    (uuid_generate_v4(), 'Finance', 'finance', 'Financial analysis, investment, and accounting'),
    (uuid_generate_v4(), 'Entertainment', 'entertainment', 'Games, media, and entertainment')
ON CONFLICT (slug) DO NOTHING;
```

**Step 3: Update Existing Posts**

```sql
UPDATE posts
SET
    author_id = (SELECT id FROM authors WHERE slug = 'poindexter-ai' LIMIT 1),
    category_id = COALESCE(
        (SELECT id FROM categories WHERE slug ILIKE (
            CASE
                WHEN title ILIKE '%tech%' OR title ILIKE '%ai%' THEN 'technology'
                WHEN title ILIKE '%game%' THEN 'entertainment'
                WHEN title ILIKE '%business%' THEN 'business'
                ELSE 'technology'
            END
        ) LIMIT 1),
        (SELECT id FROM categories WHERE slug = 'technology' LIMIT 1)
    ),
    published_at = CASE WHEN status = 'published' THEN created_at ELSE NULL END
WHERE author_id IS NULL OR category_id IS NULL OR (status = 'published' AND published_at IS NULL);
```

**Result:** All posts now have proper author/category links and publication timestamps.

---

## FIX 2: Implement content_tasks Writing (3-4 hours)

### Problem

Blog post generation bypasses `content_tasks` table. Posts go directly to `posts` table, breaking the content pipeline workflow and QA process.

### Solution

**File to Modify:** `/src/cofounder_agent/services/content_router_service.py`

**Current Flow (Broken):**

```python
# Bad: Creates post directly, no QA
async def create_blog_post():
    post = await db.create_post({...})  # ❌ Direct to posts table
    return post
```

**Target Flow (Correct):**

```python
# Good: Flow through content_tasks → QA → posts
async def create_blog_post():
    # 1. Create content_task record
    task = await db.create_content_task({
        'task_id': str(uuid4()),
        'status': 'pending',
        'topic': topic,
        'content': None,  # Will be filled after generation
        'approval_status': 'pending'
    })

    # 2. Queue generation in background
    background_tasks.add_task(
        generate_and_evaluate_content,
        task_id=task['task_id']
    )

    # 3. Return immediately
    return task
```

### Implementation Steps

**Step 1: Add content_tasks writing methods to DatabaseService**

File: `/src/cofounder_agent/services/database_service.py`

Add these methods (after line ~500):

```python
async def create_content_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new content generation task"""
    task_id = task_data.get('task_id', str(uuid4()))

    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO content_tasks (
                task_id, request_type, task_type, status, topic, style, tone,
                target_length, approval_status, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
            RETURNING *
        """,
            task_id,
            task_data.get('request_type', 'content_generation'),
            task_data.get('task_type', 'blog_post'),
            task_data.get('status', 'pending'),
            task_data.get('topic'),
            task_data.get('style', 'technical'),
            task_data.get('tone', 'professional'),
            task_data.get('target_length', 1500),
            task_data.get('approval_status', 'pending')
        )
        return self._convert_row_to_dict(row)

async def update_content_task_status(
    self, task_id: str, status: str,
    content: Optional[str] = None,
    quality_score: Optional[int] = None,
    approval_status: Optional[str] = None
) -> Dict[str, Any]:
    """Update content task status and content"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE content_tasks
            SET
                status = COALESCE($2, status),
                content = COALESCE($3, content),
                quality_score = COALESCE($4, quality_score),
                approval_status = COALESCE($5, approval_status),
                updated_at = NOW(),
                completed_at = CASE
                    WHEN $2 IN ('completed', 'approved', 'rejected') THEN NOW()
                    ELSE completed_at
                END
            WHERE task_id = $1
            RETURNING *
        """,
            task_id, status, content, quality_score, approval_status
        )
        return self._convert_row_to_dict(row) if row else None

async def get_content_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
    """Get content task by ID"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM content_tasks WHERE task_id = $1",
            task_id
        )
        return self._convert_row_to_dict(row) if row else None
```

**Step 2: Modify content_router_service.py**

File: `/src/cofounder_agent/services/content_router_service.py`

Find the `process_content_generation_task` function (around line 200-300) and modify:

**BEFORE (Bad):**

```python
async def process_content_generation_task(task: CreateBlogPostRequest, ...):
    # Direct generation and saving
    content = await ai_content_generator.generate_blog_post(...)
    post = await database.create_post({
        'title': topic,
        'content': content,
        'status': 'draft'
    })
    return post
```

**AFTER (Correct):**

```python
async def process_content_generation_task(
    task: CreateBlogPostRequest,
    database_service: DatabaseService,
    background_tasks: BackgroundTasks,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Process content generation task through full pipeline:
    1. Create content_task record (status='pending')
    2. Queue generation + QA in background
    3. Return task record
    """
    from uuid import uuid4

    # 1. CREATE CONTENT TASK RECORD
    task_id = str(uuid4())
    content_task = await database_service.create_content_task({
        'task_id': task_id,
        'request_type': 'api_request',
        'task_type': 'blog_post',
        'status': 'pending',
        'topic': task.topic,
        'style': str(task.style.value),
        'tone': str(task.tone.value),
        'target_length': task.target_length,
        'approval_status': 'pending'
    })

    logger.info(f"✅ Created content_task: {task_id}")

    # 2. QUEUE BACKGROUND GENERATION + QA
    background_tasks.add_task(
        _generate_and_evaluate_content,
        task_id=task_id,
        task=task,
        database_service=database_service,
        user_id=user_id
    )

    # 3. RETURN TASK IMMEDIATELY
    return content_task


async def _generate_and_evaluate_content(
    task_id: str,
    task: CreateBlogPostRequest,
    database_service: DatabaseService,
    user_id: Optional[str] = None
):
    """Background task: Generate content → QA → Create post"""
    try:
        # STEP 1: GENERATE CONTENT
        logger.info(f"🔄 Generating content for task {task_id}...")
        content = await ai_content_generator.generate_blog_post({
            'topic': task.topic,
            'style': task.style,
            'tone': task.tone,
            'target_length': task.target_length,
            'tags': task.tags,
            'generate_featured_image': task.generate_featured_image
        })

        # Update task with generated content
        await database_service.update_content_task_status(
            task_id=task_id,
            status='generated',
            content=content.get('content')
        )
        logger.info(f"✅ Content generated for task {task_id}")

        # STEP 2: EVALUATE QUALITY
        logger.info(f"🔍 Evaluating quality for task {task_id}...")
        quality_result = await quality_evaluator.evaluate_content(
            content=content.get('content'),
            topic=task.topic,
            context={'tags': task.tags}
        )

        quality_score = quality_result['overall_score']
        passing = quality_result['passing']

        # Create quality evaluation record
        await database_service.create_quality_evaluation({
            'content_id': task_id,
            'task_id': task_id,
            'overall_score': quality_score,
            'clarity': quality_result['criteria']['clarity'],
            'accuracy': quality_result['criteria']['accuracy'],
            'completeness': quality_result['criteria']['completeness'],
            'relevance': quality_result['criteria']['relevance'],
            'seo_quality': quality_result['criteria']['seo_quality'],
            'readability': quality_result['criteria']['readability'],
            'engagement': quality_result['criteria']['engagement'],
            'passing': passing,
            'feedback': quality_result.get('feedback'),
            'suggestions': quality_result.get('suggestions', [])
        })

        if not passing:
            # STEP 3A: REFINE IF FAILING
            logger.info(f"⚠️ Content failed QA (score {quality_score:.1f}). Refining...")
            refined = await content_critique_loop.refine_content(
                content=content.get('content'),
                feedback=quality_result['feedback'],
                suggestions=quality_result['suggestions']
            )

            # Log improvement
            await database_service.create_quality_improvement_log({
                'content_id': task_id,
                'initial_score': quality_score,
                'improved_score': refined['score'],
                'refinement_type': 'auto-critique'
            })

            # Use refined content
            content['content'] = refined['content']
            quality_score = refined['score']
            passing = refined['passing']

        # STEP 3B: CREATE POST (if passing or forced)
        approval_status = 'approved' if passing else 'pending_manual'

        logger.info(f"📝 Creating post for task {task_id}...")

        # Get or create default author
        default_author = await database_service.get_user_by_username('poindexter-ai')
        if not default_author:
            # Fallback: get first author
            default_author_id = (await database_service.pool.acquire().__aenter__().fetchval(
                "SELECT id FROM authors LIMIT 1"
            ))
        else:
            default_author_id = default_author['id']

        # Create post
        post = await database_service.create_post({
            'title': task.topic,
            'slug': task.topic.lower().replace(' ', '-') + '-' + task_id[:8],
            'content': content.get('content'),
            'excerpt': content.get('excerpt', ''),
            'featured_image_url': content.get('featured_image_url'),
            'author_id': default_author_id,
            'category_id': await _select_category_for_topic(task.topic, database_service),
            'status': 'published' if task.publish_mode == PublishMode.PUBLISH else 'draft',
            'published_at': datetime.now(timezone.utc) if task.publish_mode == PublishMode.PUBLISH else None,
            'seo_title': content.get('seo_title'),
            'seo_description': content.get('seo_description'),
            'seo_keywords': content.get('seo_keywords'),
            'metadata': {
                'content_task_id': task_id,
                'quality_score': int(quality_score),
                'generated_by': 'poindexter_ai'
            }
        })

        # Update content_task final status
        await database_service.update_content_task_status(
            task_id=task_id,
            status='completed',
            approval_status=approval_status,
            quality_score=int(quality_score)
        )

        # STEP 4: CAPTURE TRAINING DATA (for learning loop)
        await database_service.create_orchestrator_training_data({
            'execution_id': task_id,
            'user_request': f"Generate {task.topic}",
            'intent': 'content_generation',
            'execution_result': 'success' if passing else 'completed_with_refinement',
            'quality_score': quality_score / 10,  # Convert to 0-1 scale
            'success': passing,
            'tags': task.tags or [],
            'source_agent': 'content_agent'
        })

        logger.info(f"✅ Content pipeline completed for task {task_id}")

    except Exception as e:
        logger.error(f"❌ Error processing content task {task_id}: {e}", exc_info=True)
        await database_service.update_content_task_status(
            task_id=task_id,
            status='failed',
            approval_status='failed'
        )


async def _select_category_for_topic(
    topic: str,
    database_service: DatabaseService
) -> Optional[str]:
    """Select appropriate category based on topic keywords"""
    topic_lower = topic.lower()

    category_keywords = {
        'technology': ['ai', 'tech', 'software', 'cloud', 'machine learning', 'data'],
        'business': ['business', 'strategy', 'management', 'entrepreneur', 'startup'],
        'marketing': ['marketing', 'seo', 'growth', 'brand', 'customer'],
        'finance': ['finance', 'investment', 'cost', 'budget', 'roi'],
        'entertainment': ['game', 'entertainment', 'media', 'streaming']
    }

    # Find best matching category
    matched_category = 'technology'  # Default
    for category, keywords in category_keywords.items():
        if any(kw in topic_lower for kw in keywords):
            matched_category = category
            break

    # Get category ID
    async with database_service.pool.acquire() as conn:
        cat_id = await conn.fetchval(
            "SELECT id FROM categories WHERE slug = $1",
            matched_category
        )

    return cat_id
```

---

## FIX 3: Enable Quality Evaluations (2-3 hours)

### Problem

All tasks have hardcoded `quality_score = 75`. Quality scoring is disabled.

### Solution

**File:** `/src/cofounder_agent/services/quality_evaluator.py`

Verify this method exists and is being called:

```python
async def evaluate_content(
    self,
    content: str,
    topic: str,
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Evaluate content on 7 criteria:
    1. Clarity (0-10) - How clear and easy to understand
    2. Accuracy (0-10) - Factual correctness
    3. Completeness (0-10) - Covers topic thoroughly
    4. Relevance (0-10) - Matches topic and audience
    5. SEO Quality (0-10) - Keyword usage, structure
    6. Readability (0-10) - Grammar, flow, structure
    7. Engagement (0-10) - Interest level for audience

    Returns: {
        'overall_score': average of 7 criteria (0-10),
        'passing': overall_score >= 7.0,
        'criteria': {clarity, accuracy, ...},
        'feedback': string explanation,
        'suggestions': [list of improvement suggestions]
    }
    """
    # Implementation should use either:
    # A) Pattern-based scoring (fast, good enough for MVP)
    # B) LLM-based scoring (slow but most accurate)
```

**Verify it's called in content_router_service.py:**

```python
# In _generate_and_evaluate_content function
quality_result = await quality_evaluator.evaluate_content(
    content=content.get('content'),
    topic=task.topic,
    context={'tags': task.tags}
)
```

---

## FIX 4: Add Missing Database Methods (1-2 hours)

**File:** `/src/cofounder_agent/services/database_service.py`

Add these methods for full pipeline support:

```python
async def create_quality_evaluation(self, eval_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create quality evaluation record"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO quality_evaluations (
                content_id, task_id, overall_score, clarity, accuracy,
                completeness, relevance, seo_quality, readability, engagement,
                passing, feedback, suggestions, evaluated_by, evaluation_method,
                evaluation_timestamp
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, NOW())
            RETURNING *
        """,
            eval_data['content_id'],
            eval_data.get('task_id'),
            eval_data['overall_score'],
            eval_data['criteria']['clarity'],
            eval_data['criteria']['accuracy'],
            eval_data['criteria']['completeness'],
            eval_data['criteria']['relevance'],
            eval_data['criteria']['seo_quality'],
            eval_data['criteria']['readability'],
            eval_data['criteria']['engagement'],
            eval_data['passing'],
            eval_data.get('feedback'),
            json.dumps(eval_data.get('suggestions', [])),
            eval_data.get('evaluated_by', 'QualityEvaluator'),
            eval_data.get('evaluation_method', 'pattern-based')
        )
        return self._convert_row_to_dict(row)

async def create_quality_improvement_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
    """Log content quality improvement through refinement"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO quality_improvement_logs (
                content_id, initial_score, improved_score, score_improvement,
                refinement_type, changes_made, refinement_timestamp, passed_after_refinement
            )
            VALUES ($1, $2, $3, $3 - $2, $4, $5, NOW(), $3 >= 7.0)
            RETURNING *
        """,
            log_data['content_id'],
            log_data['initial_score'],
            log_data['improved_score'],
            log_data.get('refinement_type'),
            log_data.get('changes_made')
        )
        return self._convert_row_to_dict(row)

async def create_orchestrator_training_data(self, train_data: Dict[str, Any]) -> Dict[str, Any]:
    """Capture execution for training/learning"""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO orchestrator_training_data (
                execution_id, user_request, intent, business_state, execution_result,
                quality_score, success, tags, created_at, source_agent
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), $9)
            RETURNING *
        """,
            train_data['execution_id'],
            train_data['user_request'],
            train_data.get('intent'),
            json.dumps(train_data.get('business_state', {})),
            train_data.get('execution_result'),
            train_data['quality_score'],
            train_data['success'],
            train_data.get('tags', []),
            train_data.get('source_agent')
        )
        return self._convert_row_to_dict(row)

async def create_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create blog post - ensure author_id and category_id are set"""
    post_id = post_data.get('id') or str(uuid4())

    async with self.pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO posts (
                id, title, slug, content, excerpt, featured_image_url,
                author_id, category_id, status, published_at, seo_title,
                seo_description, seo_keywords, metadata, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, NOW(), NOW())
            RETURNING *
        """,
            post_id,
            post_data['title'],
            post_data['slug'],
            post_data['content'],
            post_data.get('excerpt'),
            post_data.get('featured_image_url'),
            post_data.get('author_id'),
            post_data.get('category_id'),
            post_data.get('status', 'draft'),
            post_data.get('published_at'),
            post_data.get('seo_title'),
            post_data.get('seo_description'),
            post_data.get('seo_keywords'),
            json.dumps(post_data.get('metadata', {}))
        )
        return self._convert_row_to_dict(row)
```

---

## Testing Checklist

After implementing all fixes:

- [ ] Run: `python -c "from services.database_service import DatabaseService; print('✅ Imports work')"
- [ ] Manually create a blog post via `/api/content/tasks` POST
- [ ] Verify `content_tasks` record created (status='pending')
- [ ] Wait for background processing
- [ ] Verify `quality_evaluations` record created
- [ ] Verify `posts` record created with author_id + category_id
- [ ] Verify `orchestrator_training_data` record created
- [ ] Check frontend:
  - [ ] Execution Hub shows task in Command Queue
  - [ ] Tasks page shows quality_score
  - [ ] Content Library shows post with author and category
  - [ ] Post published_at is set correctly

---

## Rollback Plan (If Issues)

If something breaks:

```bash
# Revert database changes
psql glad_labs_dev -f /path/to/backup.sql

# Revert code
git checkout src/cofounder_agent/services/content_router_service.py
git checkout src/cofounder_agent/services/database_service.py

# Restart app
python main.py
```

---

## Success Criteria

After all fixes:

- ✅ **content_tasks:** 90 records (matches task count)
- ✅ **quality_evaluations:** 85+ records with real scores
- ✅ **posts:** 10+ with author_id + category_id filled
- ✅ **orchestrator_training_data:** 50+ records
- ✅ **Frontend:** All pages show correct data

---

_Estimated Implementation Time: 10-15 hours_  
_Complexity: Medium-High_  
_Risk: Low (database migrations are safe, feature additions)_
