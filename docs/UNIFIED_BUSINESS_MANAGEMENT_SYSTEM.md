# Unified Business Management System Architecture

**Date:** January 1, 2026  
**Status:** Design & Implementation Phase  
**Version:** 2.0 (Dual-Path Architecture)

---

## Executive Summary

A unified business management system with **two input channels** converging on a **single service layer**:

1. **Manual Path** (Oversight Hub): User fills form → Creates task → Service executes
2. **Agent Path** (Poindexter Chat): User describes intent → Intent recognized → Service executes

Both paths execute the same TaskService actions, ensuring **single source of truth** and **no duplication**.

---

## System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    BUSINESS MANAGEMENT SYSTEM                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  INPUT CHANNEL 1          INPUT CHANNEL 2                       │
│  (Manual/UI)              (Agent/Chat)                          │
│  ┌──────────────┐          ┌──────────────┐                    │
│  │ Oversight    │          │ Poindexter   │                    │
│  │ Hub (React)  │          │ Assistant    │                    │
│  └──────┬───────┘          └──────┬───────┘                    │
│         │                         │                             │
│         ├─ CreateTaskModal        ├─ Conversation Tab (Chat)   │
│         │  (Blog Post Form)       │  (Traditional Q&A)         │
│         │                         │                             │
│         └─ task_service.js        └─ Agent Tab (Intent)        │
│            (REST client)             ├─ nlp_intent_recognizer  │
│                                      │ (Parse natural lang)    │
│                                      │                         │
│                                      └─ Detect workflow type   │
│         ┌──────────────────────────────┬──────────────────┐   │
│         │                              │                  │   │
│         └─────────────────────┬────────┘                  │   │
│                               │                           │   │
│                        SERVICE LAYER                       │   │
│                     (Backend Unified Bus)                  │   │
│                               │                           │   │
│         ┌─────────────────────┴──────────────────┐        │   │
│         │     ServiceRegistry & Services         │        │   │
│         │  ┌─────────────────────────────────┐  │        │   │
│         │  │  TaskService                    │  │        │   │
│         │  │  ├─ action_create_task          │  │        │   │
│         │  │  ├─ action_list_tasks           │  │        │   │
│         │  │  ├─ action_get_task             │  │        │   │
│         │  │  └─ action_update_task_status   │  │        │   │
│         │  └─────────────────────────────────┘  │        │   │
│         │                                       │        │   │
│         │  (Other Services - Market, Financial) │        │   │
│         └───────────────────┬───────────────────┘        │   │
│                             │                            │   │
│         ┌───────────────────┴────────────────┐           │   │
│         │     Persistence Layer              │           │   │
│         │  PostgreSQL (Single DB)            │           │   │
│         │  ├─ tasks table                    │           │   │
│         │  ├─ task_results table             │           │   │
│         │  └─ conversation_history table     │           │   │
│         └───────────────────────────────────┘           │   │
│                                                          │   │
└──────────────────────────────────────────────────────────┘   │
```

---

## Path 1: Manual Task Creation (Oversight Hub)

### User Flow

```
User Interface (Oversight Hub)
    ↓
CreateTaskModal.jsx (React component)
    ↓
    User fills form:
    - Topic: "AI Trends in 2026"
    - Word Count: 2000
    - Style: "technical"
    - Tone: "professional"
    ↓
Form Submit Handler
    ↓
taskService.js::createTask()
    ↓
HTTP POST /api/services/tasks/actions/create_task
    ↓
services_registry_routes.py
    ↓
ServiceRegistry.execute_action()
    ↓
TaskService.action_create_task()
    ↓
PostgreSQL: INSERT into tasks table
    ↓
Return: { id: "task-123", status: "pending", ... }
    ↓
CreateTaskModal receives task ID
    ↓
UI Updates: Show success, task added to queue
```

### Code Flow

**Frontend (React):**

```javascript
// web/oversight-hub/src/components/tasks/CreateTaskModal.jsx
const handleSubmit = async (e) => {
  const formData = {
    task_name: 'Blog Post: AI Trends',
    topic: 'AI Trends in 2026',
    category: 'blog_post',
    // ... other fields
  };

  const taskId = await createTask(formData); // Calls taskService.js
};
```

**Service Layer (JavaScript):**

```javascript
// web/oversight-hub/src/services/taskService.js
export const createTask = async (taskData) => {
  // NOW: Calls service layer instead of direct API
  const result = await makeRequest(
    '/api/services/tasks/actions/create_task', // Service API
    'POST',
    taskData
  );
  return result.id;
};
```

**Backend (Python):**

```python
# src/cofounder_agent/routes/services_registry_routes.py
@router.post("/api/services/{service}/actions/{action}")
async def execute_service_action(
    service: str,
    action: str,
    request_body: dict,
    current_user: User = Depends(get_current_user)
):
    registry = get_service_registry()
    result = await registry.execute_action(
        service=service,      # "tasks"
        action=action,        # "create_task"
        input_data=request_body,
        user_id=current_user.id
    )
    return result
