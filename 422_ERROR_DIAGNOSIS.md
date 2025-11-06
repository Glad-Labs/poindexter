# 422 Unprocessable Entity Error - Diagnosis & Fix

## Problem

Your Oversight Hub is sending POST requests to `/api/tasks` that are being rejected with **422 Unprocessable Entity**. This means Pydantic validation is failing on the backend.

## Root Cause Analysis

### Expected Schema (Backend - `task_routes.py`)

```python
class TaskCreateRequest(BaseModel):
    task_name: str          # ✅ REQUIRED (Field(...))
    topic: str              # ✅ REQUIRED (Field(...))
    primary_keyword: str    # ❌ Optional (default="")
    target_audience: str    # ❌ Optional (default="")
    category: str           # ❌ Optional (default="general")
    metadata: Optional[Dict[str, Any]] # ❌ Optional (default={})
```

### What Frontend is Sending (from `cofounderAgentClient.js`)

```javascript
{
  task_name: `Blog Post: ${topicOrOptions}`,
  topic: topicOrOptions,
  primary_keyword: primaryKeyword || '',
  target_audience: targetAudience || '',
  category: category || 'general',
  metadata: {},
}
```

## Possible Issues

### ✅ Issue 1: Missing Required Fields

- **What:** `task_name` or `topic` might be empty/undefined
- **Why:** User didn't fill in the form completely
- **Fix:** Add validation in TaskCreationModal to prevent empty submissions

### ⚠️ Issue 2: Empty String vs None

- **What:** Backend expects empty strings but gets `undefined`
- **Why:** JavaScript falsy values not properly converted
- **Fix:** Ensure all optional fields default to empty string `''`

### ⚠️ Issue 3: Metadata Not a Dict

- **What:** Metadata sent as wrong type (string instead of object)
- **Why:** JSON serialization issue
- **Fix:** Ensure metadata is always an object `{}`

### ❌ Issue 4: Extra Fields Not in Schema

- **What:** Sending fields that TaskCreateRequest doesn't expect
- **Why:** Pydantic strict mode rejects unknown fields
- **Fix:** Only send the 6 expected fields

## Solution

### Step 1: Fix Frontend Validation

Update `TaskCreationModal.jsx` to ensure form validation:

```jsx
const handleSubmit = async (e) => {
  e.preventDefault();

  // Strict validation
  if (!topic?.trim()) {
    setError('Blog topic is required');
    return;
  }
  if (!primaryKeyword?.trim()) {
    setError('Primary keyword is required');
    return;
  }
  if (!targetAudience?.trim()) {
    setError('Target audience is required');
    return;
  }
  if (!category?.trim()) {
    setError('Category is required');
    return;
  }

  // All fields valid, proceed with request
  setLoading(true);
  try {
    const response = await createBlogPost({
      topic: topic.trim(),
      primaryKeyword: primaryKeyword.trim(),
      targetAudience: targetAudience.trim(),
      category: category.trim(),
    });
    // ... handle success
  } catch (error) {
    setError(error.message);
  } finally {
    setLoading(false);
  }
};
```

### Step 2: Fix Backend Request Payload

In `cofounderAgentClient.js`, ensure exact field names:

```javascript
export async function createBlogPost(options) {
  const payload = {
    task_name: `Blog Post: ${options.topic}`,
    topic: String(options.topic || '').trim(),
    primary_keyword: String(options.primaryKeyword || '').trim(),
    target_audience: String(options.targetAudience || '').trim(),
    category: String(options.category || 'general').trim(),
    metadata: {}, // Always empty object, not string
  };

  // Validate no empty required fields
  if (!payload.topic) {
    throw new Error('Topic is required and cannot be empty');
  }
  if (!payload.task_name) {
    throw new Error('Task name is required');
  }

  return makeRequest('/api/tasks', 'POST', payload, false, null, 60000);
}
```

### Step 3: Add Debugging

Add console logging to see exact payload being sent:

```javascript
export async function createBlogPost(options) {
  const payload = {
    task_name: `Blog Post: ${options.topic}`,
    topic: String(options.topic || '').trim(),
    primary_keyword: String(options.primaryKeyword || '').trim(),
    target_audience: String(options.targetAudience || '').trim(),
    category: String(options.category || 'general').trim(),
    metadata: {},
  };

  // DEBUG: Log exact payload
  console.log('📤 Sending task payload:', JSON.stringify(payload, null, 2));
  console.log('✅ Validation:', {
    topic_valid: Boolean(payload.topic),
    task_name_valid: Boolean(payload.task_name),
    all_required_present: Boolean(payload.topic && payload.task_name),
  });

  return makeRequest('/api/tasks', 'POST', payload, false, null, 60000);
}
```

### Step 4: Backend Validation Error Response

Add better error responses in `task_routes.py`:

```python
@router.post("", response_model=Dict[str, Any], summary="Create new task", status_code=201)
async def create_task(
    request: TaskCreateRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a new task for content generation."""
    try:
        # Validate required fields
        if not request.task_name or not request.task_name.strip():
            raise HTTPException(
                status_code=422,
                detail="task_name is required and cannot be empty"
            )
        if not request.topic or not request.topic.strip():
            raise HTTPException(
                status_code=422,
                detail="topic is required and cannot be empty"
            )

        task_data = {
            "id": str(uuid_lib.uuid4()),
            "task_name": request.task_name.strip(),
            "topic": request.topic.strip(),
            "primary_keyword": request.primary_keyword.strip() if request.primary_keyword else "",
            "target_audience": request.target_audience.strip() if request.target_audience else "",
            "category": request.category.strip() if request.category else "general",
            "status": "pending",
            "agent_id": "content-agent",
            "user_id": current_user.get("id", "system"),
            "metadata": request.metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        task_id = await db_service.add_task(task_data)

        return {
            "id": task_id,
            "status": "pending",
            "created_at": task_data["created_at"],
            "message": "Task created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create task: {str(e)}"
        )
```

## Testing the Fix

### Test 1: Valid Request (Should Return 201)

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "Blog Post - AI in Healthcare",
    "topic": "How AI is Transforming Healthcare",
    "primary_keyword": "AI healthcare",
    "target_audience": "Healthcare professionals",
    "category": "healthcare",
    "metadata": {}
  }'
```

**Expected Response (201):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "created_at": "2024-01-15T10:30:00Z",
  "message": "Task created successfully"
}
```

### Test 2: Missing topic (Should Return 422)

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "Blog Post - No Topic",
    "topic": "",
    "primary_keyword": "AI healthcare",
    "target_audience": "Healthcare professionals",
    "category": "healthcare"
  }'
```

**Expected Response (422):**

```json
{
  "detail": "topic is required and cannot be empty"
}
```

## Summary

| Issue                    | Solution                            | Priority  |
| ------------------------ | ----------------------------------- | --------- |
| Form allows empty fields | Add strict validation before submit | 🔴 HIGH   |
| No debugging output      | Add console.log of payload          | 🟡 MEDIUM |
| Unclear error messages   | Add validation detail messages      | 🟡 MEDIUM |
| Type coercion issues     | Ensure all strings are trimmed      | 🟢 LOW    |

---

**Next Steps:**

1. ✅ Check browser console for the exact payload being sent
2. ✅ Implement form validation from Step 1
3. ✅ Add debugging from Step 3
4. ✅ Test with curl command above
5. ✅ Backend validation improvements from Step 4

If you still get 422 after these changes, copy the browser console output here and we'll debug further!
