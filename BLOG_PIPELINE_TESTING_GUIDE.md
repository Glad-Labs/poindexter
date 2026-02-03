# Blog Post Generation Pipeline - Testing & Debugging Guide

**Date:** February 2, 2026  
**Status:** Ready for testing after code changes

---

## CHANGES MADE

### 1. Database Schema Enhancement
**File:** `src/cofounder_agent/migrations/009_add_multi_phase_model_tracking.sql`

Added two new JSONB columns to `content_tasks`:
- `models_used_by_phase`: Tracks which model was used at each pipeline phase
- `model_selection_log`: Detailed decision tree showing why providers were selected/rejected

### 2. AIContentGenerator Enhancements
**File:** `src/cofounder_agent/services/ai_content_generator.py`

Added to metrics dict:
- `models_used_by_phase`: Dictionary tracking models per phase {"draft": "Google Gemini (gemini-2.5-flash)"}
- `model_selection_log`: Decision tree showing:
  - `gemini_key_available`: bool
  - `gemini_attempted`: bool
  - `gemini_succeeded`: bool
  - `gemini_error`: error message if failed
  - `ollama_available`: bool
  - `huggingface_token_available`: bool

Enhanced logging to show:
- When Gemini is attempted (line 303+)
- Whether skip_ollama is true when non-Ollama provider selected
- Detailed provider check information

### 3. ContentRouter Updates
**File:** `src/cofounder_agent/services/content_router_service.py`

Updated task storage after content generation:
- Store `model_used` (final model used)
- Store `models_used_by_phase` (all phases)
- Store `model_selection_log` (decision tree)

### 4. Database Service Updates
**File:** `src/cofounder_agent/services/tasks_db.py`

Updated `add_task()` to serialize:
- `models_used_by_phase` as JSON
- `model_selection_log` as JSON

---

## HOW TO TEST

### Step 1: Apply Migration
```bash
cd src/cofounder_agent
python -c "
import asyncio
from services.database_service import DatabaseService

async def migrate():
    db = DatabaseService()
    with open('migrations/009_add_multi_phase_model_tracking.sql') as f:
        sql = f.read()
    await db.execute_migration(sql)
    
asyncio.run(migrate())
"
```

Or manually in PostgreSQL:
```sql
\c glad_labs_dev
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS models_used_by_phase JSONB DEFAULT '{}'::jsonb;

ALTER TABLE content_tasks  
ADD COLUMN IF NOT EXISTS model_selection_log JSONB DEFAULT '{}'::jsonb;
```

### Step 2: Restart Backend

**Option A - Via npm:**
```bash
npm run dev:cofounder
```

**Option B - Manual restart:**
```bash
# Kill existing Python processes
taskkill /f /im python.exe

# Start new backend
cd src/cofounder_agent
poetry run uvicorn main:app --reload --port 8000
```

The backend should log:
```
[+] Loaded .env.local from /path/to/.env.local
[+] Gemini key: ✓ set
```

### Step 3: Create Test Blog Post with Gemini

**Via Frontend:**
1. Open http://localhost:3001 (Oversight Hub)
2. Click "Create Blog Post"
3. Fill in:
   - Topic: "Test: Quantum Computing Basics"
   - Style: "technical"
   - Tone: "professional"
   - Word count: 500
   - **MODEL SELECTION: Select "Gemini 2.5 Flash"**
4. Click "Generate"

**Via cURL:**
```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT" \
  -d '{
    "task_type": "blog_post",
    "topic": "Test: Quantum Computing Basics",
    "style": "technical",
    "tone": "professional",
    "target_length": 500,
    "models_by_phase": {
      "draft": "gemini-2.5-flash"
    },
    "quality_preference": "balanced"
  }'
```

### Step 4: Monitor Backend Logs

Watch for these key indicators:

**Gemini Attempt Started:**
```
🎯 Attempting user-selected provider: Gemini (preferred_model: gemini-2.5-flash)...
```

**Provider Check:**
```
🔍 PROVIDER CHECK:
   User selection - provider: gemini, model: gemini-2.5-flash
   Skip Ollama: True (user explicitly selected cloud provider)
   Gemini - key: ✓
```

**Gemini Success:**
```
✓ Content generated with user-selected Gemini: [validation feedback]
```

**OR Gemini Error (then Ollama fallback):**
```
User-selected Gemini failed: [ERROR TYPE]: [ERROR MESSAGE]
🔄 [ATTEMPT 1/3] Trying Ollama (Local, GPU-accelerated)...
```

**Model Used Recorded:**
```
✅ Content generated (XXX chars) using Google Gemini (gemini-2.5-flash)
```

### Step 5: Verify Database Storage

Check that models are stored:
```sql
SELECT 
  task_id,
  model_used,
  models_used_by_phase,
  model_selection_log
FROM content_tasks
ORDER BY created_at DESC
LIMIT 1;
```

**Expected output:**
```
task_id                | model_used                            | models_used_by_phase                 | model_selection_log
-----------------------|---------------------------------------|--------------------------------------|-------------------------------
550e8400-e29b-41d4...  | Google Gemini (gemini-2.5-flash)    | {"draft": "Google Gemini (gemini-... | {"requested_provider": "gemini",...
```

---

## DEBUGGING CHECKLIST

### Issue: Gemini Not Being Used

**Check 1: Is GEMINI_API_KEY in .env.local?**
```bash
grep "GEMINI_API_KEY" .env.local
# Should show: GEMINI_API_KEY=AIzaSyA...
```

