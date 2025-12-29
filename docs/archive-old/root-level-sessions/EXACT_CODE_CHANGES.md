# Exact Code Changes - Quick Reference

## File 1: `src/cofounder_agent/routes/task_routes.py`

### Location: Lines 167-190 (TaskResponse class)

#### BEFORE:

```python
class TaskResponse(BaseModel):
    """Schema for task response"""
    id: str
    task_name: str
    agent_id: str
    status: str
    topic: str
    primary_keyword: Optional[str]
    target_audience: Optional[str]
    category: Optional[str]
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = {}
    result: Optional[Dict[str, Any]] = None

    class Config:
```

#### AFTER:

```python
class TaskResponse(BaseModel):
    """Schema for task response"""
    id: str
    task_name: str
    agent_id: str
    status: str
    topic: str
    primary_keyword: Optional[str]
    target_audience: Optional[str]
    category: Optional[str]
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = {}
    task_metadata: Dict[str, Any] = {}  # For orchestrator output (content, excerpt, qa_feedback, etc.)
    result: Optional[Dict[str, Any]] = None

    @property
    def title(self) -> str:
        """Alias for task_name for frontend compatibility"""
        return self.task_name

    @property
    def name(self) -> str:
        """Alias for task_name for frontend compatibility"""
        return self.task_name

    class Config:
```

#### Changes Made:

- Line 182: Added `task_metadata: Dict[str, Any] = {}`
- Lines 184-190: Added two @property methods for title and name aliases

---

## File 2: `web/oversight-hub/src/services/cofounderAgentClient.js`

### Location 1: After line 13 (new function)

#### ADDED:

```javascript
/**
 * Capitalize each word in a string
 * @param {string} str - The string to capitalize
 * @returns {string} - The capitalized string
 */
function capitalizeWords(str) {
  if (!str) return '';
  return str
    .split(' ')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}
```

### Location 2: Around line 209 (in createBlogPost function)

#### BEFORE:

```javascript
const payload = {
  task_name: `Blog Post: ${topicOrOptions.trim()}`,
  topic: topicOrOptions.trim(),
  primary_keyword: (primaryKeyword || '').trim(),
  target_audience: (targetAudience || '').trim(),
  category: (category || 'general').trim(),
  metadata: {},
};
```

#### AFTER:

```javascript
const payload = {
  task_name: `Blog Post: ${capitalizeWords(topicOrOptions.trim())}`,
  topic: topicOrOptions.trim(),
  primary_keyword: (primaryKeyword || '').trim(),
  target_audience: (targetAudience || '').trim(),
  category: (category || 'general').trim(),
  metadata: {},
};
```

#### Changes Made:

- Line ~209: Wrapped `topicOrOptions.trim()` with `capitalizeWords()` function call

---

## Summary of Changes

| File                      | Change Type    | Lines   | Impact                               |
| ------------------------- | -------------- | ------- | ------------------------------------ |
| `task_routes.py`          | Add field      | 182     | API now returns task_metadata field  |
| `task_routes.py`          | Add properties | 184-190 | Frontend can access .title and .name |
| `cofounderAgentClient.js` | Add function   | 16-24   | Provides text capitalization utility |
| `cofounderAgentClient.js` | Modify call    | 209     | Applies capitalization to task names |

---

## Verification Commands

### Test Python Syntax

```bash
cd c:\Users\mattm\glad-labs-website
python -m py_compile "src/cofounder_agent/routes/task_routes.py"
# Output: (no error = success)
```

### Test Changes (after backend starts)

```bash
# Create blog post with lowercase input
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "task_name": "Blog Post: making delicious muffins",
    "topic": "making delicious muffins",
    "primary_keyword": "muffins",
    "target_audience": "bakers",
    "category": "food"
  }'

# Expected response includes:
# "task_name": "Blog Post: Making Delicious Muffins",
# "title": "Blog Post: Making Delicious Muffins",
# "name": "Blog Post: Making Delicious Muffins",
# "task_metadata": { "content": "...", ... }
```

---

## No Other Files Modified

- ✅ No database migrations
- ✅ No schema changes
- ✅ No configuration files
- ✅ No environment variables
- ✅ No package.json or requirements.txt changes

---

## Rollback Instructions (if needed)

**To revert to previous state**:

1. In `task_routes.py` (lines 182-190):
   - Delete line 182: `task_metadata: Dict[str, Any] = {}`
   - Delete lines 184-190: The @property methods

2. In `cofounderAgentClient.js`:
   - Delete lines 16-24: The capitalizeWords() function
   - Change line 209: Replace `capitalizeWords(topicOrOptions.trim())` with `topicOrOptions.trim()`

**No database cleanup needed** - all changes are code-level only.
