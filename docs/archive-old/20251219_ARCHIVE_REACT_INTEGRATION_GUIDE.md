# React Integration Guide - LangGraph WebSocket Streaming

## ✅ Backend Status: READY FOR INTEGRATION

**All endpoints verified working:**

- ✅ POST `/api/content/langgraph/blog-posts` - Returns request_id + WebSocket endpoint
- ✅ WebSocket `/api/content/langgraph/ws/blog-posts/{request_id}` - Streams 5 phases

---

## Integration Steps

### Step 1: Verify React Components Exist

```bash
# Check if components exist
ls web/oversight-hub/src/hooks/useLangGraphStream.js
ls web/oversight-hub/src/components/LangGraphStreamProgress.jsx
```

**Expected:** Both files exist

### Step 2: Create a Test Page in Oversight Hub

**File:** `web/oversight-hub/src/pages/LangGraphTest.jsx`

```jsx
import React, { useState } from 'react';
import { Box, Container, Button, TextField } from '@mui/material';
import LangGraphStreamProgress from '../components/LangGraphStreamProgress';

export default function LangGraphTestPage() {
  const [requestId, setRequestId] = useState(null);
  const [blogTopic, setBlogTopic] = useState('Python Testing Best Practices');

  const handleCreateBlog = async () => {
    try {
      const response = await fetch(
        'http://localhost:8000/api/content/langgraph/blog-posts',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            topic: blogTopic,
            keywords: ['test', 'automation'],
            audience: 'developers',
            tone: 'technical',
            word_count: 1500,
          }),
        }
      );

      const data = await response.json();
      setRequestId(data.request_id);
    } catch (error) {
      console.error('Error creating blog:', error);
    }
  };

  const handleComplete = (result) => {
    console.log('Blog generation complete:', result);
    alert('Blog created successfully!');
  };

  const handleError = (error) => {
    console.error('Error during generation:', error);
    alert(`Error: ${error}`);
  };

  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Box sx={{ mb: 3 }}>
        <TextField
          fullWidth
          label="Blog Topic"
          value={blogTopic}
          onChange={(e) => setBlogTopic(e.target.value)}
          variant="outlined"
          sx={{ mb: 2 }}
        />
        <Button
          variant="contained"
          onClick={handleCreateBlog}
          disabled={!!requestId}
        >
          Create Blog Post
        </Button>
      </Box>

      {requestId && (
        <LangGraphStreamProgress
          requestId={requestId}
          onComplete={handleComplete}
          onError={handleError}
        />
      )}
    </Container>
  );
}
```

### Step 3: Add Route to Oversight Hub

**File:** `web/oversight-hub/src/App.jsx`

```jsx
import LangGraphTestPage from './pages/LangGraphTest';

// Add to your routing
<Route path="/langgraph-test" element={<LangGraphTestPage />} />;
```

### Step 4: Test in Browser

1. Navigate to: `http://localhost:3000/langgraph-test` (or your Oversight Hub port)
2. Click "Create Blog Post"
3. Watch the progress Stepper update in real-time
4. See phases: Research → Outline → Draft → Quality Check → Finalization

---

## Component API Reference

### `useLangGraphStream` Hook

```javascript
const progress = useLangGraphStream(requestId);

// Returns:
{
  phase: "research|outline|draft|assess|finalize",
  progress: 0-100,
  status: "waiting|in_progress|completed|error",
  content: "current draft excerpt",
  quality: 0-100,
  refinements: 0-3,
  error: "error message if any",
  phases: [
    { name: "Research", completed: false },
    { name: "Outline", completed: false },
    // ... etc
  ]
}
```

### `LangGraphStreamProgress` Component

```jsx
<LangGraphStreamProgress
  requestId="request-id-123"
  onComplete={(result) => console.log('Done!', result)}
  onError={(error) => console.error('Error!', error)}
/>

// Renders:
// - Material-UI Stepper with 5 phases
// - Linear Progress bar (0-100%)
// - Quality assessment card
// - Content preview card
// - Completion alert
```

---

## WebSocket Message Format

### Progress Message

```json
{
  "type": "progress",
  "node": "research|outline|draft|assess|finalize",
  "progress": 15|30|50|70|100,
  "status": "processing"
}
```

### Complete Message

```json
{
  "type": "complete",
  "request_id": "uuid",
  "status": "completed"
}
```

### Error Message

```json
{
  "type": "error",
  "error": "error description"
}
```

---

## Testing Checklist

- [ ] React hook loads without errors
- [ ] Component renders Stepper with 5 phases
- [ ] HTTP POST returns 202 status + request_id
- [ ] WebSocket connects successfully
- [ ] Progress messages update Stepper
- [ ] All 5 phases complete in order
- [ ] Completion callback fires
- [ ] Error handling works (disconnect WebSocket)

---

## Common Issues & Fixes

### Issue: WebSocket connection fails

**Fix:** Ensure backend is running on port 8000

```bash
curl http://localhost:8000/docs
# Should show FastAPI Swagger UI
```

### Issue: Progress not updating

**Fix:** Check browser console for WebSocket errors

```javascript
// Add to useLangGraphStream hook
ws.onerror = (event) => console.error('WebSocket error:', event);
```

### Issue: CORS errors

**Fix:** Backend has CORS enabled for localhost

```python
# In main.py - already configured
CORSMiddleware(app, allow_origins=["*"], ...)
```

### Issue: React component not rendering

**Fix:** Check if component files exist

```bash
ls web/oversight-hub/src/components/LangGraphStreamProgress.jsx
# Should exist
```

---

## Next: Production Deployment

Once testing complete:

1. Remove test page
2. Integrate into main content creation workflow
3. Add authentication back to HTTP endpoint
4. Connect to database for persistence
5. Add error recovery and retry logic

---

## Support

**Backend working?** Run test:

```bash
python test_langgraph_integration.py
```

**React component ready?** Check files:

```bash
cat web/oversight-hub/src/components/LangGraphStreamProgress.jsx
```

**Need to debug?** Check logs:

```bash
# Backend logs show WebSocket connections
# Browser console shows React component rendering
# Network tab shows WebSocket traffic
```
