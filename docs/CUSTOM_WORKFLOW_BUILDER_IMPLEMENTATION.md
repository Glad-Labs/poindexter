# Custom Workflow Builder - Implementation Summary

**Status:** Phase 2 Frontend Implementation Complete  
**Date:** January 22, 2025  
**Version:** 1.0.0

---

## Overview

The custom workflow builder is a comprehensive system for creating, managing, and executing complex workflows by chaining AI services and phases together. Users can:

1. **Create custom workflows** by visually composing phases with the React Flow canvas
2. **Browse & manage workflows** with full CRUD operations
3. **Use pre-built templates** for common patterns (blog posts, social media, etc.)
4. **Execute workflows** with custom input data
5. **Track execution** status and results

---

## Architecture

### Backend Components (Python/FastAPI)

**Database Schema:**

```
custom_workflows (PostgreSQL table)
├── id (UUID PRIMARY KEY)
├── name (VARCHAR) - Workflow name
├── description (TEXT) - Workflow description
├── phases (JSONB) - Array of phase configurations
├── owner_id (VARCHAR) - User ID for ownership
├── created_at, updated_at (TIMESTAMP WITH TIME ZONE)
├── tags (JSONB) - Categorization tags
└── is_template (BOOLEAN) - Whether workflow is shared template

Indexes:
- owner_id (filter by user)
- is_template (find templates)
- created_at DESC, updated_at DESC (sorting)
```

**Service Layer:**

- **File:** `src/cofounder_agent/services/custom_workflows_service.py` (439 lines)
- **Purpose:** Business logic for workflow CRUD, validation, and discovery
- **Key Methods:**
  - `create_workflow()` - Create new workflow with validation
  - `list_workflows()` - Paginated list with filtering
  - `get_workflow()` - Fetch single workflow with ownership check
  - `update_workflow()` - Update with permission validation
  - `delete_workflow()` - Delete with cascade handling
  - `validate_workflow()` - Full workflow structure validation
  - `get_available_phases()` - Discover available phase types

**API Endpoints:**

- `POST /api/workflows/custom` - Create workflow
- `GET /api/workflows/custom` - List workflows (paginated)
- `GET /api/workflows/custom/{id}` - Get workflow details
- `PUT /api/workflows/custom/{id}` - Update workflow
- `DELETE /api/workflows/custom/{id}` - Delete workflow
- `POST /api/workflows/custom/{id}/execute` - Execute workflow
- `GET /api/workflows/available-phases` - Discover available phases

**Request/Response Models:**

- **File:** `src/cofounder_agent/schemas/custom_workflow_schemas.py` (395 lines)
- **Models:**
  - `PhaseConfig` - Individual phase configuration
  - `CustomWorkflow` - Complete workflow definition
  - `WorkflowExecutionRequest/Response` - Execution data
  - `AvailablePhase`, `AvailablePhasesResponse` - Discovery models
  - `WorkflowListResponse`, `WorkflowValidationResult` - Response models

**Database Migration:**

- **File:** `src/cofounder_agent/alembic/versions/0020_create_custom_workflows_table.py`
- **Purpose:** Create workflows table with proper indexes and constraints
- **Status:** Ready to execute on next database migration

**Integration Points:**

- **StartupManager:** Initializes `CustomWorkflowsService` on startup
- **Main.py:** Injects service into FastAPI app.state
- **Route Registration:** `custom_workflows_router` registered in `route_registration.py`

### Frontend Components (React)

**Tab-Based Interface:**

- **File:** `web/oversight-hub/src/components/pages/UnifiedServicesPanel.jsx`
- **Purpose:** Main page component with 4 tabs
- **Tabs:**
  1. **Phase 4 Services** - Browse existing services (legacy)
  2. **Create Custom Workflow** - Visual canvas for building workflows
  3. **My Workflows** - List of user's created workflows with actions
  4. **Templates** - Pre-built workflow templates with quick-execute

**Visual Canvas Component:**

- **File:** `web/oversight-hub/src/components/WorkflowCanvas.jsx` (267 lines)
- **Purpose:** React Flow visual editor for workflow composition
- **Features:**
  - Left sidebar with draggable phase palette
  - Center canvas for visual phase composition
  - Right panel for phase configuration
  - Auto-layout (sequential) and auto-connection
  - Save dialog with metadata
  - Phase removal with edge cleanup

**Phase Node Component:**

- **File:** `web/oversight-hub/src/components/PhaseNode.jsx` (48 lines)
- **Purpose:** Custom React Flow node representing a workflow phase
- **Displays:**
  - Phase name with bold label
  - Agent chip (color-coded)
  - Timeout and retry indicators
  - Left (target) and right (source) handles for connections

**Phase Configuration Panel:**

- **File:** `web/oversight-hub/src/components/PhaseConfigPanel.jsx` (165 lines)
- **Purpose:** Right-side panel for editing phase settings
- **Controls:**
  - Agent selection (text field with suggestions)
  - Description (multiline textarea)
  - Timeout slider (10-3600 seconds)
  - Max retries slider (0-10)
  - Quality threshold slider (0-1, for assess phases)
  - Skip on error toggle
  - Required phase toggle
  - Save/Remove buttons with unsaved change indicator

