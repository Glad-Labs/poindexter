# API Flow Debugging Guide

**Issue:** Frontend not polling backend for model costs  
**Root Cause Found:** API client wasn't wired to pass model selections + log them  
**Fix Applied:** Updated cofounderAgentClient.js to accept and forward model selections

---

## What Just Changed

### File: `web/oversight-hub/src/services/cofounderAgentClient.js`

**Updated `createBlogPost()` function:**

**Before:**

```javascript
export async function createBlogPost(
  topicOrOptions,
  primaryKeyword,
  targetAudience,
  category
) {
  // payload didn't include model selections
  const payload = {
    task_name: ...,
    topic: ...,
    primary_keyword: ...,
    target_audience: ...,
    category: ...,
    metadata: {},
  };
}
```

**After:**

```javascript
export async function createBlogPost(
  topicOrOptions,
  primaryKeyword,
  targetAudience,
  category,
  modelSelections,      // ← NEW
  qualityPreference,    // ← NEW
  estimatedCost         // ← NEW
) {
  const payload = {
    task_name: ...,
    topic: ...,
    primary_keyword: ...,
    target_audience: ...,
    category: ...,
    model_selections: modelSelections || {},        // ← NEW
    quality_preference: qualityPreference || ...,   // ← NEW
    estimated_cost: estimatedCost || 0.0,           // ← NEW
    metadata: {},
  };
}
```

---

## API Flow (Now Complete)

```
1. User fills form in TaskCreationModal
   ├─ Topic: "AI in Healthcare"
   ├─ Keyword: "AI healthcare"
   ├─ Audience: "Doctors"
   └─ Category: "healthcare"

2. User selects models in ModelSelectionPanel
   ├─ Quality preset: "Fast"
   ├─ Model selections: {research: ollama, outline: ollama, ...}
   ├─ Estimated cost: $0.006
   └─ Component calls onSelectionChange(selection)

3. TaskCreationModal updates state
   └─ setModelSelection(selection)

4. User clicks "Create Task"
   └─ handleSubmit() called

5. Frontend makes API call
   └─ createBlogPost(
       topic,
       primaryKeyword,
       targetAudience,
       category,
       modelSelection.modelSelections,      ← NOW PASSED
       modelSelection.qualityPreference,    ← NOW PASSED
       modelSelection.estimatedCost         ← NOW PASSED
     )

6. API client logs the payload
   └─ console.log('📤 Sending task payload:')
      {
        task_name: "Blog Post: AI in Healthcare",
        topic: "AI in Healthcare",
        primary_keyword: "AI healthcare",
        target_audience: "Doctors",
        category: "healthcare",
        model_selections: {
          research: "ollama",
          outline: "ollama",
          draft: "gpt-3.5-turbo",
          assess: "gpt-4",
          refine: "gpt-4",
          finalize: "gpt-4"
        },
        quality_preference: "fast",
        estimated_cost: 0.006,
        metadata: {}
      }

7. makeRequest() calls backend
   └─ POST /api/tasks
      └─ console.log('🔵 makeRequest: POST http://localhost:8000/api/tasks')

8. Backend receives payload
   └─ Create task with model_selections
   └─ Save to database
   └─ Log to cost_logs table

9. Backend returns task ID
   └─ response: { id: "task_123", status: "created" }

10. Frontend polls for status
    └─ pollTaskStatus() called
    └─ GET /api/tasks/task_123/status
    └─ Updates progress bar

11. Task completes
    └─ Frontend shows success
    └─ Dashboard refreshes
    └─ Shows costs
```

---

## What You'll See in Browser Console

**When form is submitted:**

```
📤 Sending task payload: {
  "task_name": "Blog Post: Ai In Healthcare",
  "topic": "AI in Healthcare",
  "primary_keyword": "AI healthcare",
  "target_audience": "Doctors",
  "category": "healthcare",
  "model_selections": {
    "research": "ollama",
    "outline": "ollama",
    "draft": "gpt-3.5-turbo",
    "assess": "gpt-4",
    "refine": "gpt-4",
    "finalize": "gpt-4"
  },
  "quality_preference": "fast",
  "estimated_cost": 0.006,
  "metadata": {}
}

✅ Validation - Required Fields: {
  topic_valid: true,
  task_name_valid: true,
  model_selections: {...},
  quality_preference: "fast",
  estimated_cost: 0.006
}

🔵 makeRequest: POST http://localhost:8000/api/tasks
```

**When backend responds:**

```
🟡 makeRequest: Response status: 200 OK
🟢 makeRequest: Response parsed: {
  "id": "task_abc123",
  "status": "created"
}
✅ makeRequest: Returning result
```

---

## Backend Side (What Should Happen)

**In FastAPI (task_routes.py):**

```python
@router.post("/api/tasks")
async def create_task(request_body: dict):
    # Extract model selections from request
    model_selections = request_body.get('model_selections', {})
    quality_preference = request_body.get('quality_preference', 'balanced')
    estimated_cost = request_body.get('estimated_cost', 0.0)

    # Log them
    print(f"📥 Received task with:")
    print(f"   model_selections: {model_selections}")
    print(f"   quality_preference: {quality_preference}")
    print(f"   estimated_cost: {estimated_cost}")

    # Save to database
    task = {
        'task_id': 'task_abc123',
        'model_selections': model_selections,
        'quality_preference': quality_preference,
        'estimated_cost': estimated_cost,
        ...
    }

    # Return response
    return {"id": task_id, "status": "created"}
```

