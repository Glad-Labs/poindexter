# Oversight Hub Consolidation - Implementation Plan

## Quick Reference: Page Mapping

### Keep (Updated)

```
✅ Dashboard        → ExecutiveDashboard.jsx (NEW - replaces current)
✅ Task Management  → TaskManagement.jsx (UPDATED - add Poindexter tab)
✅ Models           → EnhancedOllamaModelsPage.jsx (unchanged)
✅ Training         → (NEW page needed)
✅ Content          → EnhancedContentPipelinePage.jsx (unchanged)
✅ Social           → EnhancedSocialPublishingPage.jsx (unchanged)
✅ Analytics        → EnhancedMetricsPage.jsx (unchanged)
✅ Settings         → SettingsManager.jsx (unchanged)
✅ Integrations     → (NEW page needed)
```

### Delete/Consolidate

```
❌ Agents              → Functionality → Poindexter chat + Command Queue
❌ Approvals          → Functionality → Task Management page (ResultPreviewPanel)
❌ Orchestrator       → Functionality → ExecutionHub.jsx (NEW)
❌ Command Queue      → Merge → ExecutionHub.jsx (NEW)
❌ Workflow History   → Merge → ExecutionHub.jsx (NEW)
❌ Chat Page          → Already removed (use Poindexter panel)
❌ AgentsPage         → Remove (agents shown in ExecutionHub)
❌ ApprovalQueue      → Remove (merged into TaskManagement)
❌ WorkflowHistoryPage → Merge into ExecutionHub
```

---

## Implementation Steps

### Step 1: Create ExecutionHub.jsx (NEW)

**Location:** `web/oversight-hub/src/components/pages/ExecutionHub.jsx`

**Features:**

- Tab 1: Active Execution (real-time agent status)
- Tab 2: Command Queue (Poindexter pending commands)
- Tab 3: Workflow History (past executions)

```jsx
// ExecutionHub.jsx - 3-tab component
// - Pulls from /api/execution/active
// - Pulls from /api/orchestrator/queue
// - Pulls from /api/execution/history
```

---

### Step 2: Create ExecutiveDashboard.jsx (NEW)

**Location:** `web/oversight-hub/src/components/pages/ExecutiveDashboard.jsx`

**Features:**

- KPI Cards (revenue, content published, tasks, AI savings)
- Trend Charts (publishing frequency, quality, costs)
- Quick Action Cards

```jsx
// ExecutiveDashboard.jsx - Business overview
// - Pulls from /api/analytics/kpis
// - Pulls from /api/analytics/trends
// - Pulls from /api/tasks?status=active
```

---

### Step 3: Update TaskManagement.jsx

**Location:** `web/oversight-hub/src/components/tasks/TaskManagement.jsx`

**Changes:**

- Add 2-tab design: "Manual Pipeline" | "Poindexter Pipeline"
- Add `pipeline` field to task data model
- Add approval timeline visualization
- Integrate ResultPreviewPanel (already there)

```jsx
// TaskManagement.jsx updates
const [pipeline, setPipeline] = useState('all'); // all | manual | poindexter

// New task fields
task.pipeline = 'manual' | 'poindexter'
task.created_by = 'user' | 'poindexter'
task.approval = { ... timeline ... }
```

---

### Step 4: Create TrainingPage.jsx (NEW)

**Location:** `web/oversight-hub/src/components/pages/TrainingPage.jsx`

**Features:**

- Fine-tuning dataset management
- Training progress monitoring
- Custom model creation
- Model versioning

---

### Step 5: Create IntegrationsPage.jsx (NEW)

**Location:** `web/oversight-hub/src/components/pages/IntegrationsPage.jsx`

**Features:**

- Connected service status
- API credential management
- Sync history
- Data flow visualization

**Integrations to Support:**

```
- Strapi CMS
- Twitter/X API
- Facebook API
- Instagram API
- LinkedIn API
- Email Services (SendGrid, Mailchimp)
- Google Drive
- Analytics Platforms
```

