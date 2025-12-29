# 🚀 LangGraph Quick Start - Running Now

**Status**: ✅ Implementation complete and ready to use  
**Time to first test**: 5 minutes

---

## Quick Start

### 1. Verify Installation (2 minutes)

```bash
# Test imports
cd src/cofounder_agent && python -c "
from services.langgraph_graphs.states import ContentPipelineState
from services.langgraph_graphs.content_pipeline import create_content_pipeline_graph
from services.langgraph_orchestrator import LangGraphOrchestrator
print('✅ LangGraph ready!')
"
```

### 2. Start Backend (30 seconds)

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload --port 8000
```

**Look for this in logs:**

```
✅ LangGraphOrchestrator initialized
```

### 3. Test API (2 minutes)

**Option A: HTTP Request**

```bash
curl -X POST http://localhost:8000/api/content/langgraph/blog-posts \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "AI Safety",
    "keywords": ["AI", "safety", "regulations"],
    "word_count": 1200
  }'
```

**Option B: JavaScript**

```javascript
const response = await fetch(
  'http://localhost:8000/api/content/langgraph/blog-posts',
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      topic: 'AI Safety',
      keywords: ['AI', 'safety'],
      word_count: 1200,
    }),
  }
);
const data = await response.json();
console.log(data.request_id); // Use for WebSocket
```

### 4. Stream Progress (1 minute)

```javascript
// React component (copy-paste ready)
import LangGraphStreamProgress from './components/LangGraphStreamProgress';

function App() {
  const [requestId, setRequestId] = useState('abc123'); // From API response

  return (
    <LangGraphStreamProgress
      requestId={requestId}
      onComplete={(result) => console.log('✅ Done!', result)}
      onError={(err) => console.error('❌ Error:', err)}
    />
  );
}
```

---

## What's Working Now

### ✅ Backend

- [x] LangGraph service files created
- [x] Content pipeline graph (6 nodes)
- [x] Orchestrator with sync + streaming modes
- [x] FastAPI integration complete
- [x] Service dependency injection
- [x] Error handling

### ✅ API Endpoints

- [x] `POST /api/content/langgraph/blog-posts` - Create blog
- [x] `WebSocket /api/content/langgraph/ws/blog-posts/{id}` - Stream progress

### ✅ Frontend

- [x] React hook for WebSocket: `useLangGraphStream()`
- [x] Progress component: `LangGraphStreamProgress`
- [x] Stepper UI, progress bar, quality card

---

## Key Features

### 📊 Real-Time Progress Streaming

- WebSocket connection for live updates
- Shows current phase (research → outline → draft → quality → finalize)
- Displays quality scores and refinement count
- Automatic phase completion tracking

### 🔄 Refinement Loops

- Graph automatically loops when quality < threshold
- Max 3 refinement attempts (configurable)
- Quality feedback drives improvements

### 🛡️ Error Handling

- Graceful degradation if LLM unavailable
- WebSocket error recovery
- Detailed error messages in UI

### 📈 Scalable Architecture

- Parallel to existing system (no breaking changes)
- Can run both old and new orchestrators simultaneously
- Easy to add new graph types (financial, compliance, etc.)

---

## Next: Integrate With Your UI

### Add to Oversight Hub

**Step 1: Create new page**

```jsx
// web/oversight-hub/src/pages/BlogCreatorWithLangGraph.jsx

import React, { useState } from 'react';
import { Box, Button, TextField, Card } from '@mui/material';
import LangGraphStreamProgress from '../components/LangGraphStreamProgress';

export function BlogCreatorWithLangGraph() {
  const [requestId, setRequestId] = useState(null);
  const [topic, setTopic] = useState('');

  async function handleCreate() {
    const res = await fetch('/api/content/langgraph/blog-posts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, keywords: [], word_count: 800 }),
    });
    const data = await res.json();
    setRequestId(data.request_id);
  }

  return (
    <Box sx={{ p: 2 }}>
      <Card sx={{ p: 2, mb: 2 }}>
        <TextField
          fullWidth
          label="Blog Topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <Button onClick={handleCreate} variant="contained" sx={{ mt: 1 }}>
          Create with LangGraph
        </Button>
      </Card>

      {requestId && (
        <LangGraphStreamProgress
          requestId={requestId}
          onComplete={(result) => alert(`Done! Quality: ${result.quality}/100`)}
        />
      )}
    </Box>
  );
}
```

**Step 2: Add route**

```jsx
// In routes or navigation
<Route path="/blog-creator-langgraph" element={<BlogCreatorWithLangGraph />} />
```

---

## Understanding the Architecture

### How It Works

```
User clicks "Create Blog"
    ↓
POST /api/content/langgraph/blog-posts
    ↓
LangGraphOrchestrator.execute_content_pipeline()
    ↓