---

## Testing the Flow

### Step 1: Check Frontend Console

1. Open browser DevTools (F12)
2. Go to Console tab
3. Fill in task form
4. Select "Fast" in ModelSelectionPanel
5. Click "Create Task"
6. Look for log messages:
   ```
   📤 Sending task payload: {...}
   🔵 makeRequest: POST http://localhost:8000/api/tasks
   ```

**Expected:** See model_selections, quality_preference, estimated_cost in payload

**If not seeing it:**

- Check if ModelSelectionPanel is being used
- Check if modelSelection state is updating
- Check browser network tab (F12 → Network)

### Step 2: Check Network Tab

1. F12 → Network tab
2. Clear existing requests
3. Fill form and create task
4. Look for POST request to `/api/tasks`
5. Click on it
6. Check "Request" payload
7. Should include:
   ```json
   {
     "model_selections": {...},
     "quality_preference": "fast",
     "estimated_cost": 0.006
   }
   ```

**Expected:** Payload includes model data

**If not seeing it:**

- Check TaskCreationModal is passing the state
- Verify createBlogPost signature matches

### Step 3: Check Backend Logs

1. Backend should be running on port 8000 or 8001
2. Look for log messages like:
   ```
   📥 POST /api/tasks received
   Model selections: {research: ollama, ...}
   ```

**Expected:** Backend receives and logs the data

**If not seeing it:**

- Backend might not be running
- Port might be wrong
- CORS might be blocking the request

---

## Debugging Checklist

**Frontend not sending data?**

- [ ] Check ModelSelectionPanel is imported in TaskCreationModal
- [ ] Check onSelectionChange callback updates modelSelection state
- [ ] Check handleSubmit passes modelSelection to createBlogPost
- [ ] Check browser console for errors (F12)

**Data not reaching backend?**

- [ ] Check Network tab shows POST request
- [ ] Check request includes model_selections in body
- [ ] Check CORS headers are correct
- [ ] Check backend is listening on correct port

**Backend not saving data?**

- [ ] Check backend logs for received data
- [ ] Check database schema has model_selections column
- [ ] Check task_routes.py accepts model_selections
- [ ] Check database insertion query

---

## Code That Was Fixed

### File: `web/oversight-hub/src/services/cofounderAgentClient.js`

**Changes Made:**

1. Added 3 new parameters to `createBlogPost()` signature
2. Updated payload to include model_selections, quality_preference, estimated_cost
3. Added console logging to show what's being sent
4. Updated both old and new API format handlers

**Result:**

- ✅ Model selections now passed through API
- ✅ Logged in browser console
- ✅ Sent to backend in POST body
- ✅ Ready for backend to save

---

## Next Steps

### 1. Verify Frontend Changes

```bash
# Check the API client was updated
grep -A 5 "model_selections:" web/oversight-hub/src/services/cofounderAgentClient.js

# Expected output showing model_selections in payload
```

### 2. Test the Flow

1. Start backend: `python main.py` (port 8000 or 8001)
2. Start frontend: `npm start` (port 3000)
3. Open http://localhost:3000
4. Create a task
5. Check browser console (F12) for logs
6. Check Network tab for POST request

### 3. Verify Backend Receives Data

1. Check backend logs for task data
2. Check database for task record
3. Verify model_selections was saved

### 4. Check Dashboard

1. After task completes
2. Go to Cost Metrics Dashboard
3. Verify costs are displayed
4. Verify they match estimated cost ±5%

---

## Common Issues & Fixes

**Issue: "Cannot read property 'model_selections' of undefined"**

- [ ] Check ModelSelectionPanel is imported
- [ ] Check modelSelection state is initialized
- [ ] Check onSelectionChange is wired to setModelSelection

**Issue: Model selections not in network request**

- [ ] Check createBlogPost receives all 7 parameters
- [ ] Check TaskCreationModal passes modelSelection state
- [ ] Check payload construction includes model data

**Issue: Backend returns 400 Bad Request**

- [ ] Check backend schema accepts model_selections
- [ ] Check field names match (model_selections vs modelSelections)
- [ ] Check database columns exist

**Issue: Costs not showing in dashboard**

- [ ] Check task was created (task ID returned)
- [ ] Check cost_logs table has entries
- [ ] Check dashboard refreshes after task completes
- [ ] Check estimated_cost matches actual costs

---

## Summary

✅ **What was fixed:** API client now passes model selections  
✅ **What to test:** Frontend → Backend API call  
✅ **Expected result:** See model data in browser console + network request  
✅ **Next step:** Start services and run test

---

**Status:** Fix applied, ready for testing ✅

**When you're ready:**

1. Start backend
2. Start frontend
3. Create a test task
4. Check browser console for logs
5. Verify costs appear in database