```

**Service Implementation:**

```python
# src/cofounder_agent/services/task_service_example.py
class TaskService(ServiceBase):
    async def action_create_task(self, input_data: dict) -> ActionResult:
        """Create a new task"""
        task = Task(
            task_name=input_data['task_name'],
            topic=input_data['topic'],
            category=input_data['category'],
            status='pending',
            # ...
        )
        db.add(task)
        db.commit()

        return ActionResult(
            status=ActionStatus.SUCCESS,
            data={'id': task.id, 'status': task.status}
        )
```

---

## Path 2: Natural Language Intent (Poindexter Agent)

### User Flow

```
Poindexter Chat Interface
    ↓
User Types (Agent Mode):
"Create a blog post about AI trends, 2000 words, professional tone"
    ↓
NaturalLanguageInput.jsx
    ↓
Submit to Backend
    ↓
nlp_intent_recognizer.py
    ↓
Parse Intent:
- Detected Intent: "content_generation"
- Confidence: 0.95
- Parameters:
  - topic: "AI trends"
  - word_count: 2000
  - tone: "professional"
    ↓
Map to TaskService Action
    ↓
HTTP POST /api/services/tasks/actions/create_task
    ↓
(Same as Manual Path from here...)
    ↓
ServiceRegistry.execute_action()
    ↓
TaskService.action_create_task()
    ↓
PostgreSQL: INSERT into tasks table
    ↓
Return Result to Poindexter
    ↓
Chat Response:
"✓ Task created: Blog Post (Task #123)
Status: Pending
Word Count: 2000
Tone: Professional
Estimated Time: 15 minutes"
```

### Code Flow

**Frontend (React Chat):**

```javascript
// web/oversight-hub/src/components/IntelligentOrchestrator/NaturalLanguageInput.jsx
const handleSubmit = async (e) => {
  const userMessage = 'Create a blog post about AI trends...';

  // Send to backend for intent recognition
  const response = await fetch('/api/agents/intent-action', {
    method: 'POST',
    body: JSON.stringify({
      message: userMessage,
      mode: 'agent', // vs 'conversation'
    }),
  });

  const result = await response.json();
  // result = { task_id: "task-123", intent: "content_generation", ... }

  setChat((prev) => [
    ...prev,
    {
      role: 'assistant',
      content: `✓ Task created: ${result.task_summary}`,
    },
  ]);
};
```

**Backend NLP Processing:**

```python
# src/cofounder_agent/services/nlp_intent_recognizer.py
async def process_user_request(message: str, user_id: str):
    """Parse natural language and execute appropriate action"""

    # 1. Recognize intent
    intent_match = await recognize_intent(message)

    if not intent_match:
        return {
            'success': False,
            'error': 'Could not understand request'
        }

    # 2. Extract parameters
    params = await extract_parameters(
        intent_type=intent_match.intent_type,
        message=message
    )

    # 3. Execute via service layer
    registry = get_service_registry()
    result = await registry.execute_action(
        service='tasks',
        action='create_task',  # Mapped from intent
        input_data=params,
        user_id=user_id
    )

    return {
        'success': result.status == ActionStatus.SUCCESS,
        'task_id': result.data.get('id'),
        'intent': intent_match.intent_type
    }
```

**Route Handler:**

```python
# src/cofounder_agent/routes/intelligent_orchestrator_routes.py
@router.post("/api/agents/intent-action")
async def intent_to_action(
    request: IntentActionRequest,
    current_user: User = Depends(get_current_user)
):
    """Convert natural language intent to service action"""

    if request.mode == 'conversation':
        # Traditional chat - just respond
        response = await llm.chat(request.message)
        return {'response': response}

    elif request.mode == 'agent':
        # Parse intent and execute action
        result = await nlp_intent_recognizer.process_user_request(
            request.message,
            current_user.id
        )
        return result
