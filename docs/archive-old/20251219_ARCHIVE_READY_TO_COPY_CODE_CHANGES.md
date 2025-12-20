# Ready-to-Copy Code Changes

**What this is:** Exact code to copy-paste for integration  
**Time to complete:** 2.5 hours  
**Result:** ModelSelectionPanel fully integrated

---

## Change 1: Update TaskCreationModal.jsx

**File:** `web/oversight-hub/src/components/TaskCreationModal.jsx`

**Find this section** (imports at top):

```jsx
import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  // ... other imports
} from '@mui/material';
```

**Replace with:**

```jsx
import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  Box,
  Alert,
  // ... other imports
} from '@mui/material';
import ModelSelectionPanel from './ModelSelectionPanel'; // ← ADD THIS
```

---

**Find the component state section:**

```jsx
const [formData, setFormData] = useState({
  title: '',
  description: '',
  topic: '',
});
const [loading, setLoading] = useState(false);
```

**Replace with:**

```jsx
const [formData, setFormData] = useState({
  title: '',
  description: '',
  topic: '',
});
const [modelSelection, setModelSelection] = useState({
  // ← ADD THIS
  modelSelections: {
    research: 'auto',
    outline: 'auto',
    draft: 'auto',
    assess: 'auto',
    refine: 'auto',
    finalize: 'auto',
  },
  qualityPreference: 'balanced',
  estimatedCost: 0.015,
});
const [loading, setLoading] = useState(false);
```

---

**Find the form JSX (inside DialogContent):**

```jsx
<DialogContent>
  <TextField
    fullWidth
    label="Title"
    value={formData.title}
    onChange={(e) => setFormData({ ...formData, title: e.target.value })}
    margin="normal"
  />
  <TextField
    fullWidth
    label="Description"
    value={formData.description}
    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
    margin="normal"
    multiline
    rows={3}
  />
  {/* ... more fields ... */}
</DialogContent>
```

**Add ModelSelectionPanel after the other fields:**

```jsx
<DialogContent>
  <TextField
    fullWidth
    label="Title"
    value={formData.title}
    onChange={(e) => setFormData({ ...formData, title: e.target.value })}
    margin="normal"
  />
  <TextField
    fullWidth
    label="Description"
    value={formData.description}
    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
    margin="normal"
    multiline
    rows={3}
  />

  {/* ← ADD THIS SECTION */}
  <Box sx={{ mt: 3 }}>
    <ModelSelectionPanel
      onSelectionChange={(selection) => setModelSelection(selection)}
      initialQuality="balanced"
    />
  </Box>
  {/* ← END ADD */}

  {/* ... rest of fields ... */}
</DialogContent>
```

---

**Find the form submission handler:**

```jsx
const handleSubmit = async () => {
  try {
    setLoading(true);
    const response = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: formData.title,
        description: formData.description,
        topic: formData.topic,
        // ... other fields
      }),
    });
    // ... rest of handler
  }
};
```

**Update the fetch body:**

```jsx
const handleSubmit = async () => {
  try {
    setLoading(true);
    const response = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: formData.title,
        description: formData.description,
        topic: formData.topic,
        // ← ADD THESE THREE LINES
        modelSelections: modelSelection.modelSelections,
        qualityPreference: modelSelection.qualityPreference,
        estimatedCost: modelSelection.estimatedCost,
        // ← END ADD
        // ... other fields
      }),
    });
    // ... rest of handler
  }
};
```

---

## Change 2: Update TaskDetailModal.jsx

**File:** `web/oversight-hub/src/components/TaskDetailModal.jsx`

**Find the component state:**

```jsx
const [task, setTask] = useState(null);
const [loading, setLoading] = useState(false);
```

**Replace with:**

```jsx
const [task, setTask] = useState(null);
const [taskCosts, setTaskCosts] = useState(null); // ← ADD THIS
const [loading, setLoading] = useState(false);
```

---

**Find the useEffect that loads task data:**

```jsx
useEffect(() => {
  if (taskId) {
    fetchTask();
  }
}, [taskId]);

const fetchTask = async () => {
  // ... fetch task data
};
```