---

### Step 6: Update AppRoutes.jsx

**Location:** `web/oversight-hub/src/routes/AppRoutes.jsx`

**Changes:**

```javascript
// OLD ROUTES TO REMOVE
- /agents → AgentsPage (DELETE)
- /approvals → ApprovalQueue (DELETE)
- /orchestrator → OrchestratorPage (DELETE)
- /queue → CommandQueuePage (DELETE)
- /workflow → WorkflowHistoryPage (DELETE)

// NEW ROUTES TO ADD
+ /execution → ExecutionHub (NEW)
+ /training → TrainingPage (NEW)
+ /integrations → IntegrationsPage (NEW)

// UPDATED ROUTES
~ / → ExecutiveDashboard (updated from Dashboard)
~ /tasks → TaskManagement (add Poindexter tab)
~ /models → EnhancedOllamaModelsPage (unchanged)
```

---

### Step 7: Update LayoutWrapper.jsx Navigation

**Location:** `web/oversight-hub/src/components/LayoutWrapper.jsx`

**Changes:**

```javascript
const navigationItems = [
  { label: 'Dashboard', icon: '📊', path: 'dashboard' }, // RENAMED
  { label: 'Tasks', icon: '✅', path: 'tasks' }, // UNCHANGED (dual pipeline)
  { label: 'Execution Hub', icon: '⚙️', path: 'execution' }, // NEW (replaces Orchestrator/Queue/Workflow)
  { label: 'Content', icon: '📝', path: 'content' }, // UNCHANGED
  { label: 'Social', icon: '📱', path: 'social' }, // UNCHANGED
  { label: 'AI & Training', icon: '🧠', path: 'models' }, // UNCHANGED (or add submenu)
  { label: 'Analytics', icon: '📈', path: 'analytics' }, // UNCHANGED
  { label: 'Integrations', icon: '🔗', path: 'integrations' }, // NEW
  { label: 'Settings', icon: '⚙️', path: 'settings' }, // UNCHANGED
];

// REMOVED ITEMS
// ❌ Agents
// ❌ Approvals
// ❌ Orchestrator
// ❌ Command Queue
// ❌ Workflow
// ❌ Training (consolidate with Models or keep separate)
// ❌ Costs (merge into Analytics or Settings)
```

**New Navigation Structure:**

```
📊 Dashboard (Executive Overview)
✅ Tasks (Manual + Poindexter pipelines)
⚙️ Execution Hub (Active + Queue + History)
📝 Content (Generation + Inventory)
📱 Social (Publishing + Scheduling)
🧠 AI & Training (Models + Fine-tuning)
📈 Analytics (Metrics + Reports)
🔗 Integrations (Service Connections)
⚙️ Settings (Configuration)
```

---

## File Deletion Checklist

Files to **DELETE** (no longer needed):

```bash
# Pages
rm src/components/pages/AgentsPage.jsx
rm src/components/pages/AgentsPage.css
rm src/components/pages/ApprovalQueue.jsx
rm src/components/pages/ApprovalQueue.css
rm src/components/pages/OrchestratorPage.jsx (if exists)
rm src/components/pages/CommandQueuePage.jsx (if exists)
rm src/components/pages/WorkflowHistoryPage.jsx
rm src/components/pages/WorkflowHistoryPage.css
rm src/components/pages/ChatPage.jsx
rm src/components/pages/ChatPage.css

# Related components
rm src/components/OrchestratorCommandMessage.jsx (if not used elsewhere)
rm src/components/OrchestratorErrorMessage.jsx (if not used elsewhere)
rm src/components/OrchestratorMessageCard.jsx (if not used elsewhere)
rm src/components/OrchestratorResultMessage.jsx (if not used elsewhere)
rm src/components/OrchestratorStatusMessage.jsx (if not used elsewhere)

# Verify these aren't imported before deleting:
grep -r "OrchestratorCommandMessage" src/ # Should return 0 results
grep -r "ApprovalQueue" src/ # Should return only ResultPreviewPanel usage
```

