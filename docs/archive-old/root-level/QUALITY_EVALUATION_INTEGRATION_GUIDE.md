# 🎯 Quality Evaluation Engine - Integration Guide

**Status:** ✅ Implementation Complete  
**Date:** December 6, 2025  
**Components:** 3 new services + 2 migrations

---

## 📦 What Was Created

### 1. Quality Evaluator Service

**File:** `src/cofounder_agent/services/quality_evaluator.py` (520+ lines)

**Features:**

- 7-criteria evaluation framework (each 0-10 scale)
  1. **Clarity** - Sentence structure, complexity, readability
  2. **Accuracy** - Citations, facts, vagueness indicators
  3. **Completeness** - Content length, sections, diversity
  4. **Relevance** - Topic focus, keyword coverage, coherence
  5. **SEO Quality** - Headers, keywords, links, optimization
  6. **Readability** - Grammar, active voice, formatting
  7. **Engagement** - Questions, emotion, CTAs, storytelling

- Pattern-based scoring (fast, deterministic)
- LLM-based scoring option (accurate, uses ModelRouter)
- Automatic feedback generation
- Improvement suggestions

**Key Classes:**

```python
QualityScore(dataclass)          # Result object
QualityEvaluator                 # Main evaluator service
get_quality_evaluator()          # Singleton factory
```

**Usage:**

```python
evaluator = await get_quality_evaluator(model_router=None)

result = await evaluator.evaluate(
    content="Generated content here",
    context={"topic": "AI", "target_keywords": ["machine learning"]},
    use_llm=False  # True to use LLM evaluation
)

# Access results
overall_score = result.overall_score  # 0-10
passing = result.passing             # True if >= 7.0
feedback = result.feedback           # Human-readable
suggestions = result.suggestions     # List of improvements
```

### 2. Quality Score Persistence Service

**File:** `src/cofounder_agent/services/quality_score_persistence.py` (350+ lines)

**Features:**

- Store evaluation results in PostgreSQL
- Track improvement through refinement cycles
- Generate daily quality metrics/trends
- Query evaluation history
- Analytics and reporting

**Key Methods:**

```python
# Store evaluation
await persistence.store_evaluation(
    content_id="post-123",
    quality_score=result,
    task_id="task-456",
    content_length=1500,
    context_data={"keywords": [...]}
)

# Track improvements
await persistence.store_improvement(
    content_id="post-123",
    initial_score=6.5,
    improved_score=8.2,
    best_improved_criterion="clarity"
)

# Query results
history = await persistence.get_evaluation_history("post-123")
summary = await persistence.get_content_quality_summary("post-123")
trends = await persistence.get_quality_trend(days=7)
metrics = await persistence.get_quality_metrics_for_date()
```

### 3. Database Migrations

**Files:**

- `migrations/002_quality_evaluation.sql` (150+ lines)

**New Tables:**

#### `quality_evaluations` (Primary scoring table)

```
Columns:
- id (Primary Key)
- content_id, task_id (references)
- overall_score, clarity, accuracy, completeness, relevance, seo_quality, readability, engagement (DECIMAL 0-10)
- passing (BOOLEAN, True if overall >= 7.0)
- feedback (TEXT)
- suggestions (JSONB array)
- evaluated_by, evaluation_method, evaluation_timestamp
- content_length, context_data
- refinement_count, is_final

Indexes: (5)
- content_id
- task_id
- passing
- overall_score DESC
- timestamp DESC
```

#### `quality_improvement_logs` (Refinement tracking)

```
Columns:
- id, content_id
- initial_score, improved_score, score_improvement
- best_improved_criterion
- refinement_type, changes_made
- passed_after_refinement
- refinement_timestamp

Indexes: (3)
- content_id
- timestamp DESC
- score_improvement DESC
```

#### `quality_metrics_daily` (Analytics)

```
Columns:
- date (UNIQUE)
- total_evaluations, passing_count, failing_count, pass_rate
- average_score
- score_range_0_3 through score_range_9_10 (distribution)
- avg_clarity, avg_accuracy, ..., avg_engagement
- total_refinements, avg_refinements_per_content
- total_improvement_points

Index: date DESC (for trending)
```

---

## 🚀 Integration Steps (In Progress)

### Step 1: Apply Migrations ✅

Run migrations on server startup to create tables:

```bash
# Automatically runs when server starts (via MigrationService in main.py)
python main.py
```

### Step 2: Import Services in Routes (NEXT)

Add to `routes/content_routes.py`:

```python
from services.quality_evaluator import get_quality_evaluator, QualityScore
from services.quality_score_persistence import get_quality_score_persistence
```