**Add a new useEffect for costs:**

```jsx
useEffect(() => {
  if (taskId) {
    fetchTask();
    fetchTaskCosts(); // ← ADD THIS
  }
}, [taskId]);

const fetchTask = async () => {
  // ... existing code
};

// ← ADD THIS NEW FUNCTION
const fetchTaskCosts = async () => {
  try {
    const response = await fetch(`/api/tasks/${taskId}/costs`);
    if (!response.ok) throw new Error('Failed to fetch costs');
    const data = await response.json();
    setTaskCosts(data);
  } catch (err) {
    console.error('Error fetching task costs:', err);
  }
};
```

---

**Find the task details display JSX:**

```jsx
<DialogContent>
  {task ? (
    <Box>
      <Typography variant="h6">{task.title}</Typography>
      <Typography variant="body2">{task.description}</Typography>
      {/* ... more task details ... */}
    </Box>
  ) : (
    <Typography>Loading...</Typography>
  )}
</DialogContent>
```

**Add cost breakdown after task details:**

```jsx
<DialogContent>
  {task ? (
    <Box>
      <Typography variant="h6">{task.title}</Typography>
      <Typography variant="body2">{task.description}</Typography>

      {/* ← ADD THIS SECTION */}
      {taskCosts && (
        <Card sx={{ mt: 3 }}>
          <CardHeader title="Cost Breakdown" />
          <CardContent>
            <Grid container spacing={1}>
              {[
                'research',
                'outline',
                'draft',
                'assess',
                'refine',
                'finalize',
              ].map((phase) => (
                <Grid item xs={6} key={phase}>
                  <Box
                    sx={{ display: 'flex', justifyContent: 'space-between' }}
                  >
                    <Typography
                      variant="body2"
                      sx={{ textTransform: 'capitalize' }}
                    >
                      {phase}
                    </Typography>
                    <Chip
                      size="small"
                      label={`$${(taskCosts[phase] || 0).toFixed(4)}`}
                      color={
                        (taskCosts[phase] || 0) === 0 ? 'success' : 'default'
                      }
                    />
                  </Box>
                </Grid>
              ))}
            </Grid>
            <Divider sx={{ my: 2 }} />
            <Box
              sx={{
                display: 'flex',
                justifyContent: 'space-between',
                fontWeight: 'bold',
              }}
            >
              <Typography>Total Cost</Typography>
              <Typography color="primary">
                ${(taskCosts.total || 0).toFixed(4)}
              </Typography>
            </Box>
          </CardContent>
        </Card>
      )}
      {/* ← END ADD */}

      {/* ... rest of task details ... */}
    </Box>
  ) : (
    <Typography>Loading...</Typography>
  )}
</DialogContent>
```

---

## Change 3: Update task_routes.py (Backend)

**File:** `src/cofounder_agent/routes/task_routes.py`

**Find the TaskCreate schema:**

```python
class TaskCreate(BaseModel):
    title: str
    description: str
    topic: str
    # ... other fields
```

**Add model selection fields:**

```python
class TaskCreate(BaseModel):
    title: str
    description: str
    topic: str
    # ← ADD THESE THREE FIELDS
    modelSelections: Optional[Dict[str, str]] = None
    qualityPreference: Optional[str] = "balanced"
    estimatedCost: Optional[float] = 0.0
    # ← END ADD
    # ... other fields
```

---

**Find the create_task endpoint:**

