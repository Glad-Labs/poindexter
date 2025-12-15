# Pipeline Failure Analysis & Fixes Applied

## Current Issues Observed

From user logs:

```
ERROR:root:Failed to decode JSON from local LLM response.
WARNING:services.content_orchestrator:⚠️  QA Rejected - Feedback: No feedback provided....
```

**Root Cause**: Ollama/llama2 is not reliably generating valid JSON responses for the QA prompt.

---

## Fixes Applied (Session Update)

### 1. **LLM Client Exception Handling** ✅

**File**: `src/agents/content_agent/services/llm_client.py` (Lines 75-105)

**Before**:

- JSON decode errors returned empty dict `{}`
- QA agent couldn't distinguish between "no JSON" and "valid empty response"

**After**:

- `_generate_json_local()` now raises `ValueError` exceptions on JSON decode failures
- Exceptions propagate to QA agent which catches them gracefully
- QA agent returns `(False, error_message)` instead of crashing

**Impact**: QA failures are now properly caught and handled, allowing pipeline to continue.

---

### 2. **QA Prompt Simplification** ✅

**File**: `src/agents/content_agent/prompts.json` (Line 7)

**Before**:

```
Complex prompt with markdown formatting and verbose examples
Likely caused llama2 to include formatting in JSON response
```

**After**:

```
Simplified prompt: "Review this blog post... Respond with ONLY this JSON format: {"approved":true/false,"feedback":"..."}"
```

**Impact**: Clearer instructions for llama2, more likely to produce valid JSON.

---

## Pipeline Behavior After Fixes

1. **QA Iteration 1**: LLM doesn't generate valid JSON
   - Exception raised in `_generate_json_local()`
   - Caught in QA agent
   - Returns `(False, "QA system encountered an error...")`
   - Orchestrator refines content and loops

2. **QA Iteration 2**: LLM still fails JSON
   - Same process repeats
   - Orchestrator logs: "⚠️ QA loop completed after 2 iterations"
   - **Continues pipeline** (doesn't fail)

3. **Image Selection**: Still runs
   - Uses fallback placeholder images if Pexels fails
   - Content moves forward

4. **Formatting & Approval Gate**: Completes
   - Task marked as `awaiting_approval`
   - Human can review and approve/reject

---

## Why Pipeline Still Works ✅

The key insight: **QA failures don't block the pipeline anymore.**

```python
# OLD: Exception would crash pipeline
# response_data = generate_json()  # Raises exception if JSON invalid
# pipeline STOPS ❌

# NEW: Exception caught, content continues
try:
    response_data = await self.llm_client.generate_json(prompt)
except Exception as e:
    logger.error(f"QAAgent: Failed to get JSON from LLM: {e}")
    return False, f"QA system encountered an error..."  # Returns False, not exception

# Orchestrator sees False, refines content, loops
# After 2 iterations, moves to awaiting_approval
# Human makes final decision ✅
```

---

## Remaining Issue: Ollama JSON Generation

**Problem**: llama2 through Ollama is not reliably generating JSON.

**Why This Happens**:

- llama2 is not trained as strongly for JSON generation as GPT models
- JSON with special characters (quotes, braces) is harder for non-expert LLMs
- Preamble/explanation text before JSON breaks JSON extraction

**Solutions Available**:

### Option 1: Switch LLM (Recommended for production)

- Use OpenAI GPT-4/4o for better JSON reliability
- Use Claude if available
- Use other instruction-following models

### Option 2: Simpler Prompt Engineering (Current approach)

- Further simplify QA prompt
- Add more JSON examples
- Use simpler schema (fewer fields)

### Option 3: Auto-Approve with Warnings (Pragmatic)

- On JSON failure, auto-approve content with feedback: "Unable to perform automated QA - manual review recommended"
- Human can still reject
- Pipeline continues reliably

### Option 4: Retry with Different Prompt

- On first JSON failure, try a simpler version of prompt
- Then fallback to auto-approve

---

## Recommended Next Steps

### Immediate (Today):

1. **Restart Backend**
   - Changes to llm_client.py and prompts.json need server restart
   - `python main.py` in src/cofounder_agent/

2. **Test Pipeline**
   - Generate new blog post
   - Monitor logs for QA handling
   - Verify it reaches `awaiting_approval` status
   - Test approval submission

### Short Term (This Week):

1. **Monitor QA Success Rate**
   - Track how often llama2 successfully generates valid JSON
   - If < 50% success: consider switching to Option 3 (auto-approve with warnings)

2. **Improve Prompt Further**
   - If success improves but still < 80%: try even simpler prompt
   - Add real-world JSON examples from your domain

3. **Add Monitoring Metrics**
   - Log QA success/failure rates
   - Track which prompts work best
   - Feed back to improve prompts

### Medium Term (Next Sprint):

1. **Switch LLM Provider** (if available)
   - Evaluate OpenAI or Claude for QA task
   - Much higher JSON reliability
   - May improve quality assessment significantly

2. **Implement Hybrid QA**
   - Use llama2 for content refinement (good at text generation)
   - Use GPT-4 for QA (good at structured JSON)
   - Optimize cost/quality

---

## Key Files Modified This Session

1. `src/agents/content_agent/services/llm_client.py`
   - Changed exception handling to raise instead of return `{}`

2. `src/agents/content_agent/prompts.json`
   - Simplified QA prompt for llama2

3. `src/agents/content_agent/agents/creative_agent.py`
   - Auto-inserts Markdown heading if missing (previous session)

4. `src/agents/content_agent/agents/postgres_image_agent.py`
   - Returns fallback placeholder image instead of None (previous session)

5. `src/cofounder_agent/routes/content_routes.py`
   - Added database verification for rejection (previous session)

---

## Testing Checklist

- [ ] Backend restarted with new code
- [ ] Generated new blog post
- [ ] Logged QA stage
- [ ] Verified task reaches `awaiting_approval`
- [ ] Submitted approval via frontend
- [ ] Checked task status updated to `approved`
- [ ] Verified content appears in CMS
- [ ] Tested rejection workflow
- [ ] Confirmed rejected content not published

---

## Success Criteria

✅ **Pipeline reaches `awaiting_approval` status** (even if QA fails)
✅ **Human approval/rejection works** (database persistence verified)
✅ **Approved content publishes to CMS**
✅ **Rejected content doesn't publish**
✅ **No unhandled exceptions in logs**
