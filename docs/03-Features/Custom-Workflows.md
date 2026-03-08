# Custom Workflows

Custom workflows allow users to create, list, update, delete, and execute workflow definitions with user-scoped access control.

## Primary Endpoints

- `POST /api/workflows/custom`
- `GET /api/workflows/custom`
- `GET /api/workflows/custom/{workflow_id}`
- `PUT /api/workflows/custom/{workflow_id}`
- `DELETE /api/workflows/custom/{workflow_id}`
- `POST /api/workflows/custom/{workflow_id}/execute`

## Capability Discovery for Builders

- `GET /api/workflows/available-phases`

## Key Implementation Files

- `src/cofounder_agent/routes/custom_workflows_routes.py`
- `src/cofounder_agent/services/custom_workflows_service.py`
- `src/cofounder_agent/schemas/custom_workflow_schemas.py`

## Notes

- User identity is resolved from request context or JWT bearer token, with development token support for local workflows.
- Template execution internally uses the custom workflow execution path for consistent persistence and lifecycle behavior.
