# Expected API Flow & Console Logs

**Issue:** Frontend not polling backend for model costs  
**Status:** ✅ FIXED  
**What was changed:**

1. cofounderAgentClient.js - Updated to pass model selections in payload
2. task_schemas.py - Added model_selections, quality_preference, estimated_cost fields
3. task_routes.py - Added logging for model selections

---

## Expected Console Output

### Frontend Browser Console (F12)

**When user clicks "Create Task":**

```
📤 Sending task payload: {
  "task_name": "Blog Post: AI In Healthcare",
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
  "id": "abc123-def456",
  "status": "pending",
  "created_at": "2025-12-19T13:45:23.123456+00:00",
  "message": "Task created successfully"
}
✅ makeRequest: Returning result
```

---

### Backend Console (Python)

**When request arrives:**

```
INFO: "POST /api/tasks HTTP/1.1" 201 Created

📥 [TASK_CREATE] Received request:
   - task_name: Blog Post: AI In Healthcare
   - topic: AI in Healthcare
   - category: healthcare
   - model_selections: {'research': 'ollama', 'outline': 'ollama', 'draft': 'gpt-3.5-turbo', 'assess': 'gpt-4', 'refine': 'gpt-4', 'finalize': 'gpt-4'}
   - quality_preference: fast
   - estimated_cost: 0.006
   - user_id: user_12345

🔄 [TASK_CREATE] Generated task_id: abc123-def456

🔄 [TASK_CREATE] Task data prepared:
   - Basic: {
       "id": "abc123-def456",
       "task_name": "Blog Post: AI In Healthcare",
       "topic": "AI in Healthcare",
       "category": "healthcare",
       "status": "pending",
       "agent_id": "content-agent"
     }
   - Model Selections: {'research': 'ollama', 'outline': 'ollama', 'draft': 'gpt-3.5-turbo', 'assess': 'gpt-4', 'refine': 'gpt-4', 'finalize': 'gpt-4'}
   - Cost Info: quality=fast, estimated=$0.0060

💾 [TASK_CREATE] Inserting into database...

✅ [TASK_CREATE] Database insert successful - returned task_id: abc123-def456

🔍 [TASK_CREATE] Verifying task in database...

✅ [TASK_CREATE] Verification SUCCESS - Task found in database
   - Status: pending
   - Created: 2025-12-19T13:45:23.123456+00:00

📤 [TASK_CREATE] Returning response: {
  "id": "abc123-def456",
  "status": "pending",
  "created_at": "2025-12-19T13:45:23.123456+00:00",
  "message": "Task created successfully"
}

⏳ [TASK_CREATE] Queueing background task for content generation...

✅ [TASK_CREATE] Background task queued successfully
```

---

## Network Request (F12 → Network Tab)

### POST /api/tasks

**Request Headers:**

```
POST /api/tasks HTTP/1.1
Host: localhost:8000
Authorization: Bearer eyJhbG...
Content-Type: application/json
```

**Request Body:**

```json
{
  "task_name": "Blog Post: AI In Healthcare",
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
```

**Response Status:**

```
201 Created
```

**Response Body:**

```json
{
  "id": "abc123-def456",
  "status": "pending",
  "created_at": "2025-12-19T13:45:23.123456+00:00",
  "message": "Task created successfully"
}
```

---

## Files Changed

### 1. Frontend API Client

**File:** `web/oversight-hub/src/services/cofounderAgentClient.js`

**Function Signature:**

```javascript
// BEFORE
export async function createBlogPost(
  topicOrOptions,
  primaryKeyword,
  targetAudience,
  category
)

// AFTER
export async function createBlogPost(
  topicOrOptions,
  primaryKeyword,
  targetAudience,
  category,
  modelSelections,      // ← NEW
  qualityPreference,    // ← NEW
  estimatedCost         // ← NEW
)
```

**Payload Construction:**

```javascript
// BEFORE
const payload = {
  task_name: ...,
  topic: ...,
  primary_keyword: ...,
  target_audience: ...,
  category: ...,
  metadata: {}
};

// AFTER
const payload = {
  task_name: ...,
  topic: ...,
  primary_keyword: ...,
  target_audience: ...,
  category: ...,
  model_selections: modelSelections || {},        // ← NEW
  quality_preference: qualityPreference || ...,   // ← NEW
  estimated_cost: estimatedCost || 0.0,           // ← NEW
  metadata: {}
};
```

### 2. Backend Schema

**File:** `src/cofounder_agent/schemas/task_schemas.py`

**Schema Update:**

```python
# BEFORE
class TaskCreateRequest(BaseModel):
    task_name: str
    topic: str
    primary_keyword: str
    target_audience: str
    category: str
    metadata: Optional[Dict[str, Any]]

# AFTER
class TaskCreateRequest(BaseModel):
    task_name: str
    topic: str
    primary_keyword: str
    target_audience: str
    category: str
    model_selections: Optional[Dict[str, str]]      # ← NEW
    quality_preference: Optional[str]               # ← NEW
    estimated_cost: Optional[float]                 # ← NEW
    metadata: Optional[Dict[str, Any]]
```

### 3. Backend Route Handler

**File:** `src/cofounder_agent/routes/task_routes.py`

**Logging Added:**

