# Service Registry

The service registry exposes discoverable service/action schemas for agent and UI capability introspection.

## Primary Endpoints

- `GET /api/services/registry`
- `GET /api/services/list`
- `GET /api/services/{service_name}`
- `GET /api/services/{service_name}/actions`
- `GET /api/services/{service_name}/actions/{action_name}`

## What It Enables

- LLM/agent discovery of callable services and action schemas
- Dynamic frontend generation of service-action forms
- Registry-backed orchestration and integration tooling

## Key Implementation Files

- `src/cofounder_agent/routes/service_registry_routes.py`
- `src/cofounder_agent/services/service_base.py`

## Notes

- The registry schema includes per-action parameter and response schema metadata.
- Service routes return structured 404s listing available services when lookup fails.
