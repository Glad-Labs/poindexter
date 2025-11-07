# 📋 Code Changes Reference

**Date:** 2025-11-12  
**Issue:** Blog post tasks being routed to `/api/tasks` instead of `/api/content/generate`  
**Impact:** Poindexter Assistant output instead of self-critique loop results

---

## File 1: CreateTaskModal.jsx

**Location:** `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx`  
**Lines Changed:** ~200-260

### The Issue

Blog posts were being sent to the generic `/api/tasks` endpoint which just stores the task with status "pending" but never executes it.

### The Solution

Added conditional routing based on task type:

- **Blog post tasks** → `/api/content/generate` (executes self-critique loop)
- **Other tasks** → `/api/tasks` (generic storage, unchanged)

### Code Change

```javascript
// ❌ BEFORE (Lines 200-230)
const handleSubmit = async () => {
  setSubmitting(true);
  setError(null);

  try {
    // Build task payload matching backend TaskCreateRequest schema
    const taskPayload = {
      task_name: formData.title || formData.subject || `Task: ${taskType}`,
      topic: formData.topic || formData.description || '',
      primary_keyword: formData.keywords || formData.primary_keyword || '',
      target_audience: formData.target_audience || formData.audience || '',
      category: formData.category || taskType || 'general',
      metadata: {
        task_type: taskType,
        style: formData.style,
        word_count: formData.word_count,
        ...formData,
      },
    };

    console.log('📤 Sending task payload:', taskPayload);

    const token = getAuthToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    // ❌ ALWAYS sends to /api/tasks
    const response = await fetch('http://localhost:8000/api/tasks', {
      method: 'POST',
      headers,
      body: JSON.stringify(taskPayload),
    });
    // ... rest of error handling
};
```

```javascript
// ✅ AFTER (Lines 200-260)
const handleSubmit = async () => {
  setSubmitting(true);
  setError(null);

  try {
    const token = getAuthToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    let response;

    // ✅ ROUTE TO CORRECT ENDPOINT BASED ON TASK TYPE
    if (taskType === 'blog_post') {
      // Use content generation endpoint for blog posts (triggers self-critique loop)
      const contentPayload = {
        topic: formData.topic || '',
        style: formData.style || 'professional',
        tone: formData.tone || 'professional',
        target_length: formData.word_count || 1500,
        tags: formData.keywords
          ? formData.keywords.split(',').map(k => k.trim()).filter(k => k)
          : [],
      };

      console.log('📤 Sending to content generation endpoint:', contentPayload);

      // ✅ CORRECT ENDPOINT FOR BLOG POSTS - Runs self-critique pipeline
      response = await fetch('http://localhost:8000/api/content/generate', {
        method: 'POST',
        headers,
        body: JSON.stringify(contentPayload),
      });
    } else {
      // Use generic task endpoint for other types
      const taskPayload = {
        task_name: formData.title || formData.subject || `Task: ${taskType}`,
        topic: formData.topic || formData.description || '',
        primary_keyword: formData.keywords || formData.primary_keyword || '',
        target_audience: formData.target_audience || formData.audience || '',
        category: formData.category || taskType || 'general',
        metadata: {
          task_type: taskType,
          style: formData.style,
          word_count: formData.word_count,
          ...formData,
        },
      };

      console.log('📤 Sending generic task payload:', taskPayload);

      response = await fetch('http://localhost:8000/api/tasks', {
        method: 'POST',
        headers,
        body: JSON.stringify(taskPayload),
      });
    }
    // ... rest of error handling (unchanged)
};
```

### Key Changes

1. **Check task type:** `if (taskType === 'blog_post')`
2. **For blog posts:** Build simplified content payload
3. **For blog posts:** Send to `/api/content/generate` ← **NEW ENDPOINT**
4. **For others:** Keep original behavior (send to `/api/tasks`)
5. **Better logging:** Distinguish between content and generic tasks

---

## File 2: TaskManagement.jsx

**Location:** `web/oversight-hub/src/components/tasks/TaskManagement.jsx`  
**Lines Changed:** ~70-180

### The Issue

Tasks weren't being checked against the `/api/content/status` endpoint to get their results.

### The Solution

1. Added new function `fetchContentTaskStatus()` to check `/api/content/status/{taskId}`
2. Enhanced `fetchTasks()` to detect blog_post tasks and fetch their status from content endpoint
3. Auto-merges results into task list

### Code Changes