### Step 3: Auto-Evaluate Generated Content (NEXT)

After content is generated in routes, add evaluation:

```python
# In content generation endpoint (after content is created)
evaluator = await get_quality_evaluator(model_router)
quality_result = await evaluator.evaluate(
    content=generated_content,
    context={"topic": topic, "target_keywords": keywords},
    use_llm=False  # Fast pattern-based evaluation
)

# Persist to database
persistence = await get_quality_score_persistence(database_service)
await persistence.store_evaluation(
    content_id=content_id,
    quality_score=quality_result,
    task_id=task_id,
    content_length=len(generated_content.split())
)

# If not passing, trigger refinement (if score < 7.0)
if not quality_result.passing:
    # Call refinement agent with feedback
    ...
```

### Step 4: Add Evaluation Endpoints (NEXT)

Add to `routes/evaluation_routes.py` (NEW):

```python
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])

@router.post("/evaluate")
async def evaluate_content(
    content: str,
    task_id: Optional[str] = None,
    use_llm: bool = False
):
    """Manually evaluate content"""
    evaluator = await get_quality_evaluator()
    result = await evaluator.evaluate(content, use_llm=use_llm)
    return result.to_dict()

@router.get("/results/{content_id}")
async def get_evaluation_results(content_id: str):
    """Get evaluation history for content"""
    persistence = await get_quality_score_persistence()
    history = await persistence.get_evaluation_history(content_id)
    return {"content_id": content_id, "evaluations": history}

@router.get("/metrics/daily")
async def get_daily_metrics():
    """Get today's quality metrics"""
    persistence = await get_quality_score_persistence()
    metrics = await persistence.get_quality_metrics_for_date()
    return metrics

@router.get("/metrics/trend")
async def get_quality_trend(days: int = 7):
    """Get quality trend over N days"""
    persistence = await get_quality_score_persistence()
    trend = await persistence.get_quality_trend(days=days)
    return {"days": days, "metrics": trend}

@router.get("/summary/{content_id}")
async def get_quality_summary(content_id: str):
    """Get comprehensive quality summary"""
    persistence = await get_quality_score_persistence()
    summary = await persistence.get_content_quality_summary(content_id)
    return summary
```

---

## 📊 Quality Scoring Details

### 7-Criteria Framework

**1. Clarity (0-10)**

- Short, clear sentences = high score
- Avg sentence < 18 words = high score
- Complex jargon = low score
- Clarity words ("clearly", "specifically") = bonus

**2. Accuracy (0-10)**

- Citations/references = +2.0
- Specific numbers/statistics = +1.5
- Vague language ("maybe", "possibly") = -1.0
- Data-backed statements = high score

**3. Completeness (0-10)**

- 1500+ words = +3.0
- 1000-1500 words = +2.5
- Multiple sections/headers = +1.5
- Code, quotes, examples = diversity bonus

**4. Relevance (0-10)**

- Topic mentions throughout = +2.0
- Target keywords present = +2.5 max
- Coherent paragraphs = +1.0
- Stays on topic = high score

**5. SEO Quality (0-10)**

- H1 header present = +1.0
- 2+ H2 headers = +1.5
- 3+ H3 headers = +0.5
- Keyword density 1-5% = +1.5
- Links present = +1.0
- Image alt text = +0.5

**6. Readability (0-10)**

- Proper paragraph structure = +1.5
- Active voice preferred = +1.0
- Good formatting (bullets, bold) = +1.0
- Contractions present = +0.5
- Proper paragraph length (50-150 words) = high score

**7. Engagement (0-10)**

- 3+ questions = +2.0
- Emotional language = +1.5
- Call-to-action present = +1.5
- Quotes/examples/stories = +1.0
- Personal voice = high score

### Passing Criteria

- **Overall Score >= 7.0** = PASSING ✅
- **Overall Score < 7.0** = NEEDS REFINEMENT ⚠️

---

## 🔄 Automatic Refinement Loop (Future)

```
Content Generated (score 6.5)
    ↓
❌ Fails Quality Check (6.5 < 7.0)
    ↓
Refinement Needed: "Improve clarity and add more examples"
    ↓
Refinement Agent Called
    ↓
New Content Generated
    ↓
Re-evaluate (score 8.2)
    ↓
✅ Passes (8.2 >= 7.0)
    ↓
Store Both: Initial Score (6.5) + Improved Score (8.2)
    ↓
Publish Content
```

---

## 📈 Analytics & Reporting

### Daily Metrics Available

- Total evaluations
- Pass/fail counts and rates
- Average scores
- Score distribution (0-3, 3-5, 5-7, 7-9, 9-10)
- Criterion-specific averages
- Refinement statistics