```

---

## Key Design Principles

### 1. **Single Service Layer (Source of Truth)**

- Both paths execute through `ServiceRegistry.execute_action()`
- Same TaskService implementation for both
- No duplicated business logic
- Changes to TaskService automatically affect both paths

### 2. **Intent Mapping Patterns**

| User Input (NLP)                 | Detected Intent    | Mapped Action                           |
| -------------------------------- | ------------------ | --------------------------------------- |
| "Create a blog post about..."    | content_generation | action_create_task                      |
| "Generate 5 social posts for..." | social_media       | action_create_task (with social params) |
| "What tasks are running?"        | task_list          | action_list_tasks                       |
| "Update task #123 to draft"      | task_update        | action_update_task_status               |

### 3. **Schema-Driven Design**

TaskService defines action schemas once:

```python
ServiceAction(
    name="create_task",
    input_schema=JsonSchema(
        properties={
            "task_name": {...},
            "topic": {...},
            "category": {...}
        }
    )
)
```

- Manual form uses same schema for validation
- NLP extractor outputs same schema format
- Service layer validates identically

### 4. **Dual Mode in Poindexter**

**Conversation Mode:**

```
User: "What is the best approach for content marketing?"
→ Call LLM chat (no action execution)
→ Return conversational response
```

**Agent Mode:**

```
User: "Create a blog post about content marketing"
→ Parse intent (content_generation)
→ Execute TaskService.action_create_task()
→ Return task creation confirmation
```

---

## Implementation Roadmap

### Phase 1: Service Layer Foundation ✅ DONE

- ✅ ServiceBase pattern created
- ✅ TaskService example implementation
- ✅ services_registry_routes created

### Phase 2: Integration (CURRENT - ~2-3 hours)

- [ ] Update main.py - Initialize ServiceRegistry
- [ ] Update main.py - Register TaskService
- [ ] Update taskService.js - Call service layer endpoints
- [ ] Create intelligent_orchestrator_routes.py for NLP routing
- [ ] Update nlp_intent_recognizer to execute via service layer
- [ ] Update NaturalLanguageInput component for agent mode

### Phase 3: Service Migration (Week 2)

- [ ] Migrate ModelRouter to ServiceBase (model selection service)
- [ ] Migrate PublishingService to ServiceBase
- [ ] Migrate MetricsService to ServiceBase
- [ ] Create service composition patterns

### Phase 4: Advanced Features (Week 3-4)

- [ ] LLM tool integration (serve service registry as tools)
- [ ] Workflow composition (chain service actions)
- [ ] Error recovery and retries
- [ ] Service performance optimization

---

## Data Flow: End-to-End Example

### Scenario: Create Blog Post (Via NLP)

```
STEP 1: User types in Poindexter
┌─────────────────────────────────────┐
│ "Write a 2000-word blog post about  │
│  machine learning trends for CTOs"  │
└─────────────────────────────────────┘
           ↓
STEP 2: Route to Intent Recognition
NLP Intent Recognizer:
- Detect: content_generation (95% confidence)
- Extract Parameters:
  topic: "machine learning trends"
  target_audience: "CTOs"
  word_count: 2000
  style: inferred from context
           ↓
STEP 3: Service Layer Execution
POST /api/services/tasks/actions/create_task
{
  "task_name": "Blog Post: Machine Learning Trends",
  "topic": "machine learning trends",
  "target_audience": "CTOs",
  "word_count": 2000,
  "category": "blog_post"
}
           ↓
STEP 4: ServiceRegistry Route
ServiceRegistry.execute_action(
  service="tasks",
  action="create_task",
  input_data={...}
)
           ↓
STEP 5: TaskService Execute
TaskService.action_create_task(...)
- Validate input against action schema
- Create Task record
- Insert into PostgreSQL
- Return ActionResult with task ID
           ↓
STEP 6: Response to Poindexter
{
  "success": true,
  "task_id": "task-abc123",
  "task_name": "Blog Post: Machine Learning Trends",
  "status": "pending",
  "estimated_duration": "15 minutes"
}
           ↓
STEP 7: Chat Response to User
"✓ Task created successfully!

📝 Blog Post: Machine Learning Trends
Status: Pending
Word Count Target: 2000
Audience: CTOs
Estimated Time: 15 minutes

The content generation pipeline will:
1. Research current ML trends
2. Create initial draft
3. QA review and refinement
4. Generate featured image
5. Publish to blog

I'll keep you updated on progress!"
```

---

## File Structure (After Phase 2 Implementation)

```
src/cofounder_agent/
├── main.py (UPDATED)
│   ├── Initialize ServiceRegistry
│   ├── Register TaskService
│   └── Include services_registry_routes
│
├── services/
│   ├── service_base.py ✅ (exists)
│   ├── task_service_example.py ✅ (exists)
│   ├── task_service.py (NEW: production impl)
│   ├── nlp_intent_recognizer.py (UPDATED)
│   └── intelligent_orchestrator.py (NEW)
│
├── routes/
│   ├── services_registry_routes.py ✅ (exists)
│   ├── task_routes.py (UNCHANGED - for backward compat)
│   └── intelligent_orchestrator_routes.py (NEW)
│
└── models/
    ├── task_models.py (UNCHANGED)
    └── intent_models.py (NEW)

