# Task Metadata Fix Summary

**Date:** March 7, 2026  
**Issue:** Task metadata (style, tone, model selections) not matching user intent in database

## Problems Identified

### 1. Database Layer Overriding User Selections
**Location:** `src/cofounder_agent/services/tasks_db.py` (lines 209-210)

**Problem:**
```python
"style": task_data.get("style", "technical"),  # ❌ Overrides user selection
"tone": task_data.get("tone", "professional"),  # ❌ Overrides user selection
```

**Impact:** When user doesn't explicitly set style/tone, database layer forces defaults instead of using Pydantic schema defaults or user selections.

**Fix Applied:**
```python
# CRITICAL: Don't override user selections - trust Pydantic schema defaults
"style": task_data.get("style"),  # ✅ No fallback
"tone": task_data.get("tone"),    # ✅ No fallback
```

### 2. Frontend Using Hardcoded Fallbacks
**Location:** `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx` (lines 367, 369, 397, 399)

**Problem:**
```javascript
style: formData.style || 'technical',  // ❌ Bypasses validation
tone: formData.tone || 'professional',  // ❌ Bypasses validation
```

**Impact:** Even if user selects different values, fallbacks might override them if formData is empty.

**Fix Applied:**
```javascript
// Use undefined to let backend Pydantic schema apply defaults
style: formData.style || undefined,  // ✅ Respects user intent
tone: formData.tone || undefined,    // ✅ Respects user intent
```

### 3. Model Selections May Not Be Captured
**Location:** Multiple - UI → Backend → Database flow

**Problem:** 
- UI sends as `models_by_phase`  
- Backend correctly maps to `model_selections`  
- But data might be empty/default values  

**Diagnostic Results:** 9 out of 10 recent tasks had empty `model_selections`

## Fixes Applied

### Backend Changes

#### 1. `src/cofounder_agent/services/tasks_db.py`
- **Line 209-210:** Removed hardcoded `"technical"` and `"professional"` fallbacks
- **Added logging** to track values before database insertion

#### 2. `src/cofounder_agent/routes/task_routes.py`
- **Added comprehensive logging** in `_handle_blog_post_creation()`:
  - Log incoming request values (style, tone, models_by_phase)
  - Log task_data dict before DB insert
  - Shows type information for debugging

### Frontend Changes

#### 3. `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx`
- **Removed hardcoded fallbacks** for style and tone
- **Added console.log statements** to track:
  - Task type selection
  - Form data initialization
  - Model selections from panel
  - Final payload before API call
- **Changed fallbacks** from hardcoded strings to `undefined`

## Logging Added

### Backend Logs (will appear in `cofounder_agent.log`)

```
📥 [BLOG_POST] Incoming request:
   topic: <topic>
   style: <style> (type: <type>)
   tone: <tone> (type: <type>)
   models_by_phase: {...}
   quality_preference: <preference>

📦 [BLOG_POST] Task data before DB insert:
   style: <style>
   tone: <tone>
   model_selections: {...}

📊 [add_task] Critical fields being inserted:
   task_id: <uuid>
   style: <style> (original: <original>)
   tone: <tone> (original: <original>)
   model_selections: <json> (original: {...})
   quality_preference: <preference>
```

### Frontend Logs (will appear in browser console)

```
📝 [CreateTaskModal] Task type selected: blog_post
📝 [CreateTaskModal] Default data initialized: {...}
📤 [CreateTaskModal] Form data before payload: {...}
📤 [CreateTaskModal] Model selections: {...}
📤 [CreateTaskModal] Final payload: {...}
```

## Testing Instructions

### 1. Start the Backend with Logging

```bash
# Backend logs will show detailed metadata flow
npm run dev:cofounder
```

### 2. Open Frontend with DevTools

```bash
# Frontend logs will show in browser console
npm run dev:oversight
```

### 3. Create a Test Task

1. Open Oversight Hub: http://localhost:3001
2. Click "Create Task" button
3. Select "Blog Post"
4. Fill in ALL fields:
   - **Topic:** "Test Task Metadata Flow"
   - **Word Count:** 1500
   - **Writing Style:** Select "narrative" (or any non-technical)
   - **Tone:** Select "casual" (or any non-professional)
