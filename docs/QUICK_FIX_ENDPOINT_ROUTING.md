# 🔧 QUICK FIX - Route Blog Posts to Correct Endpoint

## The Change Required

**File:** `web/oversight-hub/src/components/tasks/CreateTaskModal.jsx`

**Line:** Around 220 (in the `handleSubmit` function)

---

## Current Code (BROKEN)

```javascript
// Lines 197-230 - Current implementation
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

    // ❌ WRONG ENDPOINT - Just stores task, doesn't execute
    const response = await fetch('http://localhost:8000/api/tasks', {
      method: 'POST',
      headers,
      body: JSON.stringify(taskPayload),
    });
```

---

## Fixed Code (CORRECT)

```javascript
// Lines 197-230 - FIXED implementation
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
          ? formData.keywords
              .split(',')
              .map((k) => k.trim())
              .filter((k) => k)
          : [],
      };

      console.log('📤 Sending to content generation:', contentPayload);

      // ✅ CORRECT ENDPOINT FOR BLOG POSTS
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

      console.log('📤 Sending generic task:', taskPayload);

      // Generic task endpoint for non-content tasks
      response = await fetch('http://localhost:8000/api/tasks', {
        method: 'POST',
        headers,
        body: JSON.stringify(taskPayload),
      });
    }

    // Rest of the code stays the same...
    if (!response.ok) {
      let errorMessage = `Failed to create task: ${response.statusText}`;
      try {
        const errorData = await response.json();
        console.error('❌ Backend error response:', errorData);
        if (errorData.detail) {
          errorMessage = Array.isArray(errorData.detail)
            ? errorData.detail
                .map((d) => `${d.loc?.join('.')}: ${d.msg}`)
                .join('; ')
            : errorData.detail;
        }
      } catch (parseError) {
        // Couldn't parse JSON error response, use status text
      }
      throw new Error(errorMessage);
    }

    // Success response
    const result = await response.json();
    console.log('✅ Task created successfully:', result);

    // For blog posts, result will have task_id to check status
    // For other tasks, result will have the task object

    // Notify parent and reset
    onTaskCreated();
    setTaskType('');
    setFormData({});
  } catch (err) {
    setError(`Failed to create task: ${err.message}`);
    console.error('Task creation error:', err);
  } finally {
    setSubmitting(false);
  }
};
```

---

## Key Changes

1. **Check task type** before sending
2. **For blog_post tasks:**
   - Send to `/api/content/generate` (NEW)
   - Use simplified content payload (topic, style, tone, target_length, tags)
   - Returns `task_id` to track progress

3. **For other task types:**
   - Still send to `/api/tasks` (unchanged)
   - Use original task payload

4. **Result handling:**
   - Blog posts: Check `/api/content/status/{task_id}` for results
   - Other tasks: Use `/api/tasks/{task_id}` for results

---

## Result Preview Panel Update (Optional)

The `ResultPreviewPanel.jsx` should also be updated to:

```javascript
// Check endpoint based on source
if (source === 'blog_post' || source === 'content') {
  // Check /api/content/status/{taskId}
  const response = await fetch(
    `http://localhost:8000/api/content/status/${taskId}`
  );
  const data = await response.json();
  // data.result.content contains the blog post markdown
} else {
  // Check /api/tasks/{taskId}
  const response = await fetch(`http://localhost:8000/api/tasks/${taskId}`);
  const data = await response.json();
  // data.result contains task result
}
```

---

## Testing After Fix

1. **Create a blog post task:**
   - Title: "AI in Gaming"
   - Topic: "How AI is changing gaming"
   - Style: "Technical"
   - Word Count: 2000

2. **In browser console, you should see:**

   ```
   📤 Sending to content generation: {topic: "...", style: "...", ...}
   ✅ Task created successfully: {task_id: "uuid-...", status: "pending"}
   ```

3. **Check status (paste in browser console):**

   ```javascript
   fetch('http://localhost:8000/api/content/status/YOUR_TASK_ID')
     .then((r) => r.json())
     .then((d) => console.log(d.result.content));
   ```

4. **You should see:**
   - ✅ Full blog post markdown (NOT Poindexter assistant chat)
   - ✅ Research data in the content
   - ✅ Properly formatted sections
   - ✅ Professional writing (from self-critique loop)

---

## Files to Update

| File                     | Changes                        | Priority    |
| ------------------------ | ------------------------------ | ----------- |
| `CreateTaskModal.jsx`    | Add endpoint routing logic     | 🔴 CRITICAL |
| `ResultPreviewPanel.jsx` | Update status check URLs       | 🟡 HIGH     |
| `TaskManagement.jsx`     | Optional: Better polling logic | 🟢 MEDIUM   |

---

## Summary

- **Problem:** Wrong endpoint being called
- **Solution:** Route blog_post to `/api/content/generate`
- **Benefit:** Triggers full self-critique pipeline instead of Poindexter assistant
- **Result:** Get actual blog posts, not chat responses
- **Time to Fix:** 5-10 minutes
- **Impact:** High - Fixes entire content generation workflow
