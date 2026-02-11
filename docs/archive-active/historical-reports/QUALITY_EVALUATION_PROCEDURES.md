# Quality Evaluation Procedures - Complete Guide

**Last Updated:** February 4, 2026  
**Status:** ✅ Fully Standardized to 0-100 Scale  
**Version:** 2.1 (Standardized Evaluation)

## Executive Summary

Your FastAPI quality evaluation system has been fully standardized to use a **0-100 scale** throughout all components:

- ✅ **Backend Service**: `UnifiedQualityService` returns 0-100 scores
- ✅ **Database Models**: All constraints updated to `le=100.0` (was 10.0)
- ✅ **Frontend Display**: Shows raw 0-100 values (no conversion)
- ✅ **Passing Threshold**: 70/100 (equivalent to 7/10)
- ✅ **API Responses**: Quality scores on 0-100 scale

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Content Generation Task                   │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│              UnifiedQualityService.evaluate()                 │
│  (src/cofounder_agent/services/quality_service.py)            │
│                                                                 │
│  Methods:                                                      │
│  • evaluate(content, context, method) → QualityAssessment     │
│  • _evaluate_pattern_based() → Fast heuristic scoring         │
│  • _evaluate_llm_based() → LLM-based evaluation (fallback)    │
│  • _evaluate_hybrid() → Combined pattern + LLM                │
└────────────────┬────────────────────────────────────────────┘
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
  Clarity    Accuracy   Completeness    (0-10 internally)
  ×10        ×10        ×10             (multiplied to 0-100)
     │           │           │
     └───────────┼───────────┘
                 ▼
        Overall Score (0-100)
                 │
                 ▼
     ┌─────────────────────────┐
     │   QualityAssessment     │
     │  (Dataclass returned)   │
     │  • overall_score (0-100)│
     │  • dimensions (0-100)   │
     │  • passing (bool)       │
     │  • feedback (string)    │
     │  • suggestions (list)   │
     └────────────┬────────────┘
                  │
        ┌─────────┴──────────┐
        ▼                    ▼
   Database Store    Frontend Display
   (PostgreSQL)      (React Component)
   0-100 scale       0-100 scale
```

---

## Evaluation Methods

### 1. Pattern-Based Evaluation (DEFAULT - RECOMMENDED)

**Speed:** ⚡ ~50ms  
**Cost:** 💚 Free (no API calls)  
**Accuracy:** 📊 Good for most content

**Scoring Logic (0-100 scale):**

```python
dimensions = QualityDimensions(
    clarity=clarity_score * 10,           # 0-10 → 0-100
    accuracy=accuracy_score * 10,         # Word count, citations
    completeness=completeness_score * 10, # 500-2000 words
    relevance=relevance_score * 10,       # Topic density
    seo_quality=seo_score * 10,          # Headers, structure
    readability=readability_score * 10,   # Flesch-Kincaid
    engagement=engagement_score * 10,     # Questions, bullets
)
overall_score = dimensions.average()
```

**7-Criteria Scoring Details:**

| Criterion        | Scorer Method           | Heuristic                           | Range   |
| ---------------- | ----------------------- | ----------------------------------- | ------- |
| **Clarity**      | `_score_clarity()`      | Sentence length (15-20 words ideal) | 5-9     |
| **Accuracy**     | `_score_accuracy()`     | Citations, quotes present           | 6.5-7.5 |
| **Completeness** | `_score_completeness()` | Word count (500-2000+)              | 5-9     |
| **Relevance**    | `_score_relevance()`    | Topic keyword density (1-3%)        | 3-9     |
| **SEO Quality**  | `_score_seo()`          | Headers, structure, links           | 4-9     |
| **Readability**  | `_score_readability()`  | Flesch-Kincaid grade level          | 4-9     |
| **Engagement**   | `_score_engagement()`   | Bullets, questions, variety         | 5-10    |

**Example Scoring (900-word tech article):**

```
clarity:         7.5 × 10 = 75    (18 words/sentence)
accuracy:        7.0 × 10 = 70    (has citations)
completeness:    8.0 × 10 = 80    (900 words)
relevance:       7.5 × 10 = 75    (keyword density 1.5%)
seo_quality:     7.0 × 10 = 70    (headers present)
readability:     8.0 × 10 = 80    (Grade 8 level)
engagement:      7.5 × 10 = 75    (bullet points)
────────────────────────────────
Overall Score:   75.64 / 100      ✅ PASS (≥70)
```

### 2. LLM-Based Evaluation

**Speed:** 🐌 1-5 seconds (API dependent)  
**Cost:** 💰 $0.01-0.50 per evaluation  
**Accuracy:** 🎯 High for nuanced assessment

**Current Implementation:** Delegates to pattern-based (fallback to pattern if no model_router available)

**Available Models:**

- Anthropic Claude (recommended)
- OpenAI GPT-4 (accurate but expensive)
- Google Gemini (cost-effective)
- Ollama (free local option)

### 3. Hybrid Evaluation

**Speed:** ⏱️ 1-5 seconds  
**Cost:** 💰 Variable (pattern + LLM)  
**Accuracy:** 🏆 Excellent (combines both)

**Current Implementation:**

```python
async def _evaluate_hybrid(self, content, context):
    # Get pattern-based assessment (fast)
    pattern_assessment = await self._evaluate_pattern_based(content, context)

    # Get LLM assessment if available (accurate)
    if self.model_router:
        llm_assessment = await self._evaluate_llm_based(content, context)
        # Combine results (equal weight currently)

    return pattern_assessment  # Can enhance with LLM later