5. In Model Selection Panel:
   - Select quality preference (Fast/Balanced/Quality)
   - OR manually configure phase models
6. Click "Create Task"

### 4. Monitor Logs

**In Backend Terminal:**
- Look for `📥 [BLOG_POST] Incoming request:`
- Verify `style` and `tone` match your selections
- Verify `models_by_phase` has actual model names (not just 'auto')

**In Browser Console:**
- Look for `📤 [CreateTaskModal] Final payload:`
- Verify `style`, `tone`, and `models_by_phase` are correct
- Should NOT see `'technical'` or `'professional'` unless explicitly selected

### 5. Verify Database

```bash
# Run the diagnostic script
python scripts/diagnose_metadata_flow.py
```

**Expected Results:**
- Style should match what you selected (not 'technical')
- Tone should match what you selected (not 'professional')
- Model selections should have actual values (not empty `{}`)

## Expected Behavior After Fix

### Before Fix:
```json
{
  "style": "technical",  // ❌ Default even if user selected "narrative"
  "tone": "professional", // ❌ Default even if user selected "casual"
  "model_selections": {}  // ❌ Empty even if user configured models
}
```

### After Fix:
```json
{
  "style": "narrative",   // ✅ Matches user selection
  "tone": "casual",       // ✅ Matches user selection
  "model_selections": {   // ✅ Has actual model names
    "research": "mixtral:latest",
    "draft": "qwen3-coder:30b",
    "assess": "llama3:70b-instruct",
    ...
  }
}
```

## Debugging Tips

### If Style/Tone Still Defaults:

1. **Check browser console** for `📤 [CreateTaskModal] Final payload:`
   - If style/tone are missing here, frontend isn't capturing them
   - Verify dropdown selections are firing `onChange` events

2. **Check backend logs** for `📥 [BLOG_POST] Incoming request:`
   - If values are missing here, API request wasn't sent correctly
   - Check Network tab in DevTools for actual request body

3. **Check database logs** for `📊 [add_task] Critical fields:`
   - If values are missing here, database serialization failed
   - Verify no other code is modifying task_data

### If Model Selections Still Empty:

1. **Check ModelSelectionPanel** is calling `onSelectionChange`:
   - Add console.log in `handleModelSelectionChange` callback
   - Verify `modelSelection` state is being updated

2. **Check if `modelSelections` has default values:**
   - ModelSelectionPanel initializes with `{ research: 'auto', ... }`
   - `'auto'` should still be sent to backend (not empty)

3. **Verify backend mapping:**
   - Check logs show `models_by_phase` → `model_selections` mapping
   - Ensure JSON serialization isn't stripping empty values

## Rollback Instructions

If the fixes cause issues:

### Backend Rollback:
```bash
git checkout HEAD -- src/cofounder_agent/services/tasks_db.py
git checkout HEAD -- src/cofounder_agent/routes/task_routes.py
```

### Frontend Rollback:
```bash
git checkout HEAD -- web/oversight-hub/src/components/tasks/CreateTaskModal.jsx
```

## Next Steps

1. **Test thoroughly** with multiple task types
2. **Monitor production logs** for any issues
3. **Consider adding:**
   - Form field validation improvements
   - Default value initialization for style/tone dropdowns
   - Better error messages if required fields missing
4. **Update documentation** with proper task creation workflow

## Related Files

- `src/cofounder_agent/services/tasks_db.py` - Database insertion
- `src/cofounder_agent/routes/task_routes.py` - Request handling
- `src/cofounder_agent/schemas/task_schemas.py` - Pydantic models
- `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx` - UI form
- `web/oversight-hub/src/components/ModelSelectionPanel.jsx` - Model selector
- `scripts/diagnose_metadata_flow.py` - Diagnostic tool

## Success Criteria

✅ User-selected style appears in database (not 'technical')  
✅ User-selected tone appears in database (not 'professional')  
✅ Model selections are saved as JSON object (not empty)  
✅ Quality preference is saved correctly  
✅ Logs show complete data flow from UI → Backend → Database  
✅ Diagnostic script shows 0 issues in new tasks  

---

**Status:** Fixes Applied, Ready for Testing  
**Last Updated:** March 7, 2026
