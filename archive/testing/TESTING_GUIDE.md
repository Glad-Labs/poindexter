# Testing Task Metadata Fix - Quick Guide

## Current Status

✅ Backend fixes applied to `tasks_db.py` and `task_routes.py`
✅ Frontend fixes applied to `CreateTaskModal.jsx`
✅ Diagnostic logging added throughout the stack
✅ Test scripts created

## Next Steps to Verify the Fix

### Option 1: Automated Test Script

```bash
./scripts/test_metadata_fix.sh
```

This will guide you through the testing process step-by-step.

### Option 2: Manual Testing

#### 1. Start Services (if not already running)

```bash
# Terminal 1 - Backend
npm run dev:cofounder

# Terminal 2 - Frontend
npm run dev:oversight
```

#### 2. Open Browser with DevTools

- Navigate to: <http://localhost:3001>
- Open DevTools (F12)
- Go to Console tab

#### 3. Create Test Task

**Click "Create Task"** and fill in:

| Field             | Value                            | Why This Matters                    |
| ----------------- | -------------------------------- | ----------------------------------- |
| Task Type         | Blog Post                        | Testing blog post creation          |
| Topic             | "Testing Metadata Fix - March 7" | Unique identifier                   |
| Word Count        | 1500                             | Standard length                     |
| **Writing Style** | **"narrative"**                  | ⚠️ NOT "technical" (old default)    |
| **Tone**          | **"casual"**                     | ⚠️ NOT "professional" (old default) |
| Keywords          | "testing, metadata"              | Optional                            |

**In Model Selection Panel:**

- Select quality tier OR
- Manually configure models for each phase
- Should see cost estimate update

**Click "Create Task"**

#### 4. Check Browser Console

Look for these log messages:

```
📝 [CreateTaskModal] Task type selected: blog_post
📤 [CreateTaskModal] Form data before payload:
  { topic: "Testing Metadata Fix...", style: "narrative", tone: "casual", ... }
📤 [CreateTaskModal] Model selections:
  { modelSelections: { research: "...", draft: "...", ... }, qualityPreference: "balanced" }
📤 [CreateTaskModal] Final payload:
  { task_type: "blog_post", style: "narrative", tone: "casual", models_by_phase: {...} }
```

**❌ RED FLAGS:**

- If `style: undefined` → Dropdown not capturing selection
- If `tone: undefined` → Dropdown not capturing selection
- If `models_by_phase: {}` → ModelSelectionPanel not sending data

#### 5. Check Backend Logs

In the terminal running the backend, look for:

```
📥 [BLOG_POST] Incoming request:
   topic: Testing Metadata Fix - March 7
   style: narrative (type: <class 'str'>)
   tone: casual (type: <class 'str'>)
   models_by_phase: {'research': '...', 'draft': '...', ...}
   quality_preference: balanced

📦 [BLOG_POST] Task data before DB insert:
   style: narrative
   tone: casual
   model_selections: {'research': '...', ...}

📊 [add_task] Critical fields being inserted:
   task_id: <uuid>
   style: narrative (original: narrative)
   tone: casual (original: casual)
   model_selections: {...} (original: {...})
```

**❌ RED FLAGS:**

- If any show `None` or default values
- If serialized differs from original

#### 6. Verify Database

Run diagnostic:

```bash
python scripts/diagnose_metadata_flow.py
```

Look for your test task at the top (most recent):

```
🔍 Task #1: <uuid>
   Type: blog_post
   Topic: Testing Metadata Fix - March 7...
   📝 Style: narrative ✅
   🎭 Tone: casual ✅
   🤖 Model Selections: ✅ 6 phases configured
   ⚡ Quality: balanced
```

**Expected Issues Count:**

```
🎨 Style Issues: 0/1  (or fewer than before)
🎭 Tone Issues: 0/1   (or fewer than before)
🤖 Model Selection Issues: 0/1  (or fewer than before)
```

## Interpreting Results

### ✅ Success Indicators

1. Browser console shows your selected values (not defaults)
2. Backend logs show received values match your selections
3. Database diagnostic shows correct values stored
4. No issues reported for your new test task

### ❌ Failure Indicators & Fixes

#### Problem: Style/Tone show as `undefined` in browser console

**Cause:** Dropdown not capturing selection
**Fix:** Check dropdown `onChange` handler and `formData` state

#### Problem: Backend logs show `None` for style/tone

**Cause:** Frontend not sending values in request
**Fix:** Check `taskPayload` construction in `handleSubmit`

#### Problem: Database has wrong values

**Cause:** Serialization or migration issue
**Fix:** Check `serialize_value_for_postgres` in `tasks_db.py`

#### Problem: Model selections empty `{}`

**Cause:** ModelSelectionPanel not calling callback
**Fix:** Verify `onSelectionChange` is wired correctly

## Common Issues

### Issue: Dropdowns show "Select..." but no selection captured

**Solution:** Ensure dropdown has `value` prop bound to `formData[field.name]`

### Issue: Model panel shows 'auto' but these aren't saved

**Solution:** 'auto' should still be sent - check if defaults are stripped

### Issue: Old tasks still show defaults

**Expected:** Only NEW tasks after the fix should have correct values

## Quick Verification Command

```bash
# One-liner to check most recent task
python -c "
import asyncio
import asyncpg
import os

async def check():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev'))
    row = await conn.fetchrow('SELECT task_id, style, tone, model_selections FROM content_tasks ORDER BY created_at DESC LIMIT 1')
    print(f'Latest task: {row[\"task_id\"]}')
    print(f'  Style: {row[\"style\"]}')
    print(f'  Tone: {row[\"tone\"]}')
    print(f'  Models: {row[\"model_selections\"]}')
    await conn.close()

asyncio.run(check())
"
```

## Need Help?

If tests fail, check:

1. [TASK_METADATA_FIX.md](../TASK_METADATA_FIX.md) - Detailed problem analysis
2. Application logs in `src/cofounder_agent/logs/cofounder_agent.log`
3. Browser DevTools Console for frontend errors
4. Database connection with `psql` to verify schema

## Success Criteria Checklist

- [ ] Browser console logs show correct values
- [ ] Backend logs show correct values received
- [ ] Backend logs show correct values before DB insert
- [ ] Database diagnostic shows correct values stored
- [ ] No Type mismatches (all strings, not None)
- [ ] Model selections is populated dict (not empty)
- [ ] New task has 0 issues in diagnostic report

---

**Ready to test?** Run services and follow steps above, or use:

```bash
./scripts/test_metadata_fix.sh
```