Graph execution (6 sequential nodes):
  1. research_phase → Gathers topic info
  2. outline_phase → Creates structure
  3. draft_phase → Writes content
  4. assess_quality → Evaluates (score 0-100)
  5. [DECISION] → If score < 80 and attempts < 3:
       └─ refine_phase → Improves content (loop to assess)
     → Else: Continue to finalize
  6. finalize_phase → Saves to DB
    ↓
Streams progress via WebSocket
    ↓
Frontend updates Stepper, progress bar, quality score
    ↓
Completion alert shows final quality score
```

### State Management

```
ContentPipelineState = {
  // Input (user provided)
  topic: string,
  keywords: [string],
  audience: string,
  tone: string,
  word_count: int,

  // Processing (filled by nodes)
  research_notes: string,
  outline: string,
  draft: string,
  final_content: string,

  // Quality tracking
  quality_score: float (0-100),
  quality_feedback: string,
  passed_quality: bool,
  refinement_count: int,

  // Output
  task_id: string,
  status: "in_progress" | "completed" | "failed",

  // Tracking
  messages: [MessageDict],
  errors: [string]
}
```

State automatically flows through graph nodes. Each node receives full state, modifies what it needs, returns updated state.

---

## Troubleshooting

### Issue: "LangGraph orchestrator not available"

**Cause:** Main.py startup failed  
**Fix:**

```bash
# Check FastAPI logs during startup
cd src/cofounder_agent
python -m uvicorn main:app --reload 2>&1 | grep -i langgraph
```

### Issue: WebSocket connection fails

**Cause:** Incorrect endpoint URL  
**Fix:**

```javascript
// Verify endpoint format
const requestId = 'abc-123-def';
const url = `ws://localhost:8000/api/content/langgraph/ws/blog-posts/${requestId}`;
console.log('Connecting to:', url);
```

### Issue: No progress updates

**Cause:** WebSocket message format mismatch  
**Fix:**

```javascript
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Message:', data); // Debug what's received
  // Check: { type: "progress", node: "research", progress: 15 }
};
```

---

## Performance Tips

### Optimize for Speed

```javascript
// Pre-fetch graph during app startup
useEffect(() => {
  fetch('/api/content/langgraph/blog-posts', {
    method: 'POST',
    body: JSON.stringify(initialData),
  });
}, []);
```

### Batch Multiple Requests

```javascript
// Don't wait for each to complete
const requests = ['Topic1', 'Topic2', 'Topic3'].map((topic) =>
  fetch('/api/content/langgraph/blog-posts', {
    method: 'POST',
    body: JSON.stringify({ topic }),
  }).then((r) => r.json())
);

Promise.all(requests).then((results) => {
  results.forEach((r) => subscribeToStream(r.request_id));
});
```

### Monitor Quality Scores

```javascript
// Track refinement effectiveness
if (progress.quality > 75) {
  console.log('✅ Content approved!');
  // Auto-publish or save
}
```

---

## Files Reference

| Path                                            | Purpose              | LOC  |
| ----------------------------------------------- | -------------------- | ---- |
| `services/langgraph_orchestrator.py`            | Main service wrapper | 150  |
| `services/langgraph_graphs/states.py`           | State definitions    | 70   |
| `services/langgraph_graphs/content_pipeline.py` | Graph nodes          | 350  |
| `routes/content_routes.py`                      | API endpoints        | +150 |
| `hooks/useLangGraphStream.js`                   | React hook           | 80   |
| `components/LangGraphStreamProgress.jsx`        | Progress UI          | 200  |

---

## What's Next?

### Phase 1: Test & Validate (This week)

- [ ] Create sample blogs and verify quality
- [ ] Test with real LLM (not mock)
- [ ] Check WebSocket reliability under load
- [ ] Monitor database saves

### Phase 2: Production Deployment (Next week)

- [ ] Enable in staging environment
- [ ] Load test concurrent requests
- [ ] Set up monitoring/alerts
- [ ] Team training

### Phase 3: Expand Workflows (Following weeks)

- [ ] Create financial analysis workflow
- [ ] Create compliance workflow
- [ ] Multi-agent patterns (collaboration, handoff)
- [ ] Deprecate old orchestrators

---

## Support

**Documentation:**

- Full analysis: [LANGGRAPH_INTEGRATION_ANALYSIS.md](./LANGGRAPH_INTEGRATION_ANALYSIS.md)
- Implementation details: [LANGGRAPH_IMPLEMENTATION_GUIDE.md](./LANGGRAPH_IMPLEMENTATION_GUIDE.md)
- Complete build log: [LANGGRAPH_IMPLEMENTATION_COMPLETE.md](./LANGGRAPH_IMPLEMENTATION_COMPLETE.md)

**Questions? Check:**

1. The debug WebSocket section above
2. FastAPI logs for startup errors
3. Browser console for frontend errors
4. API response codes (202 = async accepted, 503 = orchestrator unavailable)

---

**Status: 🚀 Ready to use!**

Start with Quick Start #1 and #2 above. You'll be streaming blog creation progress in 5 minutes.
