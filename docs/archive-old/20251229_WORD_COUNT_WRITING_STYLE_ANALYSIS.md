# Word Count & Writing Style Parameter Analysis

**Date:** December 23, 2025  
**Status:** ⚠️ PARTIALLY IMPLEMENTED

## Summary

The frontend is collecting and sending `word_count` and `writing_style` (called `style`) parameters, but the **FastAPI backend is NOT actively using them** during content generation. These parameters are stored in task metadata but don't influence the content pipeline.

---

## Current Implementation

### Frontend (Implemented ✅)

**File:** [web/oversight-hub/src/components/tasks/CreateTaskModal.jsx](web/oversight-hub/src/components/tasks/CreateTaskModal.jsx)

The form collects these parameters:

```javascript
taskTypes: {
  blog_post: {
    fields: [
      {
        name: 'word_count',
        label: 'Word Count',
        type: 'number',
        defaultValue: 1500,
      },
      {
        name: 'style',
        label: 'Writing Style',
        type: 'select',
        options: [
          'technical',
          'narrative',
          'listicle',
          'educational',
          'thought-leadership',
        ],
      },
    ];
  }
}
```

**Sent in task payload (lines 318-326):**

```javascript
metadata: {
  task_type: 'blog_post',
  style: formData.style || 'technical',           // ✅ Collected
  tone: formData.tone || 'professional',
  word_count: formData.word_count || 1500,        // ✅ Collected
  tags: [...],
  generate_featured_image: true,
  publish_mode: 'draft',
}
```

### Backend (NOT IMPLEMENTED ❌)

**File:** [src/cofounder_agent/schemas/task_schemas.py](src/cofounder_agent/schemas/task_schemas.py)

The `TaskCreateRequest` model does NOT include `word_count` or `writing_style` fields:

```python
class TaskCreateRequest(BaseModel):
    task_name: str                          # ✅ Defined
    topic: str                              # ✅ Defined
    primary_keyword: str                    # ✅ Defined
    target_audience: str                    # ✅ Defined
    category: str                           # ✅ Defined
    model_selections: Optional[Dict]        # ✅ Defined
    quality_preference: Optional[str]       # ✅ Defined
    estimated_cost: Optional[float]         # ✅ Defined
    metadata: Optional[Dict[str, Any]]      # ✅ Generic catch-all (where word_count goes)

    # ❌ NO word_count or writing_style fields
    # ❌ NO dedicated fields for content constraints
```

### Where They Go (But Aren't Used)

1. **Stored but ignored in metadata:** Parameters end up in the generic `metadata` dict but aren't extracted
2. **No orchestration logic:** The Content Agent doesn't read `word_count` or `style` when generating content
3. **No prompt injection:** These aren't passed to the LLM as constraints

**Evidence:**

- grep found `word_count` in tests (test_seo_content_generator.py) but NOT in actual agent code
- grep found references in oversight hub (OrchestratorResultMessage.jsx) but only for DISPLAY, not generation
- Python tests mock `word_count=1500` but don't verify the output respects it

---

## What Would Be Needed to Implement

### Option 1: Add as First-Class Fields (Recommended)

**1. Update the Schema**

```python
class TaskCreateRequest(BaseModel):
    # ... existing fields ...
    word_count: Optional[int] = Field(
        default=1500,
        ge=300,
        le=5000,
        description="Target word count for content (300-5000 words)"
    )
    writing_style: Optional[str] = Field(
        default="technical",
        pattern="^(technical|narrative|listicle|educational|thought-leadership)$",
        description="Content writing style preference"
    )
```

**2. Pass to Content Agent**

Update [src/agents/content_agent/orchestrator.py](src/agents/content_agent/orchestrator.py):

```python
async def generate_content(self, task: TaskCreateRequest):
    # Extract constraints
    target_words = task.word_count or 1500
    style = task.writing_style or 'technical'

    # Pass to each phase
    await self.research_phase(
        topic=task.topic,
        target_words=target_words,
        style=style
    )
```

**3. Inject into Prompts**

Modify [src/agents/content_agent/phases/creative_phase.py](src/agents/content_agent/phases/creative_phase.py):

```python
system_prompt = f"""
You are a content writer. Generate a blog post about: {topic}

CONSTRAINTS:
- Word count: Target {target_words} words (±10% acceptable)
- Writing style: {style}
- Tone: {tone}
- Include relevant examples and data

Output should be exactly {target_words} words.
"""
```

**4. Validate Output**

```python
actual_words = len(content.split())
if actual_words < target_words * 0.9:
    # Trigger expand phase
    content = await expand_content(content, target_words)
elif actual_words > target_words * 1.1:
    # Trigger trim phase
    content = await trim_content(content, target_words)
```

### Option 2: Use Metadata (Minimal Change)

Extract from existing `metadata` dict:

```python
word_count = task.metadata.get('word_count', 1500)
writing_style = task.metadata.get('style', 'technical')
```

More fragile but requires no schema changes.

---

## Impact Assessment

### Current Status

- **User Experience:** Users think they're controlling word count and style, but they're not
- **Expected Behavior:** Content should be generated with specified constraints
- **Actual Behavior:** Parameters are collected but ignored

### If Implemented

- **Content Quality:** More consistent output matching user expectations
- **SEO:** Word count targets would match SEO strategy
- **Brand Voice:** Writing style consistency across content
- **Cost Control:** Shorter target words = fewer tokens = lower cost

---

## Test Coverage

**Currently Testing:**

- ✅ Frontend form collection (CreateTaskModal.jsx)
- ✅ Schema definition (TaskCreateRequest)
- ✅ Metadata storage

**NOT Testing:**

- ❌ Actual constraint enforcement in generation
- ❌ Word count validation on output
- ❌ Style consistency in generated content
- ❌ E2E flow from task creation → content generation with constraints

---

## Recommendation

**Priority: MEDIUM** - Implement Option 1 (First-Class Fields)

1. **Add fields to TaskCreateRequest** (5 min)
2. **Extract in orchestrator** (10 min)
3. **Inject into prompts** (20 min)
4. **Add validation** (15 min)
5. **Write tests** (20 min)

**Estimated Effort:** ~1 hour for full implementation

Would you like me to implement this?