```

---

## Quality Thresholds & Feedback

### Scoring Scale (0-100)

| Score      | Category   | Feedback                        | Color     |
| ---------- | ---------- | ------------------------------- | --------- |
| **90-100** | Excellent  | Publication ready               | 🟢 Green  |
| **75-89**  | Good       | Minor improvements recommended  | 🔵 Blue   |
| **70-74**  | Acceptable | Some improvements suggested     | 🟡 Yellow |
| **60-69**  | Fair       | Significant improvements needed | 🟠 Orange |
| **0-59**   | Poor       | Major revisions required        | 🔴 Red    |

### Passing Threshold

- **Minimum Score:** 70/100
- **Equivalent to:** 7/10 (old scale)
- **Interpretation:** Content is acceptable for publication with optional refinements

```python
passing = overall_score >= 70  # 70+ = PASS
```

### Feedback Generation

```python
def _generate_feedback(self, dimensions, context):
    overall = dimensions.average()

    if overall >= 85:
        return "Excellent content quality - publication ready"
    if overall >= 75:
        return "Good quality - minor improvements recommended"
    if overall >= 70:
        return "Acceptable quality - some improvements suggested"
    if overall >= 60:
        return "Fair quality - significant improvements needed"
    return "Poor quality - major revisions required"
```

### Improvement Suggestions

Automatically generated when dimensions are below 70:

```python
suggestions = []
threshold = 70

if dimensions.clarity < threshold:
    suggestions.append("Simplify sentence structure and use shorter sentences")
if dimensions.accuracy < threshold:
    suggestions.append("Fact-check claims and add citations where appropriate")
if dimensions.completeness < threshold:
    suggestions.append("Add more detail and cover the topic more thoroughly")
