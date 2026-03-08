# API Endpoint Index

This index provides a quick map of high-value API surfaces used across workflows, capabilities, and orchestration.

## Workflows

- `GET /api/workflows/templates`
- `POST /api/workflows/execute/{template_name}`
- `GET /api/workflows/status/{execution_id}`
- `GET /api/workflows/executions`
- `POST /api/workflows/pause/{workflow_id}`
- `POST /api/workflows/resume/{workflow_id}`
- `POST /api/workflows/cancel/{workflow_id}`

## Custom Workflows

- `POST /api/workflows/custom`
- `GET /api/workflows/custom`
- `GET /api/workflows/custom/{workflow_id}`
- `PUT /api/workflows/custom/{workflow_id}`
- `DELETE /api/workflows/custom/{workflow_id}`
- `POST /api/workflows/custom/{workflow_id}/execute`

## Workflow Progress

- `POST /api/workflow-progress/initialize/{execution_id}`
- `POST /api/workflow-progress/start/{execution_id}`
- `POST /api/workflow-progress/phase/start/{execution_id}`
- `POST /api/workflow-progress/phase/complete/{execution_id}`
- `POST /api/workflow-progress/phase/fail/{execution_id}`
- `POST /api/workflow-progress/complete/{execution_id}`
- `POST /api/workflow-progress/fail/{execution_id}`
- `GET /api/workflow-progress/{execution_id}`

## Capabilities and Service Registry

- `POST /api/capability-tasks`
- `POST /api/agents/introspect`
- `GET /api/services/registry`
- `GET /api/services/list`
- `GET /api/services/{service_name}`
- `GET /api/services/{service_name}/actions`
- `GET /api/services/{service_name}/actions/{action_name}`

## Auth/OAuth

- `POST /api/auth/github/callback`
- `POST /api/auth/github-callback` (fallback/deprecated)
- `POST /api/auth/logout`

## Related Files

- `src/cofounder_agent/routes/workflow_routes.py`
- `src/cofounder_agent/routes/custom_workflows_routes.py`
- `src/cofounder_agent/routes/workflow_progress_routes.py`
- `src/cofounder_agent/routes/service_registry_routes.py`
- `src/cofounder_agent/routes/capability_tasks_routes.py`
- `src/cofounder_agent/routes/auth_unified.py`