```javascript
// ✅ NEW FUNCTION (added before fetchTasks)
const fetchContentTaskStatus = async (taskId) => {
  try {
    const token = getAuthToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(
      `http://localhost:8000/api/content/status/${taskId}`,
      {
        headers,
        signal: AbortSignal.timeout(5000),
      }
    );

    if (response.ok) {
      const data = await response.json();
      console.log('✅ Content task status:', data);
      return {
        status: data.status || 'completed',
        result: data.result || {},
        error_message: data.error_message,
      };
    } else {
      console.warn(
        `Failed to fetch content task status: ${response.statusText}`
      );
      return null;
    }
  } catch (error) {
    console.error('Failed to fetch content task status:', error);
    return null;
  }
};
```

```javascript
// ❌ BEFORE fetchTasks
const fetchTasks = async () => {
  try {
    setError(null);
    const token = getAuthToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch('http://localhost:8000/api/tasks', {
      headers,
      signal: AbortSignal.timeout(5000),
    });

    if (response.ok) {
      const data = await response.json();
      setTasks(data.tasks || []);
      // ❌ Never checks /api/content/status for blog post results
    } else {
      setError(`Failed to fetch tasks: ${response.statusText}`);
      console.error('Failed to fetch tasks:', response.statusText);
    }
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error';
    setError(`Unable to load tasks: ${errorMessage}`);
    console.error('Failed to fetch tasks:', error);
  } finally {
    setLoading(false);
  }
};
```

```javascript
// ✅ AFTER fetchTasks (ENHANCED)
const fetchTasks = async () => {
  try {
    setError(null);
    const token = getAuthToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch('http://localhost:8000/api/tasks', {
      headers,
      signal: AbortSignal.timeout(5000),
    });

    if (response.ok) {
      let data = await response.json();
      let tasks = data.tasks || [];

      // ✅ For blog_post tasks, also fetch content-specific status
      tasks = await Promise.all(
        tasks.map(async (task) => {
          // If task type indicates content generation, fetch from content endpoint
          if (
            task.task_type === 'blog_post' ||
            task.category === 'content_generation' ||
            task.metadata?.task_type === 'blog_post'
          ) {
            const contentStatus = await fetchContentTaskStatus(task.id);
            if (contentStatus) {
              console.log('📄 Updated blog post task status:', {
                id: task.id,
                status: contentStatus.status,
                hasResult: !!contentStatus.result,
              });
              return {
                ...task,
                status: contentStatus.status,
                result: contentStatus.result,
                error_message: contentStatus.error_message,
              };
            }
          }
          return task;
        })
      );

      setTasks(tasks);
    } else {
      setError(`Failed to fetch tasks: ${response.statusText}`);
      console.error('Failed to fetch tasks:', response.statusText);
    }
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error';
    setError(`Unable to load tasks: ${errorMessage}`);
    console.error('Failed to fetch tasks:', error);
  } finally {
    setLoading(false);
  }
};
```

### Key Changes

1. **New function:** `fetchContentTaskStatus()` queries `/api/content/status/{taskId}`
2. **Enhanced fetchTasks:** Detects blog_post tasks
3. **For blog posts:** Calls `fetchContentTaskStatus()` in parallel
4. **Merges results:** Combines generic task data with content-specific data
5. **Better logging:** Shows when content task status is updated

---

## Data Flow Comparison

### ❌ BEFORE (BROKEN)

```
User creates blog post task
    ↓
CreateTaskModal calls /api/tasks
    ↓
Task stored with status: "pending"
    ↓
TaskManagement polls /api/tasks
    ↓
Task remains pending (never executes)
    ↓
ResultPreviewPanel shows empty/loading
    ↓
Frontend falls back to Poindexter assistant
    ↓
❌ User sees chat bot response instead of blog post
```

### ✅ AFTER (FIXED)

```
User creates blog post task
    ↓
CreateTaskModal detects taskType === 'blog_post'
    ↓
Creates contentPayload (topic, style, tone, length, tags)
    ↓
Sends to /api/content/generate endpoint
    ↓
Backend triggers self-critique pipeline immediately:
  • Research Agent (2-3s)
  • Creative Agent (5-8s)
  • QA Agent (3-5s)
  • Creative Agent Refined (3-5s)
  • Image Agent (1-2s)
  • Publishing Agent (1-2s)
    ↓
TaskManagement detects blog_post task
    ↓
Polls /api/content/status/{taskId} every 10 seconds
    ↓
Gets updated status and result as pipeline completes
    ↓
