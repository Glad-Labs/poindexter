# CUSTOM_WORKFLOW_BUILDER_PHASE_1 - Database Schema and Models

**Status:** Completed  
**Date:** January 22, 2026  
**Version:** 1.0.0

---

## Overview

Phase 1 of the Custom Workflow Builder establishes the foundational database schema and data models that support workflow creation, management, and execution.

## Database Schema

### Workflows Table

```sql
CREATE TABLE custom_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    phases JSONB NOT NULL,
    owner_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    tags JSONB DEFAULT '[]',
    is_template BOOLEAN DEFAULT FALSE,
    
    CONSTRAINT fk_owner FOREIGN KEY (owner_id) REFERENCES users(id)
);

-- Indexes for performance
CREATE INDEX idx_workflows_owner_id ON custom_workflows(owner_id);
CREATE INDEX idx_workflows_is_template ON custom_workflows(is_template);
CREATE INDEX idx_workflows_created_at ON custom_workflows(created_at DESC);
```

### Phase Structure (JSONB)

```json
{
  "phases": [
    {
      "id": "phase_0",
      "name": "research",
      "agent_name": "content_agent",
      "input_mapping": {},
      "user_inputs": {}
    },
    {
      "id": "phase_1",
      "name": "draft",
      "agent_name": "content_agent",
      "input_mapping": {
        "previous_output": "phases.0.output"
      },
      "user_inputs": {}
    }
  ]
}
```

## Data Models (Pydantic)

### Core Schemas

```python
# Phase configuration
class PhaseConfig(BaseModel):
    id: str
    name: str
    agent_name: str
    input_mapping: Dict[str, str] = {}
    user_inputs: Dict[str, Any] = {}
    skip: bool = False

# Complete workflow definition
class CustomWorkflow(BaseModel):
    id: Optional[UUID] = None
    name: str
    description: Optional[str] = None
    phases: List[PhaseConfig]
    owner_id: str
    tags: List[str] = []
    is_template: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

# Workflow execution request
class WorkflowExecutionRequest(BaseModel):
    initial_inputs: Dict[str, Any] = {}
    override_phases: Optional[Dict[str, Dict[str, Any]]] = None
    skip_phases: Optional[List[str]] = None

# Workflow execution response
class WorkflowExecutionResponse(BaseModel):
    workflow_id: str
    execution_id: str
    status: str
    phase_results: Dict[str, Any]
    executed_at: datetime
```

## Core Concepts

### 1. Workflow Definition

A workflow is a sequence of phases that execute in order, with each phase:

- Operating on inputs from user or previous phase outputs
- Performing a specific task (research, draft, critique, etc.)
- Producing outputs for the next phase
- Supporting optional skip/user override

### 2. Phase Mapping

Phases are automatically connected through semantic input/output matching:

- `output` from phase N feeds into phase N+1 automatically
- Users can override with custom `input_mapping`
- Phase registry defines available phases and their contracts

### 3. Ownership & Permissions

- Each workflow has an `owner_id` linked to the user who created it
- Only owners can modify or delete their workflows
- Templates are shared (is_template=true)
- Public templates are discoverable by all users

### 4. Extensibility

New phases can be added by:

1. Registering in `PhaseRegistry`
2. Defining input/output contracts
3. Adding to execution pipeline
4. No database changes needed

## Migration

### Alembic Migration File

- **File:** `src/cofounder_agent/alembic/versions/0020_create_custom_workflows_table.py`
- **Status:** Ready to execute
- **Commands:**

```bash
# Upgrade
alembic upgrade head

# Downgrade
alembic downgrade -1
```

## Entity Relationships

```plaintext
users (1) ──────── (*) custom_workflows
                         │
                         ├─ phases[] (JSONB)
                         └─ tags[] (JSONB)
```

## Next Steps

→ **Phase 2:** Frontend UI and API Client  
Build the React UI components and API integration layer for workflow management.

---

**File References:**

- Schema Definition: `src/cofounder_agent/schemas/custom_workflow_schemas.py`
- Migration: `src/cofounder_agent/alembic/versions/0020_create_custom_workflows_table.py`
- Documentation: See Phase 2+ for implementation details
