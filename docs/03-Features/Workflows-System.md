# Workflows System

The workflow system provides template-based execution and lifecycle control for multi-phase tasks.

## What It Covers

- Template execution for common content flows (blog, social, email, newsletter, market analysis)
- Execution tracking and status retrieval
- Workflow control (pause, resume, cancel)
- Integration with progress broadcasting and persistence

## Primary Endpoints

- `POST /api/workflows/execute/{template_name}`
- `GET /api/workflows/templates`
- `GET /api/workflows/status/{execution_id}`
- `GET /api/workflows/executions`
- `POST /api/workflows/pause/{workflow_id}`
- `POST /api/workflows/resume/{workflow_id}`
- `POST /api/workflows/cancel/{workflow_id}`

## Key Implementation Files

- `src/cofounder_agent/routes/workflow_routes.py`
- `src/cofounder_agent/services/template_execution_service.py`
- `src/cofounder_agent/services/workflow_executor.py`
- `src/cofounder_agent/services/workflow_engine.py`

## Notes

- Template execution is asynchronous and returns an execution identifier for follow-up status/progress checks.
- Template workflows are persisted before execution so workflow execution records satisfy foreign key constraints.
