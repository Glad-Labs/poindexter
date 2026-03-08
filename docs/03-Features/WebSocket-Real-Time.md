# WebSocket Real-Time

Real-time updates are exposed over WebSocket channels for workflow progress, image generation progress, and global event streams.

## Primary Channels

- `ws://localhost:8000/api/ws/workflow/{execution_id}` - Workflow phase updates
- `ws://localhost:8000/api/ws/image-generation/{task_id}` - Image generation progress
- `ws://localhost:8000/api/ws/` - Global event namespace
- `ws://localhost:8000/api/workflow-progress/ws/{execution_id}` - Workflow progress stream

## WebSocket Message Examples

### Workflow Progress Message

**Client connects:**

```javascript
const ws = new WebSocket(
  'ws://localhost:8000/api/ws/workflow/550e8400-e29b-41d4-a716-446655440000'
);

ws.onopen = () => {
  console.log('Connected');
  ws.send(JSON.stringify({ type: 'ping' }));
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  console.log('Progress update:', message);
};
```

**Server sends progress:**

```json
{
  "type": "progress",
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
  "status": "running",
  "current_phase": "draft",
  "phase_status": "completed",
  "progress_percent": 35,
  "phase_duration_ms": 8234,
  "elapsed_total_ms": 20569,
  "message": "Completed research phase, starting draft...",
  "timestamp": "2026-03-08T14:32:15Z"
}
```

### Image Generation Progress Message

**Server sends periodic updates:**

```json
{
  "type": "progress",
  "task_id": "task-550e8400-e29b-41d4",
  "status": "generating",
  "current_step": 32,
  "total_steps": 50,
  "percentage": 64.0,
  "current_stage": "base_model",
  "elapsed_time": 46.5,
  "estimated_remaining": 26.3,
  "message": "Generating base image (step 32/50)"
}
```

### Client Commands

**Ping/Pong (keep-alive):**

```json
{"type": "ping"}
{"type": "pong"}
```

**Request current progress:**

```json
{ "type": "get_progress" }
```

## Related REST Progress Endpoints

- `POST /api/workflow-progress/initialize/{execution_id}` - Initialize progress tracking
- `POST /api/workflow-progress/start/{execution_id}` - Start workflow
- `POST /api/workflow-progress/phase/start/{execution_id}` - Mark phase start
- `POST /api/workflow-progress/phase/complete/{execution_id}` - Mark phase complete
- `POST /api/workflow-progress/complete/{execution_id}` - Mark workflow complete
- `POST /api/workflow-progress/fail/{execution_id}` - Mark workflow failed

**Example phase completion:**

```bash
curl -X POST "http://localhost:8000/api/workflow-progress/phase/complete/550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{
    "phase_name": "draft",
    "phase_output": {"content": "...", "word_count": 1250},
    "duration_ms": 8234
  }'
```

## Key Implementation Files

- [src/cofounder_agent/routes/websocket_routes.py](../../src/cofounder_agent/routes/websocket_routes.py)
- [src/cofounder_agent/routes/workflow_progress_routes.py](../../src/cofounder_agent/routes/workflow_progress_routes.py)
- [src/cofounder_agent/services/websocket_manager.py](../../src/cofounder_agent/services/websocket_manager.py)
- [src/cofounder_agent/services/workflow_progress_service.py](../../src/cofounder_agent/services/workflow_progress_service.py)

## Notes

- The `ConnectionManager` in websocket routes handles per-task/per-execution connection fanout to multiple clients
- Clients should implement keep-alive (ping/pong) to detect connection loss
- Server sends keep-alive every 30 seconds if no updates occur
- All progress updates include execution_id for client-side reconciliation