# ... etc
```

---

## API Integration

### Endpoint: POST /api/quality/evaluate

**Request:**

```json
{
  "content": "Your content here...",
  "topic": "AI in Healthcare",
  "keywords": ["AI", "healthcare", "machine learning"],
  "method": "pattern-based"
}
```

**Response (0-100 scale):**

```json
{
  "overall_score": 82,
  "passing": true,
  "dimensions": {
    "clarity": 85,
    "accuracy": 80,
    "completeness": 85,
    "relevance": 80,
    "seo_quality": 75,
    "readability": 85,
    "engagement": 80
  },
  "feedback": "Good quality - minor improvements recommended",
  "suggestions": [
    "Add more specific examples",
    "Improve SEO keyword distribution"
  ],
  "evaluation_method": "pattern-based",
  "content_length": 1200,
  "word_count": 180
}
```

### Database Schema

**Table:** `quality_evaluations`

```sql
-- All scores stored on 0-100 scale
overall_score     DECIMAL(5,2)    -- 0-100
clarity           DECIMAL(5,2)    -- 0-100
accuracy          DECIMAL(5,2)    -- 0-100
completeness      DECIMAL(5,2)    -- 0-100
relevance         DECIMAL(5,2)    -- 0-100
seo_quality       DECIMAL(5,2)    -- 0-100
readability       DECIMAL(5,2)    -- 0-100
engagement        DECIMAL(5,2)    -- 0-100
passing           BOOLEAN         -- overall_score >= 70
```

---

## Frontend Display

### TaskDataFormatter.js

```javascript
// Format task with quality score (0-100)
export const formatTaskData = (task) => {
  return {
    // ...
    qualityScore: task.quality_score || 0, // Raw 0-100
    qualityBadge: getQualityBadge(task.quality_score),
  };
};

// Quality badge with thresholds
export const getQualityBadge = (score) => {
  const numScore = parseFloat(score) || 0;

  if (numScore >= 90) return { label: 'Excellent', color: '#059669' };
  if (numScore >= 75) return { label: 'Good', color: '#0891b2' };
  if (numScore >= 60) return { label: 'Fair', color: '#d97706' };
  return { label: 'Poor', color: '#dc2626' };
};
```

### MessageFormatters.js

```javascript
// Format quality score for display
export const formatQualityScore = (score) => {
  if (typeof score !== 'number') return 'N/A';
  const normalized = score > 1 ? score : score * 100;
  return `${Math.round(normalized)}/100`; // e.g., "82/100"
};
```

---

## Recent Fixes & Standardization

### Changes Applied (Feb 4, 2026)

| File                          | Change                          | Reason                             |
| ----------------------------- | ------------------------------- | ---------------------------------- |
| `quality_service.py`          | QualityDimensions all 0-100     | Consistent internal representation |
| `quality_service.py`          | QualityAssessment passing >= 70 | Standard threshold                 |
| `unified_orchestrator.py`     | Removed 100x multiplication     | Service now returns 0-100          |
| `database_response_models.py` | Field constraints le=100.0      | Accept full 0-100 range            |
| `content_db.py`               | Passing check >= 70             | Match backend threshold            |
| `MessageFormatters.js`        | Display X/100 format            | User preference                    |
| `taskDataFormatter.js`        | Raw 0-100 display               | No conversion needed               |
| `test_subtask_endpoints.py`   | Assert <= 100                   | Updated test expectations          |

---

## Common Scenarios & Expected Scores

### Scenario 1: Quick News Update (300 words)

```
Input: Brief news article about AI regulation update

Expected Results:
• clarity: 85    (short, clear sentences)
• accuracy: 75   (no citations - news update)
• completeness: 55   (too short, lacks depth)
• relevance: 80  (on-topic throughout)
• seo_quality: 70    (lacks headers)
• readability: 85    (easy to read)
• engagement: 70     (minimal interactive elements)
────────────────────────────────────────
• overall_score: 71  ✅ PASS
• Status: Acceptable - add more detail for higher score
```

### Scenario 2: In-Depth Blog Post (1,500 words)

```
Input: Comprehensive article with headers, examples, citations

Expected Results:
• clarity: 80    (mix of sentence lengths)
• accuracy: 85   (citations and quotes present)
• completeness: 90   (comprehensive coverage)
• relevance: 85  (well-focused content)
• seo_quality: 85    (headers, structure)
• readability: 85    (good flow, varied paragraphs)
• engagement: 85     (questions, lists, variety)
────────────────────────────────────────
• overall_score: 84  ✅ PASS
• Status: Good quality - publication ready
```

### Scenario 3: AI-Generated Content (Draft)

```
Input: Generated content with repetitive structure, no citations