**Frontend API Service:**

- **File:** `web/oversight-hub/src/services/workflowBuilderService.js` (180 lines)
- **Purpose:** API client for workflow operations
- **Methods:**
  - `getAvailablePhases()` - Fetch available workflows
  - `createWorkflow(definition)` - Create new workflow
  - `listWorkflows(options)` - List with pagination
  - `getWorkflow(id)` - Fetch details
  - `updateWorkflow(id, updates)` - Modify workflow
  - `deleteWorkflow(id)` - Remove workflow
  - `executeWorkflow(id, inputData)` - Run workflow
  - `getExecutionStatus(executionId)` - Track execution
  - `exportWorkflowToJSON(workflow)` - Export to JSON
  - `importWorkflowFromJSON(jsonString)` - Import from JSON

**Dependencies Installed:**

- `reactflow` - Visual node editor library (MIT license)
- Material-UI components for layout
- lucide-react icons for UI elements

---

## Data Flow

### Create Workflow Flow

```
User Input
  ↓
WorkflowCanvas (React)
  ↓ (Phase drag/drop, configuration)
PhaseNode Components (Visual representation)
  ↓ (Save button clicked)
workflowBuilderService.createWorkflow()
  ↓ (JSON payload)
POST /api/workflows/custom
  ↓
CustomWorkflowsService.create_workflow()
  ↓ (Validation, UUID generation)
PostgreSQL custom_workflows table
  ↓ (ID returned)
Frontend state update
  ↓
My Workflows tab display
```

### Execute Workflow Flow

```
User selects "Execute"
  ↓
workflowBuilderService.executeWorkflow(id, inputData)
  ↓
POST /api/workflows/custom/{id}/execute
  ↓
CustomWorkflowsService (loads workflow from DB)
  ↓
WorkflowEngine (TBD - not yet implemented)
  ↓ (Convert phases to WorkflowPhase objects)
Orchestrator (Execute phases sequentially or in parallel)
  ↓
Results persisted to PostgreSQL
  ↓
Execution ID returned to frontend
```

---

## Phase Configuration Structure

Each phase in a workflow has the following configuration:

```json
{
  "name": "research",
  "agent": "content_agent",
  "description": "Gather background information and research",
  "timeout_seconds": 300,
  "max_retries": 2,
  "skip_on_error": false,
  "required": true,
  "quality_threshold": 0.8,
  "metadata": {}
}
```

**Field Descriptions:**

- `name` - Unique phase identifier in workflow
- `agent` - Which agent handles this phase
- `description` - Human-readable phase purpose
- `timeout_seconds` - Max execution time (10-3600s)
- `max_retries` - Number of retry attempts on failure (0-10)
- `skip_on_error` - Continue if phase fails
- `required` - Phase must succeed for workflow to continue
- `quality_threshold` - Minimum quality score (0-1)
- `metadata` - Additional configuration key-value pairs

---

## Available Phases

Hardcoded in `custom_workflows_service.get_available_phases()`:

| Phase | Agent | Description | Default Timeout |
|-------|-------|-------------|-----------------|
| research | content | Gather background and identify key points | 300s |
| draft | content | Generate initial draft with brand voice | 600s |
| assess | content | Critique and suggest improvements | 300s |
| refine | content | Apply feedback and improve draft | 300s |
| image | image | Select/generate visuals and alt text | 600s |
| publish | content | Format for CMS, add SEO, convert to markdown | 300s |

---

## Completed Tasks ✅

### Backend

- [x] Database schema with proper indexes
- [x] Pydantic validation models
- [x] Service layer with full CRUD
- [x] 7 REST API endpoints
- [x] Database migration script
- [x] Service initialization (startup_manager)
- [x] Dependency injection setup

### Frontend

- [x] Tab-based UnifiedServicesPanel main page
- [x] React Flow canvas component
- [x] Phase node visual component
- [x] Phase configuration side panel
- [x] Frontend API service (workflowBuilderService.js)
- [x] React Flow library installation

---

## Remaining Tasks 🔄

### High Priority

1. **Workflow Execution Integration**
   - Implement POST /api/workflows/custom/{id}/execute endpoint
   - Convert CustomWorkflow → WorkflowPhase objects
   - Integrate with WorkflowEngine
   - Return execution ID for tracking

2. **User ID Context**
   - Extract user ID from JWT token
   - Set in request.state for dependency injection
   - Update OAuth callback to populate request.state.user_id

3. **Testing**
   - Unit tests for CustomWorkflowsService
   - Integration tests for REST endpoints
   - Frontend tests for components
   - End-to-end workflow creation → execution

### Medium Priority

4. **Template Execution**
   - Implement "Execute" button for pre-built templates
   - Load template definition and populate canvas
   - Quick-execute without editing

2. **Workflow Cloning**
   - Add "Clone" action in My Workflows
   - Create copy with "_copy" suffix

3. **Execution History**
   - Create workflow_executions table
   - Track execution status and results
   - Display in dedicated tab

### Lower Priority