---

## New Page Templates

### ExecutionHub.jsx Template

```jsx
import React, { useState, useEffect } from 'react';
import {
  Paper,
  Tabs,
  Tab,
  Box,
  CircularProgress,
  Typography,
  Grid,
  Card,
  CardContent,
  List,
  ListItem,
} from '@mui/material';

const ExecutionHub = () => {
  const [activeTab, setActiveTab] = useState(0);
  const [loading, setLoading] = useState(false);

  return (
    <Paper>
      <Tabs value={activeTab} onChange={(e, v) => setActiveTab(v)}>
        <Tab label="⚡ Active Execution" />
        <Tab label="💬 Command Queue" />
        <Tab label="📜 History" />
      </Tabs>

      <Box sx={{ p: 3 }}>
        {activeTab === 0 && <ActiveExecutionTab />}
        {activeTab === 1 && <CommandQueueTab />}
        {activeTab === 2 && <HistoryTab />}
      </Box>
    </Paper>
  );
};

// Fetch from:
// GET /api/execution/active
// GET /api/execution/queue
// GET /api/execution/history
// GET /api/agents/status
```

### ExecutiveDashboard.jsx Template

```jsx
import React, { useState, useEffect } from 'react';
import { Grid, Card, CardContent, Typography } from '@mui/material';
import { LineChart, BarChart } from '@mui/x-charts';

const ExecutiveDashboard = () => {
  const [kpis, setKpis] = useState({});
  const [trends, setTrends] = useState({});

  useEffect(() => {
    // Fetch KPI data
    fetch('/api/analytics/kpis')
      .then((r) => r.json())
      .then(setKpis);

    // Fetch trends
    fetch('/api/analytics/trends')
      .then((r) => r.json())
      .then(setTrends);
  }, []);

  return (
    <Grid container spacing={3}>
      {/* KPI Cards */}
      <Grid item xs={12} sm={6} md={3}>
        <KPICard title="Revenue" value={kpis.revenue} change="+12%" />
      </Grid>
      <Grid item xs={12} sm={6} md={3}>
        <KPICard
          title="Content Published"
          value={kpis.published}
          change="+8%"
        />
      </Grid>
      <Grid item xs={12} sm={6} md={3}>
        <KPICard title="Tasks Completed" value={kpis.completed} change="+25%" />
      </Grid>
      <Grid item xs={12} sm={6} md={3}>
        <KPICard title="AI Savings" value={`$${kpis.savings}`} change="+40%" />
      </Grid>

      {/* Charts */}
      <Grid item xs={12} md={6}>
        <TrendChart data={trends.publishing} title="Publishing Frequency" />
      </Grid>
      <Grid item xs={12} md={6}>
        <TrendChart data={trends.quality} title="Content Quality" />
      </Grid>

      {/* Quick Actions */}
      <Grid item xs={12}>
        <QuickActionCards />
      </Grid>
    </Grid>
  );
};
```

---

## API Endpoints Needed in FastAPI

### New endpoints to implement:

```python
# /src/cofounder_agent/routes/execution.py
@router.get("/api/execution/active")
async def get_active_execution():
    """Get currently running tasks and agents"""
    return {
        "agents": [
            {
                "id": "content-agent",
                "name": "Content Agent",
                "status": "running",
                "current_task": "blog-post-123",
                "progress": 85,
                "tokens_used": 2500,
                "estimated_completion": "2m 15s"
            }
        ],
        "active_tasks": [...],
        "last_updated": timestamp
    }

@router.get("/api/execution/queue")
async def get_command_queue():
    """Get Poindexter pending commands"""
    return {
        "pending_commands": [
            {
                "id": "cmd-456",
                "user_request": "Create 5 blog posts about AI trends",
                "created_at": timestamp,
                "breakdown": [
                    "Generate blog post: AI in 2025",
                    "Generate blog post: Prompt engineering tips",
                    ...
                ],
                "status": "processing",
                "current_step": 2,
                "total_steps": 5
            }
        ]
    }

@router.get("/api/execution/history")
async def get_execution_history(days: int = 30):
    """Get historical execution logs"""
    return {
        "executions": [
            {
                "id": "exec-789",
                "task": "Generate blog post",
                "started_at": timestamp,
                "completed_at": timestamp,
                "duration": "5m 30s",
                "status": "success",
                "result": {...},
                "agent": "content-agent"
            }
        ],
        "summary": {
            "total_tasks": 156,
            "success_rate": 94.2,
            "avg_duration": "3m 45s",
            "total_cost": "$45.67"
        }
    }

@router.get("/api/agents/status")
async def get_agents_status():
    """Get all agent status"""
    return {
        "agents": [
            {
                "id": "content-agent",
                "name": "Content Agent",
                "status": "idle" | "running" | "error",
                "last_activity": timestamp,
                "tasks_completed": 156,
                "success_rate": 94.2,
                "avg_response_time": "3m 45s"
            }
        ]
    }
```

---

## Testing Checklist

After implementation:

- [ ] Navigation menu shows correct items (9 items, no duplicates)
- [ ] Old routes (agents, approvals, orchestrator, queue, workflow) return 404
- [ ] Dashboard loads KPI data correctly
- [ ] Task Management shows both pipelines in tabs
- [ ] ExecutionHub displays active agents and commands
- [ ] Poindexter chat still works in bottom panel
- [ ] ResultPreviewPanel approval flow works
- [ ] All 89 tasks load correctly in Task Management
- [ ] No console errors on page load
- [ ] API calls return expected data

---

## File Structure After Consolidation

```
src/
├── components/
│   ├── pages/
│   │   ├── ExecutiveDashboard.jsx        (NEW)
│   │   ├── ExecutionHub.jsx              (NEW)
│   │   ├── TrainingPage.jsx              (NEW)
│   │   ├── IntegrationsPage.jsx          (NEW)
│   │   ├── EnhancedOllamaModelsPage.jsx  (KEEP)
│   │   ├── EnhancedContentPipelinePage.jsx (KEEP)
│   │   ├── EnhancedSocialPublishingPage.jsx (KEEP)
│   │   ├── EnhancedMetricsPage.jsx       (KEEP)
│   │   ├── SettingsManager.jsx           (KEEP)
│   │   ├── (DELETE AgentsPage.jsx)
│   │   ├── (DELETE ApprovalQueue.jsx)
│   │   ├── (DELETE WorkflowHistoryPage.jsx)
│   │   ├── (DELETE ChatPage.jsx)
│   │   └── ... others
│   ├── LayoutWrapper.jsx                  (UPDATE navigation)
│   ├── TaskDetailModal.jsx                (KEEP)
│   └── tasks/
│       ├── TaskManagement.jsx             (UPDATE - add tabs)
│       ├── ResultPreviewPanel.jsx         (KEEP)
│       └── ...
├── routes/
│   └── AppRoutes.jsx                      (UPDATE routing)
└── ...
```

---

## Rollback Plan

If issues occur:

```bash
# Revert changes
git checkout src/components/LayoutWrapper.jsx
git checkout src/routes/AppRoutes.jsx

# Restore deleted files
git checkout src/components/pages/AgentsPage.jsx
git checkout src/components/pages/ApprovalQueue.jsx
# ... etc
```

---

## Success Criteria

✅ **Phase 1 Complete when:**

1. ExecutionHub.jsx renders without errors
2. ExecutiveDashboard.jsx displays KPI cards
3. TaskManagement has working pipeline tabs
4. Navigation menu shows 9 items
5. Old pages return 404
6. No console errors

✅ **Phase 2 Complete when:**

1. TrainingPage.jsx implemented
2. IntegrationsPage.jsx implemented
3. API endpoints tested
4. All workflows tested end-to-end

✅ **Phase 3 Complete when:**

1. Advanced features working
2. Both pipelines (manual + Poindexter) tested
3. Performance benchmarks met
4. Documentation complete