Expected Results:
• clarity: 70    (simple but repetitive)
• accuracy: 55   (no citations or validation)
• completeness: 75   (decent length)
• relevance: 60  (some off-topic sections)
• seo_quality: 65    (basic structure)
• readability: 75    (clear but boring)
• engagement: 55     (minimal interaction)
────────────────────────────────────────
• overall_score: 65  ❌ FAIL
• Status: Fair quality - needs significant refinement
• Suggestions:
  - Add citations and fact-checks
  - Improve topic focus
  - Add interactive elements
  - Enhance readability with varied structure
```

---

## Troubleshooting

### Issue: Service Returns Scores on 0-10 Scale

**Root Cause:** Scores multiplied by 100 somewhere in pipeline  
**Solution:** Check unified_orchestrator.py line 710 - should use raw score, not `score * 100`

### Issue: Scores Show as X/5 in UI

**Root Cause:** Frontend formatter converting 0-100 to 0-5  
**Solution:** Update MessageFormatters.js to display raw 0-100 (already fixed)

### Issue: Database Rejects Scores > 10

**Root Cause:** Old schema constraints `le=10.0` still in place  
**Solution:** Update response model field constraints to `le=100.0` (already fixed)

### Issue: Passing Threshold Wrong

**Root Cause:** Code checking `>= 7.0` instead of `>= 70`  
**Solution:** Update all threshold comparisons (already fixed in quality_service.py and content_db.py)

---

## Performance Considerations

### Evaluation Speed

```
Pattern-Based:   ~50ms    (7 criteria heuristics)
LLM-Based:       1-5s     (API dependent)
Hybrid:          1-5s     (pattern + LLM)
```

### Cost per Evaluation

```
Pattern-Based:   Free        (0 API calls)
LLM-Based:       $0.01-0.50  (1 API call)
Hybrid:          $0.01-0.50  (1-2 API calls)
```

### Batch Evaluation

For bulk content assessment:

```python
# Process 100 articles
async def evaluate_batch(articles):
    tasks = [
        quality_service.evaluate(article)
        for article in articles
    ]
    results = await asyncio.gather(*tasks)
    return results

# Cost: 100 × $0.01-0.50 = $1-50 for LLM-based
# Time: ~1-5 seconds (parallel) vs 5-500ms each
```

---

## Best Practices

1. **Use Pattern-Based by Default**
   - Fast, free, good accuracy
   - Only use LLM for edge cases or high-stakes content

2. **Monitor Score Distribution**
   - Track average scores over time
   - Identify improvement opportunities
   - Use metrics dashboard for trends

3. **Combine with Human Review**
   - Quality scores are heuristics, not absolute truth
   - Always have editorial review for important content
   - Use scores to flag content for human review

4. **Adjust Thresholds as Needed**
   - Default 70/100 passing threshold
   - Can customize by content type
   - Document any changes for consistency

5. **Use Suggestions for Improvement**
   - Automatically generated based on low dimensions
   - Provide actionable feedback to content creators
   - Track improvement over time

---

## References

- Backend Service: [quality_service.py](../src/cofounder_agent/services/quality_service.py)
- Database Layer: [content_db.py](../src/cofounder_agent/services/content_db.py)
- API Schemas: [quality_schemas.py](../src/cofounder_agent/schemas/quality_schemas.py)
- Response Models: [database_response_models.py](../src/cofounder_agent/schemas/database_response_models.py)
- Frontend Formatters: [taskDataFormatter.js](../web/oversight-hub/src/utils/taskDataFormatter.js)
- Frontend Messages: [MessageFormatters.js](../web/oversight-hub/src/utils/MessageFormatters.js)

---

## Version History

| Date         | Version | Changes                                           |
| ------------ | ------- | ------------------------------------------------- |
| Feb 4, 2026  | 2.1     | ✅ Standardized 0-100 scale across all components |
| Jan 21, 2026 | 2.0     | Introduced unified quality service                |
| Earlier      | 1.0     | Initial pattern-based evaluation                  |
