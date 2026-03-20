# Using Capability-Based Tasks

**Goal:** Create tasks from natural language intent and let the system choose the best capabilities automatically.

**Time:** ~15 minutes  
**Difficulty:** Beginner to Intermediate

---

## Overview

In this tutorial, you'll:

1. Inspect available capabilities
2. Submit an intent-based task request
3. Review selected capabilities and execution plan
4. Check task results

By the end, you can route work by intent instead of manually assembling every phase.

---

## Prerequisites

- Backend running on `http://localhost:8000`
- `ENABLE_CAPABILITY_SYSTEM=true` in environment
- Auth token (`dev-token` for local development)

References:

- [Capability-Based Tasks Feature](../03-Features/Capability-Based-Tasks.md)
- [Capability Catalog](../07-Appendices/Capability-Catalog.md)

---

## Step 1: Discover Capabilities

```bash
curl -X POST http://localhost:8000/api/agents/introspect \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json"
```

Review the response for available capability names (for example `research`, `content_writing`, `quality_evaluation`, `publishing`).

---

## Step 2: Create a Capability Task from Intent

```bash
curl -X POST http://localhost:8000/api/capability-tasks \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "intent": "Create a practical blog post about reducing cloud costs for startups, then critique and polish it",
    "constraints": {
      "tone": "advisory",
      "target_audience": "startup founders"
    },
    "model": "ollama/mistral"
  }'
```

Expected: task/workflow response indicating selected capabilities and execution metadata.

---

## Step 3: Check How the System Routed Your Task

Inspect the response for:

- Intent interpretation summary
- Selected capabilities and order
- Agent/service assignment
- Execution ID or task ID

This confirms semantic routing decisions were applied.

---

## Step 4: Retrieve Status and Output

```bash
curl -X GET http://localhost:8000/api/tasks/<task_id> \
  -H "Authorization: Bearer dev-token"
```

For completed tasks, verify output includes:

- Generated content
- Quality/evaluation notes
- Final publication-ready result when applicable

---

## Troubleshooting

### No capabilities selected

- Check capability system feature flags
- Run `/api/agents/introspect` to confirm services are registered

### `404` or `405` on capability endpoint

- Confirm route is `/api/capability-tasks`
- Verify backend branch includes capability routes

### Poor routing choices

- Refine intent with clearer goals and audience
- Add constraints to improve capability matching

See: [Troubleshooting Hub](../06-Troubleshooting/README.md)

---

## Next Steps

- Compare intent-based tasks with hand-authored custom workflows.
- Explore [Service Registry](../03-Features/Service-Registry.md) to understand available services.
- Use [Workflows System](../03-Features/Workflows-System.md) for advanced orchestration control.