```python
@router.post("/api/tasks")
async def create_task(task: TaskCreate, user: User = Depends(get_current_user)):
    """Create a new task"""
    try:
        task_data = {
            "title": task.title,
            "description": task.description,
            "topic": task.topic,
            # ... other fields
        }

        task_id = await db.create_task(task_data, user.id)
        return {"id": task_id, "status": "created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Update to include model selections:**

```python
@router.post("/api/tasks")
async def create_task(task: TaskCreate, user: User = Depends(get_current_user)):
    """Create a new task with optional model selections"""
    try:
        task_data = {
            "title": task.title,
            "description": task.description,
            "topic": task.topic,
            # ← ADD THESE THREE FIELDS
            "modelSelections": task.modelSelections or {},
            "qualityPreference": task.qualityPreference,
            "estimatedCost": task.estimatedCost,
            # ← END ADD
            # ... other fields
        }

        task_id = await db.create_task(task_data, user.id)
        return {"id": task_id, "status": "created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

---

**Add a new endpoint to get task costs:**

```python
# ← ADD THIS NEW ENDPOINT
@router.get("/api/tasks/{task_id}/costs")
async def get_task_costs(task_id: str, user: User = Depends(get_current_user)):
    """Get cost breakdown for a specific task"""
    try:
        db = DatabaseService()
        costs = await db.get_task_costs(task_id)

        if not costs:
            return {
                "research": 0.0,
                "outline": 0.0,
                "draft": 0.0,
                "assess": 0.0,
                "refine": 0.0,
                "finalize": 0.0,
                "total": 0.0,
            }

        return costs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# ← END ADD
```

---

## Change 4: Ensure Imports Are Correct

**Frontend (TaskCreationModal.jsx):**

```jsx
// Make sure these are imported
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  Box,
  Alert,
  Card,
  CardHeader,
  CardContent,
  Chip,
  Grid,
  Divider,
  Typography,
} from '@mui/material';
import ModelSelectionPanel from './ModelSelectionPanel';
```

**Frontend (TaskDetailModal.jsx):**

```jsx
// Make sure these are imported
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Card,
  CardHeader,
  CardContent,
  Chip,
  Grid,
  Divider,
  Typography,
} from '@mui/material';
```

**Backend (task_routes.py):**

```python
# Make sure these are imported
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict
from services.database_service import DatabaseService
from models import User  # or however you import User model
```

---

## Testing These Changes

### Frontend Test

```bash
cd web/oversight-hub
npm start
# Open http://localhost:3000
# Click "Create Task"
# Should see ModelSelectionPanel below other fields
# Click "Fast" preset
# Cost should update to ~$0.006
# Fill in task details and submit
```

### Backend Test

```bash
# Test create task with model selection
curl -X POST http://localhost:8001/api/tasks \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Post",
    "description": "Test with models",
    "modelSelections": {
      "research": "ollama",
      "outline": "ollama",
      "draft": "gpt-3.5-turbo",
      "assess": "gpt-4",
      "refine": "gpt-4",
      "finalize": "gpt-4"
    },
    "qualityPreference": "fast",
    "estimatedCost": 0.006
  }'

# Should return: {"id": "...", "status": "created"}

# Test get costs (replace TASK_ID with response from above)
curl -X GET http://localhost:8001/api/tasks/TASK_ID/costs \
  -H "Authorization: Bearer YOUR_JWT"

# Should return: {"research": 0.0, "outline": 0.0, ..., "total": 0.006}
```

---

## Common Issues & Fixes

**Issue:** `ImportError: cannot import name 'ModelSelectionPanel'`  
**Fix:** Make sure `ModelSelectionPanel.jsx` is in `components/` folder

**Issue:** ModelSelectionPanel not showing in modal  
**Fix:** Check that Box import is added and component is wrapped properly

**Issue:** Cost not saving with task  
**Fix:** Check TaskCreate schema includes the three new fields

**Issue:** GET /api/tasks/{id}/costs returns 404  
**Fix:** Make sure endpoint is added to task_routes.py and decorators are correct

**Issue:** `TypeError: Cannot read property 'modelSelections' of undefined`  
**Fix:** Check onSelectionChange is passed to ModelSelectionPanel and state is initialized

---

## Summary

**Changed Files:**

1. `web/oversight-hub/src/components/TaskCreationModal.jsx` - Added ModelSelectionPanel integration
2. `web/oversight-hub/src/components/TaskDetailModal.jsx` - Added cost display
3. `src/cofounder_agent/routes/task_routes.py` - Added model selection fields and costs endpoint

**New Files:**

1. `web/oversight-hub/src/components/ModelSelectionPanel.jsx` - Already created ✅

**Time to Implement:** 30-45 minutes  
**Time to Test:** 60-90 minutes  
**Total:** 2 hours

---

You're ready. Copy these changes and you're done with integration! 🚀