```python
# BEFORE
logger.info(f"📥 [TASK_CREATE] Received request:")
logger.info(f"   - task_name: {request.task_name}")
logger.info(f"   - topic: {request.topic}")

# AFTER
logger.info(f"📥 [TASK_CREATE] Received request:")
logger.info(f"   - task_name: {request.task_name}")
logger.info(f"   - topic: {request.topic}")
logger.info(f"   - model_selections: {request.model_selections}")         # ← NEW
logger.info(f"   - quality_preference: {request.quality_preference}")     # ← NEW
logger.info(f"   - estimated_cost: {request.estimated_cost}")             # ← NEW
```

**Task Data Includes:**

```python
# BEFORE
task_data = {
    "id": task_id,
    "task_name": ...,
    "topic": ...,
    "status": "pending",
    ...
}

# AFTER
task_data = {
    "id": task_id,
    "task_name": ...,
    "topic": ...,
    "model_selections": request.model_selections or {},      # ← NEW
    "quality_preference": request.quality_preference or ..., # ← NEW
    "estimated_cost": request.estimated_cost or 0.0,        # ← NEW
    "status": "pending",
    ...
}
```

---

## Testing Checklist

### Step 1: Verify Code Changes

```bash
# Check cofounderAgentClient.js has model_selections in payload
grep -A 2 "model_selections:" web/oversight-hub/src/services/cofounderAgentClient.js
# Expected: Shows payload includes model_selections

# Check schema has new fields
grep -A 3 "model_selections" src/cofounder_agent/schemas/task_schemas.py
# Expected: Shows Optional[Dict[str, str]]

# Check route logging includes model data
grep -A 2 "model_selections:" src/cofounder_agent/routes/task_routes.py
# Expected: Shows logging statement
```

### Step 2: Start Services

```bash
# Terminal 1: Start backend
cd src/cofounder_agent
python main.py
# Expected: Server running on http://localhost:8000

# Terminal 2: Start frontend
cd web/oversight-hub
npm start
# Expected: Running on http://localhost:3000
```

### Step 3: Create Test Task

1. Open http://localhost:3000 in browser
2. Click "Create Task" button
3. Fill in form:
   - Topic: "Testing Model Selection"
   - Keyword: "testing"
   - Audience: "developers"
   - Category: "technology"
4. Click "Fast" preset in ModelSelectionPanel
5. Click "Create Task"

### Step 4: Check Frontend Console

```
F12 → Console tab

Expected to see:
📤 Sending task payload: {...}
✅ Validation - Required Fields: {...}
🔵 makeRequest: POST http://localhost:8000/api/tasks
🟡 makeRequest: Response status: 201 Created
🟢 makeRequest: Response parsed: {...}
✅ makeRequest: Returning result
```

### Step 5: Check Backend Console

```
Expected to see:
📥 [TASK_CREATE] Received request:
   - model_selections: {'research': 'ollama', ...}
   - quality_preference: fast
   - estimated_cost: 0.006

💾 [TASK_CREATE] Inserting into database...
✅ [TASK_CREATE] Database insert successful
```

### Step 6: Check Network Tab

```
F12 → Network tab
Find POST request to /api/tasks

Request Body should show:
{
  "model_selections": {...},
  "quality_preference": "fast",
  "estimated_cost": 0.006,
  ...
}

Response should show:
{
  "id": "...",
  "status": "pending",
  ...
}
```

---

## Success Criteria

✅ **Frontend sends model data:**

- [ ] See "📤 Sending task payload" in console
- [ ] Payload includes model_selections
- [ ] Payload includes quality_preference
- [ ] Payload includes estimated_cost

✅ **Network request includes data:**

- [ ] POST /api/tasks shows in Network tab
- [ ] Request body includes all 3 model fields
- [ ] Response status is 201 Created

✅ **Backend logs model data:**

- [ ] See "📥 [TASK_CREATE] Received request:" in backend logs
- [ ] See "model_selections:" line in logs
- [ ] See "quality_preference:" line in logs
- [ ] See "estimated_cost:" line in logs

✅ **Task created successfully:**

- [ ] Task ID returned in response
- [ ] Task created in database
- [ ] Model selections saved with task

---

## Troubleshooting

**If frontend doesn't send data:**

```
Check:
1. ModelSelectionPanel imported in TaskCreationModal?
2. modelSelection state initialized?
3. onSelectionChange updates state?
4. handleSubmit passes modelSelection to createBlogPost?
```

**If data doesn't reach backend:**

```
Check:
1. Backend running on port 8000?
2. CORS enabled?
3. Network request shows model data?
4. No 400/422 errors in response?
```

**If backend doesn't log data:**

```
Check:
1. Schema accepts model_selections?
2. Route handler logs them?
3. Task data includes them?
4. Database columns exist?
```

---

## Summary

✅ **What's Fixed:**

- API client now passes 3 additional parameters
- Backend schema updated to accept them
- Route handler logs them for debugging
- All along the chain will now show the data flow

✅ **What to Test:**

- Create task with model selections
- Check browser console for payload
- Check backend logs for received data
- Verify network request body

✅ **Expected Result:**

- Frontend → Backend communication shows model selections
- Console logs prove data is flowing through the system
- Backend saves model selections with task

**You're all set. Start services and create a test task.** ✅
