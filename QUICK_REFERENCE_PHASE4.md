# Phase 4 Backend WebSocket - Quick Reference Card

## 🚀 Quick Start

### 1. Verify Backend is Running

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok", "service": "cofounder-agent"}
```

### 2. Check WebSocket Status

```bash
curl http://localhost:8000/api/ws/stats
# Expected: {"total_connections": 0, "namespaces": {}}
```

### 3. Test from Browser (Console)

```javascript
const ws = new WebSocket('ws://localhost:8000/api/ws');
ws.onopen = () => console.log('✓ Connected');
ws.onmessage = (e) => console.log('Message:', JSON.parse(e.data));
setTimeout(() => ws.close(), 60000);
```

---

## 📦 Emit Events (Backend)

### Basic Syntax

```python
from services.websocket_event_broadcaster import emit_task_progress

await emit_task_progress(
    task_id='task-123',
    status='RUNNING',
    progress=50,
    current_step='Processing',
    total_steps=10,
    completed_steps=5,
    message='50% complete'
)
```

### All Event Types

```python
await emit_task_progress(task_id, status, progress, current_step, total_steps, completed_steps, message, elapsed_time, estimated_time_remaining, error)
await emit_workflow_status(workflow_id, status, duration, task_count, task_results)
await emit_analytics_update(total_tasks, completed_today, average_completion_time, cost_today, success_rate, failed_today, running_now)
await emit_notification(type, title, message, duration)
```

---

## 🎯 Frontend Integration

### 1. Use WebSocket Hook

```javascript
import { useTaskProgress } from '../context/WebSocketContext';

function MyComponent() {
    const { isConnected } = useTaskProgress('task-123', (data) => {
        console.log('Progress:', data.progress);
    });
    
    return <div>{isConnected ? 'Connected' : 'Disconnected'}</div>;
}
```

### 2. Available Hooks

- `useWebSocket()` - Connection status
- `useTaskProgress(taskId, callback)` - Task updates
- `useWorkflowStatus(workflowId, callback)` - Workflow updates
- `useAnalyticsUpdates(callback)` - Analytics updates
- `useWebSocketEvent(eventName, callback)` - Generic events

---

## 📝 Message Format

### Task Progress

```json
{
    "type": "progress",
    "event": "task.progress.task-123",
    "data": {
        "taskId": "task-123",
        "status": "RUNNING",
        "progress": 50,
        "currentStep": "Processing",
        "totalSteps": 10,
        "completedSteps": 5,
        "message": "50% complete"
    }
}
```

### Workflow Status

```json
{
    "type": "workflow_status",
    "event": "workflow.status.wf-456",
    "data": {
        "workflowId": "wf-456",
        "status": "COMPLETED",
        "duration": 300,
        "taskCount": 3,
        "taskResults": {}
    }
}
```

### Analytics Update

```json
{
    "type": "analytics",
    "event": "analytics.update",
    "data": {
        "totalTasks": 1000,
        "completedToday": 50,
        "successRate": 98.5
    }
}
```

### Notification

```json
{
    "type": "notification",
    "event": "notification.received",
    "data": {
        "type": "success",
        "title": "Complete",
        "message": "Done",
        "duration": 5000
    }
}
```

---

## 🔧 Integration Points

### Task Executor

```python
# In src/cofounder_agent/services/task_executor.py
await emit_task_progress(task_id=task.id, status='RUNNING', progress=45, ...)
```

### Workflow History

```python
# In src/cofounder_agent/services/workflow_history.py
await emit_workflow_status(workflow_id=workflow.id, status='COMPLETED', ...)
```

### Analytics Service

```python
# In src/cofounder_agent/services/analytics_routes.py
await emit_analytics_update(total_tasks=1000, completed_today=50, ...)
```

### Notification Service

```python
# Anywhere in backend
await emit_notification(type='success', title='Done', message='Completed')
```

---

## 📊 File Locations

| What | Where |
|------|-------|
| WebSocket Manager | `src/cofounder_agent/services/websocket_manager.py` |
| Event Broadcaster | `src/cofounder_agent/services/websocket_event_broadcaster.py` |
| WebSocket Routes | `src/cofounder_agent/routes/websocket_routes.py` |
| Frontend Provider | `src/context/WebSocketContext.jsx` |
| Frontend Client | `src/services/websocketService.js` |
| Notifications | `src/components/notifications/NotificationCenter.jsx` |
| Task Monitor | `src/components/dashboard/LiveTaskMonitor.jsx` |

---

## 🧪 Testing

### Test Connection Count

```bash
watch -n 1 'curl -s http://localhost:8000/api/ws/stats'
```

### Test Event Emission

```python
cd src/cofounder_agent
python3 -c "
import asyncio
from services.websocket_event_broadcaster import emit_task_progress

async def test():
    await emit_task_progress(task_id='test', status='COMPLETED', progress=100, current_step='Done', total_steps=1, completed_steps=1, message='Test')

asyncio.run(test())
"
```

### Test Frontend Subscription

```javascript
// In browser console
const ws = new WebSocket('ws://localhost:8000/api/ws');
let count = 0;
ws.onmessage = (e) => {
    count++;
    console.log(`Message ${count}:`, JSON.parse(e.data));
};
```

---

## ✅ Checklist: Integration Complete?

- [ ] Backend running: `npm run dev:cofounder`
- [ ] WebSocket accessible: `curl http://localhost:8000/api/ws/stats`
- [ ] Frontend connected: See 🟢 in NotificationCenter
- [ ] Events emitting: Backend calling `emit_*` functions
- [ ] Events receiving: Frontend console shows incoming messages
- [ ] UI updating: LiveTaskMonitor shows progress updates

---

## ⚠️ Common Commands

```bash
# Start everything
npm run dev

# Just backend
npm run dev:cofounder

# Check health
curl http://localhost:8000/health

# Check WebSocket
curl http://localhost:8000/api/ws/stats

# Frontend logs
npm run dev:oversight  # Port 3001

# Reset everything
npm run clean:install && npm run setup:all
```

---

## 🔐 Production Checklist

- [ ] Add JWT authentication to WebSocket endpoint
- [ ] Change `ws://` to `wss://` in frontend (HTTPS)
- [ ] Add rate limiting
- [ ] Enable CORS properly
- [ ] Set connection timeout (30 min)
- [ ] Add monitoring and alerting
- [ ] Load test with 1000+ connections
- [ ] Monitor memory for leaks

---

## 📞 Need Help?

- Detailed backend guide: `PHASE_4_BACKEND_IMPLEMENTATION.md`
- Deployment instructions: `PHASE_4_DEPLOYMENT_GUIDE.md`
- Full project summary: `PHASE_1_4_COMPLETION_SUMMARY.md`
- Status report: `PHASE_4_BACKEND_WEBSOCKET_STATUS.md`

---

## 🎯 Expected Outcomes

✅ WebSocket server running  
✅ Backend can emit events  
✅ Frontend receives updates  
✅ Real-time UI updates  
✅ Notifications showing  
✅ Progress tracking working  
✅ Zero compilation errors  
✅ All 3 services healthy  

**Status:** COMPLETE AND OPERATIONAL ✅