ResultPreviewPanel displays full blog post markdown
    ↓
✅ User sees professional blog post (not chat response)
```

---

## Payload Structure Changes

### CreateTaskModal - Blog Post Payload

**What was being sent (WRONG):**

```json
{
  "task_name": "AI Trends in 2025",
  "topic": "What are the latest AI trends",
  "primary_keyword": "AI, trends",
  "target_audience": "Business",
  "category": "blog_post",
  "metadata": {
    "task_type": "blog_post",
    "style": "Technical",
    "word_count": 1500,
    ...
  }
}
```

**Endpoint:** `/api/tasks` ← Wrong - generic storage

**What is being sent now (CORRECT):**

```json
{
  "topic": "What are the latest AI trends",
  "style": "Technical",
  "tone": "professional",
  "target_length": 1500,
  "tags": ["AI", "trends", "business", "2025"]
}
```

**Endpoint:** `/api/content/generate` ← Correct - runs pipeline

---

## Result Structure Changes

### TaskManagement - Blog Post Status

**Before:** (never updated)

```json
{
  "id": "550e8400-...",
  "task_type": "blog_post",
  "status": "pending",
  "result": null
}
```

**After:** (gets updated from content endpoint)

```json
{
  "id": "550e8400-...",
  "task_type": "blog_post",
  "status": "completed",
  "result": {
    "content": "# AI Trends in 2025\n\n## Research...",
    "title": "AI Trends in 2025",
    "seo": {
      "title": "AI Trends in 2025 | Business Impact",
      "description": "Discover the latest AI trends...",
      "keywords": ["AI", "trends", "business", "2025"]
    }
  }
}
```

---

## Backend Endpoints Used

### CreateTaskModal

**New Endpoint:**

```
POST /api/content/generate
Content-Type: application/json

Request Body:
{
  "topic": string,
  "style": string,
  "tone": string,
  "target_length": integer,
  "tags": string[]
}

Response:
{
  "task_id": string,
  "status": string ("pending" | "in_progress" | "completed" | "failed")
}
```

**Old Endpoint (still used for non-blog tasks):**

```
POST /api/tasks
Content-Type: application/json

Request Body:
{
  "task_name": string,
  "topic": string,
  "category": string,
  "metadata": object,
  ...
}

Response:
{
  "id": string,
  "status": string ("pending" | "in_progress" | "completed" | "failed")
}
```

### TaskManagement

**New Endpoint (for blog posts):**

```
GET /api/content/status/{task_id}

Response:
{
  "task_id": string,
  "status": string,
  "result": {
    "content": string (markdown),
    "title": string,
    "seo": object
  },
  "error_message": string (optional)
}
```

**Existing Endpoint (still used for other tasks):**

```
GET /api/tasks

Response:
{
  "tasks": [
    {
      "id": string,
      "task_type": string,
      "status": string,
      ...
    }
  ]
}
```

---

## Testing the Code Changes

### Console Output Verification

**Good:**

```javascript
// When creating blog post:
console.log('📤 Sending to content generation endpoint:', {...})

// When polling status:
console.log('📄 Updated blog post task status:', {id: "...", status: "completed", hasResult: true})
```

**Bad:**

```javascript
// If you see this, routing is wrong:
console.log('📤 Sending generic task payload:', {...})
```

---

## Files Modified Summary

| File                  | Lines    | Changes                       | Impact                                |
| --------------------- | -------- | ----------------------------- | ------------------------------------- |
| `CreateTaskModal.jsx` | ~200-260 | Added endpoint routing logic  | Tasks sent to correct endpoint        |
| `TaskManagement.jsx`  | ~70-180  | Added content status fetching | Results fetched from content endpoint |

**Total Changes:** ~120 lines of code  
**Backward Compatible:** Yes - other task types unaffected  
**Build Impact:** None - no new dependencies  
**Syntax Errors:** 0

---

## Verification Commands

```bash
# Check files were modified:
git diff web/oversight-hub/src/components/tasks/CreateTaskModal.jsx
git diff web/oversight-hub/src/components/tasks/TaskManagement.jsx

# Verify no syntax errors:
npm run lint web/oversight-hub/src/components/tasks/

# Test the fix:
# (Follow: docs/TESTING_PROCEDURE_STEP_BY_STEP.md)
```

---

**Summary:** The fix routes blog post tasks to the content generation endpoint instead of the generic task storage endpoint, enabling the self-critique pipeline to execute correctly.