7. **Advanced Features**
   - Conditional branching (if/then logic)
   - Parallel phase execution
   - Custom phase types
   - Version control for workflows
   - Sharing/collaboration features

---

## Database Setup

**Migration Execution:**

```bash
# Run pending migrations (from project root)
cd src/cofounder_agent
alembic upgrade head
```

**Verify Setup:**

```sql
-- Check table creation
SELECT * FROM information_schema.tables WHERE table_name = 'custom_workflows';

-- Check indexes
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'custom_workflows';
```

---

## Testing the Implementation

### 1. Test Backend Available Phases Endpoint

```bash
curl http://localhost:8000/api/workflows/available-phases
```

**Expected Response:**

```json
{
  "phases": [
    {
      "id": "research",
      "name": "research",
      "agent": "content_agent",
      "description": "Gather background information...",
      "version": "1.0"
    },
    // ... 5 more phases
  ]
}
```

### 2. Test Create Workflow

```bash
curl -X POST http://localhost:8000/api/workflows/custom \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Blog Workflow",
    "description": "Full blog post generation",
    "phases": [
      {
        "name": "research",
        "agent": "content",
        "timeout_seconds": 300,
        "max_retries": 2
      },
      {
        "name": "draft",
        "agent": "content",
        "timeout_seconds": 600,
        "max_retries": 2
      }
    ]
  }'
```

### 3. Test Frontend UI

1. Navigate to Oversight Hub (<http://localhost:3001>)
2. Go to `/services` route (Services page)
3. Click "Create Custom Workflow" tab
4. Verify:
   - Available phases list appears on left
   - Can drag phases onto canvas
   - Phases auto-connect
   - Can edit phase configuration (right panel)
   - Can save workflow with metadata

---

## Configuration

### Environment Variables

```env
# Optional: Control workflow execution behavior
WORKFLOW_EXECUTION_TIMEOUT=3600  # Max total execution time
WORKFLOW_PARALLEL_EXECUTION=false  # Allow parallel phases
WORKFLOW_RETRY_DELAY=5  # Seconds between retries
```

### Service Initialization Order

**In `startup_manager.py` (after workflow manager, before image generators):**

```python
# Initialize custom workflows service
self.custom_workflows_service = CustomWorkflowsService(
    database_service=self.database_service
)
```

---

## Code Quality

**Type Safety:** Pydantic models with validation  
**Async:** Full async/await in service and routes  
**Error Handling:** Custom exceptions with user-friendly messages  
**Documentation:** JSDoc and Python docstrings throughout  
**Testing:** Pytest-compatible structure, ready for tests

---

## File Locations

```
Backend:
├── src/cofounder_agent/
│   ├── schemas/custom_workflow_schemas.py
│   ├── services/custom_workflows_service.py
│   ├── routes/custom_workflows_routes.py
│   ├── alembic/versions/0020_create_custom_workflows_table.py
│   ├── utils/startup_manager.py (modified)
│   ├── utils/route_registration.py (modified)
│   └── main.py (modified)

Frontend:
├── web/oversight-hub/
│   ├── src/
│   │   ├── components/
│   │   │   ├── pages/UnifiedServicesPanel.jsx (modified)
│   │   │   ├── WorkflowCanvas.jsx
│   │   │   ├── PhaseNode.jsx
│   │   │   └── PhaseConfigPanel.jsx
│   │   └── services/
│   │       └── workflowBuilderService.js
│   └── package.json (reactflow added)
```

---

## Next Steps

### Immediate (Next Session)

1. Run database migration to create custom_workflows table
2. Extract and set user ID in request context (auth integration)
3. Implement workflow execution endpoint
4. Test full create → list → execute flow

### Short Term

5. Add workflow execution tracking and history
2. Implement template quick-execute
3. Write comprehensive test suite

### Long Term

8. Advanced workflow features (conditionals, loops, etc.)
2. Workflow versioning and collaboration
3. Performance optimizations for large workflows

---

## Support & Documentation

**Key Files for Reference:**

- Architecture: [02-ARCHITECTURE_AND_DESIGN.md](../../docs/02-ARCHITECTURE_AND_DESIGN.md)
- Development: [04-DEVELOPMENT_WORKFLOW.md](../../docs/04-DEVELOPMENT_WORKFLOW.md)
- Agents: [05-AI_AGENTS_AND_INTEGRATION.md](../../docs/05-AI_AGENTS_AND_INTEGRATION.md)

**Common Issues:**

| Issue | Solution |
|-------|----------|
| User ID is "test-user-123" | Extract from JWT token in auth middleware |
| Workflow execution returns null | Implement integration with WorkflowEngine |
| React Flow canvas not rendering | Verify reactflow installation: npm list reactflow |
| Database table doesn't exist | Run migration: alembic upgrade head |

---

## Changelog

**v1.0.0 - January 22, 2025**

- Initial implementation of custom workflow builder
- Backend: Schemas, service, routes, database migration
- Frontend: UnifiedServicesPanel with 4 tabs, visual canvas, configuration UI
- React Flow integration for drag-drop canvas
- Frontend API service for workflow operations
- Workflow CRUD with ownership and template support
