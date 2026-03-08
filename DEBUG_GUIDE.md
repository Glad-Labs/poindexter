# Debugging Task Data Issues

Quick guide for debugging model_used and content length issues.

## Issue Summary

1. **Model field showing "Not specified"** - Frontend can't find model_used in task data
2. **Content too short** - Blog posts ~500 words instead of target (e.g., 1500 words)

## Debugging Steps

### Step 1: Check Browser Console (Frontend)

1. Open Developer Tools (F12)
2. Go to **Console** tab
3. Click on a task with the issue
4. Look for **"🐛 TaskMetadataDisplay Debug"** group
5. Check what data is present:
   - `model_used (top-level)`: Should show the model name
   - `target_length (top-level)`: Should show target word count
   - `parsedResult.content`: Should have the full blog post text

**What to look for:**

- ✅ If data is present: Frontend parsing issue (check display logic)
- ❌ If data is missing: Backend or database issue (continue to Step 2)

---

### Step 2: Check API Response (Backend)

Test the API endpoint directly:

```bash
# Replace <task_id> with actual task ID from screenshot
python scripts/test-api-response.py <task_id>
```

**Example:**

```bash
python scripts/test-api-response.py 550e8400-e29b-41d4-a716-446655440000
```

**What to look for:**

- Check if `model_used` is present in API response
- Check if `target_length` is present
- Compare actual vs target word count
- Full response saved to `scripts/api_response_<id>.json`

**Diagnosis:**

- ✅ If present in API but not frontend: Data serialization issue
- ❌ If missing from API: Database or query issue (continue to Step 3)

---

### Step 3: Check Database (PostgreSQL)

Query the database directly:

```bash
# Replace <task_id> with actual task ID
python scripts/debug-task-data.py <task_id>
```

**Example:**

```bash
python scripts/debug-task-data.py 550e8400-e29b-41d4-a716-446655440000
```

**What to look for:**

- Is `model_used` NULL in database?
- Is `target_length` stored correctly?
- Compare database stored content length vs target
- Check `models_used_by_phase` and `model_selection_log`

**Diagnosis:**

- ❌ If `model_used` is NULL: Generation pipeline isn't storing the model
- ⚠️ If content length < 90% of target: Generation pipeline not respecting target_length

---

### Step 4: Get Task ID from UI

To get a task ID for testing:

1. Open Oversight Hub (http://localhost:3001)
2. Go to Tasks page
3. Click on a task with the issue
4. Look at the URL or modal title - task ID will be shown
5. OR in browser console, the debug logs show `task.id`

---

## Expected Outcomes

### Scenario A: model_used is NULL in database

**Root Cause:** Content generation in `content_router_service.py` isn't storing model_used  
**Fix Location:** `src/cofounder_agent/services/content_router_service.py` ~line 630-640  
**Action:** Check database update call includes `model_used` parameter

### Scenario B: model_used in DB but not in API response

**Root Cause:** `db_service.get_task()` or serialization not including field  
**Fix Location:** `src/cofounder_agent/services/tasks_db.py` or `schemas/unified_task_response.py`  
**Action:** Verify query includes model_used column

### Scenario C: model_used in API but not in frontend

**Root Cause:** Frontend parsing logic issue  
**Fix Location:** `web/oversight-hub/src/components/tasks/TaskMetadataDisplay.jsx`  
**Action:** Check parsing order (already fixed - should show data if present)

### Scenario D: Content shorter than target

**Root Cause:** AI model not respecting word count in prompts  
**Fix Location:** `src/cofounder_agent/services/ai_content_generator.py` or prompt templates  
**Potential Issues:**

- Max tokens too low for target length
- Prompt doesn't emphasize word count requirement
- Model validation passes even when content is short
- Model provider truncating output

---

## Quick Checks

### Check if backend is running:

```bash
curl http://localhost:8000/health
```

### Check if you can access tasks API:

```bash
curl -H "Authorization: Bearer dev-token" http://localhost:8000/api/tasks
```

### Check database connection:

```bash
# From project root
python -c "import asyncpg; import asyncio; import os; from dotenv import load_dotenv; load_dotenv('.env.local'); asyncio.run(asyncpg.connect(os.getenv('DATABASE_URL'))); print('✅ Database connected')"
```

---

## Need More Help?

If debugging scripts show unexpected results or errors, share:

1. Console output from frontend (with screenshots)
2. Output from `test-api-response.py` script
3. Output from `debug-task-data.py` script
4. Task ID being tested

This will help pinpoint exactly where in the data flow the issue occurs.