**Check 2: Did backend restart after adding/updating key?**
```bash
# In backend logs at startup, look for:
[+] Loaded .env.local from /path/to/.env.local
```
If you don't see this, restart backend.

**Check 3: Is skip_ollama True?**
Look for this in logs:
```
Skip Ollama: True (user explicitly selected cloud provider)
```
If False, the user selection didn't reach the generator correctly.

**Check 4: Is Gemini key detected?**
Look for:
```
Gemini - key: ✓
```
If `✗`, the env var isn't loaded.

**Check 5: Are all 3 conditions of Gemini attempt met?**
```python
if preferred_provider and preferred_provider.lower() == 'gemini' and self.gemini_key:
```
- `preferred_provider` = "gemini" (or starts with "gemini")
- `preferred_provider.lower()` = exactly "gemini"
- `self.gemini_key` = has a value (39 char Google API key)

### Issue: Ollama Used Instead of Gemini

**Possible Causes:**
1. **Ollama is running** - Even with Gemini selected, if Ollama is available AND an error occurs in Gemini, it falls back to Ollama
   - Solution: Either disable Ollama or check Gemini error logs

2. **Gemini API error** - Gemini was tried but failed
   - Check logs for: `User-selected Gemini failed:`
   - Look for error type and message

3. **models_by_phase not passed** - Frontend didn't send model selection
   - Check in logs for: `models_by_phase = None` or `{}`
   - Verify frontend is sending `models_by_phase: {"draft": "gemini-..."}

### Issue: Model Not Recorded in Database

**Check 1: Is update_task being called?**
Look for:
```
🔵 TasksDatabase.update_task() ENTRY
```

**Check 2: Does update contain model_used?**
Look for in logs:
```
Updates received: ['status', 'content', 'title', 'model_used', 'models_used_by_phase', 'model_selection_log']
```

**Check 3: Can the database handle new columns?**
Run migration:
```bash
python src/cofounder_agent/migrations/apply_migrations.py --file 009_add_multi_phase_model_tracking.sql
```

---

## EXPECTED BEHAVIOR AFTER CHANGES

### Successful Gemini Generation

**Timeline:**
1. Task created with models_by_phase: {"draft": "gemini-2.5-flash"}
2. ContentRouter extracts preferred_provider = "gemini"
3. AIContentGenerator.generate_blog_post() called
4. Gemini branch enters (line 303+)
5. Content generated successfully
6. metrics["model_used"] = "Google Gemini (gemini-2.5-flash)"
7. metrics["models_used_by_phase"]["draft"] = same
8. ContentRouter stores all metrics in database
9. Task updated with model info

**Database Result:**
```json
{
  "model_used": "Google Gemini (gemini-2.5-flash)",
  "models_used_by_phase": {
    "draft": "Google Gemini (gemini-2.5-flash)"
  },
  "model_selection_log": {
    "requested_provider": "gemini",
    "requested_model": "gemini-2.5-flash",
    "attempted_providers": ["gemini"],
    "gemini_key_available": true,
    "gemini_attempted": true,
    "gemini_succeeded": true,
    "gemini_error": null
  }
}
```

### Gemini Failed, Fallback to Ollama

**Timeline:**
1. Gemini attempted
2. Gemini API error (e.g., rate limit, auth error)
3. Falls through to Ollama check
4. Ollama available and succeeds
5. metrics["model_used"] = "Ollama - neural-chat:latest"
6. metrics["models_used_by_phase"]["draft"] = same
7. metrics["model_selection_log"]["gemini_error"] = "[error message]"
8. metrics["model_selection_log"]["attempted_providers"] = ["gemini", "ollama"]

**Database Result:**
```json
{
  "model_used": "Ollama - neural-chat:latest",
  "models_used_by_phase": {
    "draft": "Ollama - neural-chat:latest"
  },
  "model_selection_log": {
    "requested_provider": "gemini",
    "gemini_attempted": true,
    "gemini_succeeded": false,
    "gemini_error": "401 Unauthorized - API key invalid or expired",
    "attempted_providers": ["gemini", "ollama"]
  }
}
```

---

## NEXT STEPS

After verifying Gemini works:

1. **Expand phase tracking** - Currently only tracking "draft" phase. Can add:
   - "research": Research phase model
   - "qa": QA/refinement phase model
   - "image": Image generation model
   - "publish": Publishing model

2. **Add analytics** - Query which models are used most often:
   ```sql
   SELECT 
     model_used,
     COUNT(*) as count,
     ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) as percentage
   FROM content_tasks
   WHERE model_used IS NOT NULL
   GROUP BY model_used
   ORDER BY count DESC;
   ```

3. **Add cost tracking** - Use model_used to calculate costs:
   - Gemini 2.5 Flash: $0.075 per 1M input, $0.30 per 1M output
   - Ollama: $0 (local)
   - Calculate actual cost based on tokens used

4. **Add per-phase provider selection** - Allow users to select different models for research, draft, QA, etc.

---

## KEY FILES MODIFIED

| File | Changes | Lines |
|------|---------|-------|
| ai_content_generator.py | Added metrics tracking, enhanced logging | 275-320, 389-395 |
| content_router_service.py | Store model info in DB | 553-565 |
| tasks_db.py | Serialize new JSON columns | 200-202 |
| migrations/009_*.sql | New migration | All |

