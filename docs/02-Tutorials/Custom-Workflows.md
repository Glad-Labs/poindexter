# Building Custom Workflows

**Goal:** Create and run a custom multi-phase workflow with real-time progress tracking.

**Time:** ~25 minutes  
**Difficulty:** Intermediate

---

## Overview

In this tutorial, you'll:

1. Define a custom workflow template with phases
2. Create the workflow through the API
3. Execute it with inputs
4. Monitor progress and inspect outputs

By the end, you'll be able to build reusable workflows beyond built-in templates.

---

## Prerequisites

- Complete [Your First Workflow](Your-First-Workflow.md)
- Backend running on `http://localhost:8000`
- Auth token available (`dev-token` for local development)

Helpful references:

- [Workflows System](../03-Features/Workflows-System.md)
- [Workflow Templates Matrix](../07-Appendices/Workflow-Templates-Matrix.md)

---

## Step 1: Create a Custom Workflow

Create a workflow with three phases (`research`, `draft`, `assess`):

```bash
curl -X POST http://localhost:8000/api/custom-workflows \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-custom-blog-workflow",
    "description": "Research, draft, and assess a developer-focused article",
    "phases": [
      {
        "phase_name": "research",
        "agent": "research_agent",
        "inputs": {
          "topic": "${initial.topic}",
          "keywords": "${initial.keywords}"
        }
      },
      {
        "phase_name": "draft",
        "agent": "creative_agent",
        "inputs": {
          "research_summary": "${phases.research.output.summary}",
          "target_audience": "${initial.target_audience}",
          "tone": "${initial.tone}"
        }
      },
      {
        "phase_name": "assess",
        "agent": "qa_agent",
        "inputs": {
          "content": "${phases.draft.output.content}"
        }
      }
    ]
  }'
```

**Expected response:** HTTP `201` with a created workflow ID and metadata.

---

## Step 2: Execute the Custom Workflow

```bash
curl -X POST http://localhost:8000/api/workflows/execute/my-custom-blog-workflow \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "initial_inputs": {
      "topic": "How AI is changing software QA",
      "keywords": ["AI", "QA", "automation"],
      "target_audience": "engineering managers",
      "tone": "practical"
    },
    "model": "ollama/mistral"
  }'
```

Save the returned `execution_id`.

---

## Step 3: Monitor Execution

Poll status:

```bash
curl -X GET http://localhost:8000/api/workflows/<execution_id> \
  -H "Authorization: Bearer dev-token"
```

Or monitor in real-time:

- [WebSocket Real-Time](../03-Features/WebSocket-Real-Time.md)

---

## Step 4: Validate Outputs

When `status` becomes `complete`, verify:

- Each phase has `status: complete`
- `final_output.content_markdown` exists
- Quality or assessment output is present from `assess`

---

## Troubleshooting

### `404` on execute endpoint

- Confirm workflow name exactly matches `name` used at creation.

### `422` validation errors

- Ensure required `initial_inputs` fields are present.
- Confirm phase input mapping keys exist (for example, `phases.research.output.summary`).

### Workflow stuck in `pending`

- Check backend logs and model availability.
- Confirm database connectivity.

See: [Troubleshooting Hub](../06-Troubleshooting/README.md)

---

## Next Steps

- Add a conditional or optional phase to your workflow.
- Use [Model Selection](../03-Features/Model-Selection.md) to compare cost/quality.
- Explore [Capability-Based Tasks](Capability-Based-Tasks.md) for intent-driven routing.