web/oversight-hub/src/
├── services/
│   └── taskService.js (UPDATED)
│       └── Now calls /api/services/tasks/actions/*
│
└── components/
    ├── tasks/
    │   └── CreateTaskModal.jsx (UNCHANGED)
    │       └── Uses updated taskService.js
    │
    └── IntelligentOrchestrator/
        ├── NaturalLanguageInput.jsx (UPDATED)
        │   └── Supports conversation + agent modes
        └── AgentModeToggle.jsx (NEW)
            └── Switch between conversation/agent
```

---

## Backward Compatibility Guarantee

**Existing endpoints remain unchanged:**

- ✅ `POST /api/tasks` → existing task_routes.py (unchanged)
- ✅ `GET /api/tasks` → existing task_routes.py (unchanged)
- ✅ `PATCH /api/tasks/{id}` → existing task_routes.py (unchanged)

**New service layer endpoints (optional):**

- `POST /api/services/tasks/actions/create_task`
- `POST /api/services/tasks/actions/list_tasks`
- `GET /api/services/registry` (schema discovery)

**Both paths work:**

```
Manual Form → taskService.js → /api/services/tasks/actions/create_task
Chat Intent → nlp_intent_recognizer → /api/services/tasks/actions/create_task

Both create same task in PostgreSQL
Both use same TaskService
Both fully backward compatible
```

---

## Error Handling Strategy

### Manual Path Error Flow

```
User submits form
  ↓
taskService.js catches error
  ↓
Display user-friendly message in modal
  ↓
Retry button available
```

### Agent Path Error Flow

```
User types request
  ↓
Intent recognition fails
  ↓
Response: "I didn't understand that request. Try: 'Create a blog post about...'"
  ↓
User rephrases
```

---

## Security Considerations

- ✅ All paths require JWT authentication (`get_current_user`)
- ✅ TaskService actions validate input against schemas
- ✅ PostgreSQL queries use parameterized statements
- ✅ Rate limiting on service endpoints (if needed)
- ✅ Audit trail of all actions (service_request_id)

---

## Performance Characteristics

| Operation          | Manual Path | Agent Path | Notes                         |
| ------------------ | ----------- | ---------- | ----------------------------- |
| Task Creation      | ~200ms      | ~500ms     | Agent path adds NLP parsing   |
| Task Listing       | ~100ms      | ~100ms     | Direct service call           |
| Intent Recognition | N/A         | ~300ms     | Depends on message complexity |
| DB Query           | ~50ms       | ~50ms      | Identical for both paths      |

---

## Testing Strategy

### Manual Path Tests

```python
# Test CreateTaskModal → taskService.js flow
def test_manual_task_creation():
    form_data = {...}
    task_id = create_task(form_data)
    assert task_exists(task_id)
    assert task.status == 'pending'
```

### Agent Path Tests

```python
# Test NLP → TaskService flow
def test_agent_task_creation():
    message = "Create a blog post about AI"
    result = process_intent(message)
    assert result.success
    assert task_exists(result.task_id)
```

### Integration Tests

```python
# Test both paths create same data
def test_dual_path_consistency():
    manual_task = create_task_manual(...)
    agent_task = create_task_agent(...)

    assert manual_task.topic == agent_task.topic
    assert manual_task.category == agent_task.category
    # Both stored in same table
```

---

## Next Steps

1. **Review This Architecture** (15 min)
   - Confirm dual-path approach aligns with vision
   - Clarify any questions about data flows

2. **Phase 2 Implementation** (~2-3 hours)
   - Update main.py
   - Update taskService.js
   - Create intelligent_orchestrator_routes.py
   - Update nlp_intent_recognizer

3. **Testing & Validation** (~1 hour)
   - Test manual path still works
   - Test agent path creates correct tasks
   - Verify database consistency

4. **Phase 3: Service Expansion** (Week 2)
   - Migrate additional services to ServiceBase
   - Expand intent recognition for more workflows

---

## References

- [ServiceBase Pattern](./SERVICE_LAYER_ARCHITECTURE.md)
- [Backward Compatibility](./SERVICE_LAYER_BACKWARD_COMPATIBILITY.md)
- [Integration Checklist](./SERVICE_LAYER_INTEGRATION_CHECKLIST.md)
