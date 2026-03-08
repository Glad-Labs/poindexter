# WebSocket Real-Time

Real-time updates are exposed over WebSocket channels for workflow progress, image generation progress, and global event streams.

## Primary Channels

- `ws://localhost:8000/api/ws/workflow/{execution_id}`
- `ws://localhost:8000/api/ws/image-generation/{task_id}`
- `ws://localhost:8000/api/ws/` (global namespace)
- `ws://localhost:8000/api/workflow-progress/ws/{execution_id}`

## Related REST Progress Endpoints

- `POST /api/workflow-progress/initialize/{execution_id}`
- `POST /api/workflow-progress/start/{execution_id}`
- `POST /api/workflow-progress/phase/start/{execution_id}`
- `POST /api/workflow-progress/phase/complete/{execution_id}`
- `POST /api/workflow-progress/complete/{execution_id}`
- `POST /api/workflow-progress/fail/{execution_id}`

## Key Implementation Files

- `src/cofounder_agent/routes/websocket_routes.py`
- `src/cofounder_agent/routes/workflow_progress_routes.py`
- `src/cofounder_agent/services/websocket_manager.py`
- `src/cofounder_agent/services/workflow_progress_service.py`

## Notes

- The `ConnectionManager` in websocket routes handles per-task/per-execution connection fanout.
- The workflow-progress route also supports a dedicated WebSocket endpoint and broadcast helper for execution-specific updates.
