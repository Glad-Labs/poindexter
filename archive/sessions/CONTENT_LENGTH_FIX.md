# Content Length Fix - March 6, 2026

## Issues Fixed

### 1. Model Field Not Displaying ✅ FIXED

**Root Cause:** `ModelConverter.to_task_response()` was missing `model_used`, `models_used_by_phase`, and `model_selection_log` from its `normalized_fields` list.

**Impact:** These fields were stored in PostgreSQL but dropped when converting database rows to API responses.

**Fix:**

- Added three model tracking fields to `normalized_fields` in [model_converter.py](src/cofounder_agent/schemas/model_converter.py)
- Fields now properly serialized and returned in API responses
- Frontend can now display model information

**Files Changed:**

- `src/cofounder_agent/schemas/model_converter.py` (line ~168-172)

---

### 2. Content Too Short (500 words instead of 1500) ✅ FIXED

**Root Cause:** Validation was too lenient - content below target length only lost 2.0 points out of 10.0, passing the 7.0 quality threshold. No refinement was triggered for short content.

**Impact:** Blog posts generated at ~500 words when 1500+ was requested.

**Fixes Implemented:**

#### A. Stricter Validation Scoring

- **Very short content (<70% of target):** Now loses 5.0 points → FAILS quality check → refinement triggered
- **Moderately short (70-90% of target):** Loses 3.0 points → likely fails quality check
- **Previous behavior:** Only lost 2.0 points → passed quality check

**Changes:**

```python
# Before: -2.0 penalty for any short content
if word_count < min_words:
    score -= 2.0

# After: Graduated penalties based on severity
if word_count < target_length * 0.7:
    score -= 5.0  # Critical - will fail quality check
else:
    score -= 3.0  # Significant - likely to fail quality check
```

**Files Changed:**

- `src/cofounder_agent/services/ai_content_generator.py` (line ~122-150)

#### B. Increased Quality Threshold

- **Before:** 7.0/10.0 threshold allowed short content (8.0 score) to pass
- **After:** 7.5/10.0 threshold ensures length violations fail quality check

**Files Changed:**

- `src/cofounder_agent/services/ai_content_generator.py` (line ~53)

#### C. Strengthened Prompts

Enhanced both system and generation prompts to emphasize word count:

**System Prompt** (`blog_generation.blog_system_prompt`):

```
🎯 CRITICAL WORD COUNT REQUIREMENT: You MUST write EXACTLY {target_length} words (±10% tolerance).
   - Minimum acceptable: {min_words} words
   - Target: {target_length} words
   - Maximum: {max_words} words
   - Your response will be REJECTED if it falls outside this range.
```

**Generation Prompt** (`blog_generation.initial_draft`):

```
🎯 MANDATORY WORD COUNT: You MUST write {word_count} words (±10% acceptable range).
   Your content will be REJECTED if it's too short. Write detailed, thorough content.
```

Added emphasis:

- "MUST write" instead of "approximately"
- "will be REJECTED" for non-compliance
- "Write DETAILED content in each section - don't be brief!"
- "Write multiple paragraphs per section with concrete examples"

**Files Changed:**

- `src/cofounder_agent/services/prompt_manager.py` (line ~678-710, ~93-130)
- `src/cofounder_agent/services/ai_content_generator.py` (line ~322-340)

---

## Validation Flow Updated

### Before (Lenient):

1. Generate content
2. Check length: 499 words vs 1500 target
3. **Score: 8.0/10** (only -2.0 penalty)
4. **Passes 7.0 threshold** ✅
5. Content accepted without refinement

### After (Strict):

1. Generate content
2. Check length: 499 words vs 1500 target (< 70% threshold)
3. **Score: 5.0/10** (-5.0 penalty for critical shortness)
4. **Fails 7.5 threshold** ❌
5. **Refinement triggered** with strong feedback
6. Refined content must meet word count to pass

---

## Testing Instructions

### Test 1: Verify Model Field Display

1. Restart backend: `npm run dev:cofounder`
2. Open Oversight Hub: http://localhost:3001
3. Click on any completed task
4. Check "Model Used" field - should show actual model (e.g., "Google Gemini (gemini-2.5-flash)")
5. Open browser console - debug logs should show `model_used` present

### Test 2: Verify Content Length Enforcement

1. Create new blog post task via API or UI
2. Set target_length to 1500 words
3. Monitor backend logs for:
   - "Word count requirement: 1350-1650 words (target: 1500)"
   - "CRITICAL WORD COUNT REQUIREMENT" in prompts
4. After generation, check:
   - Word count should be 1350-1650 (90-110% of target)
   - If < 1050 words (70% threshold), quality score should be ≤ 5.0
   - Refinement should be triggered for short content

### Test 3: Run Debug Scripts

Use the debugging tools to inspect specific tasks:

```bash
# Check API response for specific task
python scripts/test-api-response.py <task_id>

# Check database directly
python scripts/debug-task-data.py <task_id>
```

---

## Expected Outcomes

### Immediate (After Restart):

- ✅ Model field displays in UI for all tasks
- ✅ API responses include `model_used`, `models_used_by_phase`, `model_selection_log`
- ✅ Console logs show model information

### For New Content Generation:

- ✅ Content meets 90-110% of target length
- ✅ Very short content (< 70%) triggers refinement
- ✅ Quality threshold enforces length requirements
- ✅ Prompts emphasize word count prominently

### Quality Improvements:

- More detailed, thorough content
- Better adherence to word count targets
- Automatic refinement for length violations
- Clearer feedback when content is rejected

---

## Rollback Instructions

If issues arise, revert these commits:

1. `model_converter.py` change (model field serialization)
2. `ai_content_generator.py` changes (validation and threshold)
3. `prompt_manager.py` changes (prompt strengthening)

Original validation logic:

```python
# Old validation: 2.0 penalty, 7.0 threshold
if word_count < min_words:
    score -= 2.0
threshold = 7.0
```

---

## Related Files

### Modified:

- `src/cofounder_agent/schemas/model_converter.py`
- `src/cofounder_agent/services/ai_content_generator.py`
- `src/cofounder_agent/services/prompt_manager.py`

### Created (Debugging Tools):

- `scripts/debug-task-data.py`
- `scripts/test-api-response.py`
- `DEBUG_GUIDE.md`

### Frontend (Already Updated):

- `web/oversight-hub/src/components/tasks/TaskMetadataDisplay.jsx`
  - Added model field display
  - Added debug console logging
  - Enhanced word count display with target/actual comparison