### Query Examples

```python
# Get today's pass rate
metrics = await persistence.get_quality_metrics_for_date()
print(f"Pass rate: {metrics['pass_rate']}%")

# Get last 7 days of scores
trend = await persistence.get_quality_trend(days=7)
for day_metrics in trend:
    print(f"{day_metrics['date']}: avg={day_metrics['average_score']}")

# Get content quality summary
summary = await persistence.get_content_quality_summary("post-123")
print(f"Current: {summary['current_score']}, Passing: {summary['passing']}")
print(f"Improvements: {summary['improvement_count']}")
```

---

## 🧪 Testing the Evaluation Engine

### 1. Verify Migrations Ran

```bash
# Connect to PostgreSQL
psql glad_labs_dev

# Check tables created
\dt quality_*

# Expected output:
# - quality_evaluations
# - quality_improvement_logs
# - quality_metrics_daily
```

### 2. Test Evaluator Directly

```python
import asyncio
from services.quality_evaluator import QualityEvaluator

async def test():
    evaluator = QualityEvaluator()

    content = """
    # Introduction to Machine Learning

    Machine learning is a subset of artificial intelligence that enables
    systems to learn and improve from experience without being explicitly
    programmed. [source: Stanford AI Index 2023]

    There are three main types:
    - Supervised Learning: Learns from labeled data
    - Unsupervised Learning: Finds patterns in unlabeled data
    - Reinforcement Learning: Learns through trial and error

    Modern applications include recommendation systems, computer vision,
    natural language processing, and autonomous vehicles.
    """

    result = await evaluator.evaluate(
        content,
        context={"topic": "Machine Learning", "target_keywords": ["ML", "AI"]}
    )

    print(f"Overall: {result.overall_score}/10")
    print(f"Clarity: {result.clarity}/10")
    print(f"Passing: {result.passing}")
    print(f"Feedback: {result.feedback}")
    print(f"Suggestions: {result.suggestions}")

asyncio.run(test())
```

### 3. Test Persistence

```python
import asyncio
from services.quality_evaluator import QualityEvaluator, QualityScore
from services.quality_score_persistence import get_quality_score_persistence

async def test():
    # Create evaluator and get result
    evaluator = QualityEvaluator()
    result = await evaluator.evaluate("test content")

    # Get persistence service
    from services.database_service import DatabaseService
    db = DatabaseService()
    await db.initialize()
    persistence = await get_quality_score_persistence(db)

    # Store evaluation
    stored = await persistence.store_evaluation(
        content_id="test-123",
        quality_score=result
    )
    print(f"Stored: {stored}")

    # Retrieve evaluation
    summary = await persistence.get_content_quality_summary("test-123")
    print(f"Summary: {summary}")

    await db.close()

asyncio.run(test())
```

---

## 📋 Production Readiness Checklist

- [x] Quality evaluator service created (7-criteria, pattern + LLM)
- [x] Persistence layer created (storage, queries, analytics)
- [x] Database migrations created (3 tables, 8 indexes)
- [ ] Routes integrated (need to add to content_routes.py)
- [ ] Evaluation endpoints created (need new routes)
- [ ] Automatic refinement loop (next phase)
- [ ] End-to-end testing completed
- [ ] Performance optimization completed
- [ ] Documentation completed

---

## 🎯 Next Steps

**Today (30 min):**

1. ✅ Create quality_evaluator.py service - DONE
2. ✅ Create quality_score_persistence.py service - DONE
3. ✅ Create migrations - DONE
4. 🔄 Integrate into content_routes.py - NEXT
5. 🔄 Add evaluation API endpoints - NEXT

**Then (1-2 hours):** 6. Test entire evaluation pipeline 7. Verify auto-scoring works on content generation 8. Check database persistence 9. Test refinement triggers

**Finally:** 10. Create automatic refinement loop 11. Performance optimization 12. Documentation and deployment

---

## 📚 File Reference

| File                           | Purpose                    | Status           |
| ------------------------------ | -------------------------- | ---------------- |
| `quality_evaluator.py`         | 7-criteria evaluation      | ✅ Complete      |
| `quality_score_persistence.py` | Database storage & queries | ✅ Complete      |
| `002_quality_evaluation.sql`   | Database schema            | ✅ Complete      |
| `content_routes.py`            | Integration point          | 🔄 Next          |
| `evaluation_routes.py`         | API endpoints              | 🔄 Next          |
| `main.py`                      | Auto-run migrations        | ✅ Already ready |

---

**Status Summary:** 🎉 Evaluation engine fully implemented and ready for integration!
