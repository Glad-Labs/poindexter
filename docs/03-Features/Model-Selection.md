# Model Selection

Workflow executions can capture a caller-selected model and persist that choice for traceability and analytics.

## How It Works

1. Client includes a `model` key in workflow template input payload.
2. Template execution extracts the selected model from `task_input`.
3. Custom workflow execution receives `selected_model` and injects it into execution inputs.
4. Execution persistence stores `selected_model` in workflow execution records.

## Request Example

```json
{
  "topic": "AI governance",
  "tone": "professional",
  "model": "gpt-4-turbo"
}
```

## Key Implementation Files

- `src/cofounder_agent/services/template_execution_service.py`
- `src/cofounder_agent/services/custom_workflows_service.py`
- `src/cofounder_agent/services/workflow_execution_adapter.py`

## Notes

- Model selection is optional; default routing still applies when no explicit model is provided.
- Selected model metadata is intended for observability, auditability, and future cost/performance analysis.
