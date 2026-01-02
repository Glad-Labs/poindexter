# Wiring Checklist: Unified Business Management System

**Date:** January 1, 2026  
**Status:** Most Logic Already Implemented - Wiring Needed  
**Focus:** Avoiding Duplication - Connect Existing Components

---

## ✅ What's Already Built (No Duplication)

### Backend Service Layer (500+ lines - READY)

- ✅ `service_base.py` - ServiceBase, ServiceRegistry, ServiceAction, JsonSchema
- ✅ `task_service_example.py` - TaskService with 4 actions (create, list, get, update)
- ✅ `services_registry_routes.py` - API endpoints for service discovery + execution
- ✅ Routes registered via `utils/route_registration.py` in startup

### Frontend Components (READY TO WIRE)

- ✅ `CreateTaskModal.jsx` - Manual form for creating tasks
- ✅ `taskService.js` - REST client calling `/api/tasks`
- ✅ `NaturalLanguageInput.jsx` - Chat form for natural language requests
- ✅ `IntelligentOrchestrator.jsx` - Main orchestrator panel (routes, state, polling)
- ✅ `nlp_intent_recognizer.py` - Parse intent from natural language (NOT wired)
- ✅ `workflow_router.py` - Route requests to pipelines (NOT wired)

### Database & Persistence (READY)

- ✅ PostgreSQL with `tasks` table
- ✅ `database_service.py` - Full CRUD operations
- ✅ Task schema supports both manual and agent-created tasks

### Chat/Agent Infrastructure (READY)

- ✅ `chat_routes.py` - Chat endpoint
- ✅ WebSocket routes for real-time updates
- ✅ Poindexter Assistant UI with Conversation + Agent modes

---

## 🔴 What Needs Wiring (3 Critical Connections)

### CONNECTION 1: taskService.js → Service Layer

**Current:** POST /api/tasks (direct, works fine)  
**Target:** POST /api/services/tasks/actions/create_task (service layer)

**File to Update:** `web/oversight-hub/src/services/taskService.js`

**Change Required:**

```javascript
// BEFORE
export const createTask = async (taskData) => {
  const result = await makeRequest('/api/tasks', 'POST', taskData, ...);
};

// AFTER (Service Layer)
export const createTask = async (taskData) => {
  const result = await makeRequest(
    '/api/services/tasks/actions/create_task',
    'POST',
    { params: taskData },  // ServiceLayer expects {params, context}
    ...
  );
};
```

**Impact:** Manual form (CreateTaskModal) automatically uses service layer  
**Risk:** Low - Same database, same schema  
**Time:** 5 minutes

---

### CONNECTION 2: nlp_intent_recognizer → Service Layer Execution

**Current:** Parse intent only (doesn't execute)  
**Target:** Parse intent + Execute TaskService action

**Files to Update:**

1. `src/cofounder_agent/services/nlp_intent_recognizer.py` - Add execute method
2. `src/cofounder_agent/services/workflow_router.py` - Use service layer

**Change Required (nlp_intent_recognizer.py):**

```python
async def execute_recognized_intent(
    self,
    intent_match: IntentMatch,
    user_id: str
) -> Dict[str, Any]:
    """Execute intent via service layer"""
    registry = get_service_registry()
    service = registry.get_service('tasks')

    result = await service.execute_action(
        intent_match.intent_type,  # e.g., 'create_task'
        intent_match.parameters,
        user_id=user_id
    )
    return result
```

**Impact:** NLP chat (NaturalLanguageInput) → Intent → Service Layer → Task  
**Risk:** Low - Using same TaskService, same database  
**Time:** 10 minutes

---

### CONNECTION 3: Poindexter Agent Mode → nlp_intent_recognizer

**Current:** NaturalLanguageInput component exists, but Agent mode logic incomplete  
**Target:** Switch between Conversation (chat-only) and Agent (intent detection)

**Files to Update:**

1. `web/oversight-hub/src/components/IntelligentOrchestrator/NaturalLanguageInput.jsx` - Add Agent mode toggle + intent execution
2. `src/cofounder_agent/routes/chat_routes.py` - Add agent mode endpoint

**Change Required (NaturalLanguageInput.jsx):**

```javascript
const [mode, setMode] = useState('conversation'); // or 'agent'

const handleSubmit = async (message) => {
  if (mode === 'conversation') {
    // Direct LLM chat
    const response = await callChatAPI(message);
    setResponse(response);
  } else if (mode === 'agent') {
    // Intent recognition + execution
    const intent = await recognizeIntent(message);
    const result = await executeServiceAction(intent);
    setResponse(result);
  }
};
```

**Impact:** "Agent" tab can now decode user intent and execute tasks automatically  
**Risk:** Low - Intent recognition optional, user can still use conversation mode  
**Time:** 15 minutes

---

## 📋 Summary: What Needs Wiring

| Connection | Type             | Component             | Work                 | Time   |
| ---------- | ---------------- | --------------------- | -------------------- | ------ |
| 1          | Frontend→Backend | taskService.js        | Update POST endpoint | 5 min  |
| 2          | Backend Logic    | nlp_intent_recognizer | Add execute method   | 10 min |
| 3          | Frontend Logic   | NaturalLanguageInput  | Add agent mode       | 15 min |

**Total Wiring Time:** ~30 minutes

---

## 🎯 Result After Wiring

```
User Flow 1: Manual Path (Unchanged Behavior)
────────────────────────────────────────────
UI: Oversight Hub
  ↓
Form: CreateTaskModal
  ↓ [User fills: Topic, Word Count, Style, Tone]
  ↓
Button: Submit
  ↓
taskService.js::createTask()  [NOW CALLS SERVICE LAYER]
  ↓
POST /api/services/tasks/actions/create_task
  ↓
ServiceRegistry executes TaskService.action_create_task()
  ↓
PostgreSQL: Insert task
  ↓
Task appears in queue ✅


User Flow 2: Agent Path (New)
────────────────────────────
UI: Poindexter Assistant
  ↓
Switch to: "Agent" Mode
  ↓
Input: "Create a blog post about AI trends"
  ↓
nlp_intent_recognizer.recognize_intent()
  ↓
Result: IntentMatch {
  intent_type: 'create_task',
  parameters: {topic: 'AI trends', style: 'technical', ...}
}
  ↓
nlp_intent_recognizer.execute_recognized_intent()  [NEW]
  ↓
ServiceRegistry executes TaskService.action_create_task(...)  [SAME AS PATH 1]
  ↓
PostgreSQL: Insert task
  ↓
Task appears in queue ✅
```

---

## 🧠 Architecture After Wiring

```
                UNIFIED BUSINESS MANAGEMENT SYSTEM
                ─────────────────────────────────

        INPUT 1: Manual (Form)      INPUT 2: Agent (Chat)
        ──────────────────────      ─────────────────────
        CreateTaskModal.jsx         Poindexter Assistant
        (React component)           (Agent Mode)
              │                             │
              │                            │
              ├─ User fills form          ├─ User describes intent
              │                            │
              └──────┬──────┬──────────────┘
                     │      │
                     ▼      ▼
        taskService.js    nlp_intent_recognizer.py
        (REST client)     (Intent parser + executor)
              │                     │
              └──────────┬──────────┘
                         │
                         ▼
           SERVICE LAYER (Unified Backend)
           ─────────────────────────────────
           ServiceRegistry
           ├── TaskService
           │   ├── action_create_task
           │   ├── action_list_tasks
           │   ├── action_get_task
           │   └── action_update_task_status
           └── (Other Services - Market, Financial, etc.)
                         │
                         ▼
                   PostgreSQL
                   (Single DB)
                   ├── tasks
                   ├── task_results
                   └── conversation_history


KEY: Both paths → Same TaskService → Same Database → Single Source of Truth ✅
```

---

## 🚀 Implementation Order

**Step 1: Wire CONNECTION 1 (5 min)**

- Update `taskService.js` to call service layer endpoint
- Verify manual form still works

**Step 2: Wire CONNECTION 2 (10 min)**

- Add execution method to `nlp_intent_recognizer.py`
- Test intent recognition + execution

**Step 3: Wire CONNECTION 3 (15 min)**

- Add Agent mode toggle to `NaturalLanguageInput.jsx`
- Test Agent mode execution

**Verification:** Both paths create tasks, both appear in queue, no duplication

---

## ✅ What You DON'T Need to Change

- ✅ CreateTaskModal.jsx - No changes to UI
- ✅ PostgreSQL schema - No changes
- ✅ Existing `/api/tasks` endpoint - Keep for backward compatibility
- ✅ Task execution pipeline - No changes
- ✅ Authentication/Authorization - No changes
- ✅ Service base classes - Already complete
- ✅ Task creation logic - Already in TaskService

---

## 🔍 Verification Checklist

After wiring, verify:

1. **Manual Path**

   ```bash
   # Create task via Oversight Hub form
   # Verify: Task appears in queue with correct data
   ```

2. **Agent Path**

   ```bash
   # Switch Poindexter to Agent mode
   # Type: "Create a blog post about machine learning"
   # Verify: Task appears in queue with recognized parameters
   ```

3. **No Duplication**

   ```bash
   # Check PostgreSQL tasks table
   # Verify: Both paths use same table, same schema
   # Verify: No duplicate code in service execution
   ```

4. **Backward Compatibility**
   ```bash
   # Verify old /api/tasks endpoint still works
   # Verify existing integrations unaffected
   ```

---

## 📊 Before vs After

| Aspect                 | Before Wiring       | After Wiring                 |
| ---------------------- | ------------------- | ---------------------------- |
| Manual task creation   | Works (form)        | Works (form + service layer) |
| NLP intent recognition | Recognizes intent   | Recognizes + executes        |
| Agent mode             | N/A (incomplete)    | Full functionality           |
| Code duplication       | Potential (2 paths) | Zero (single service)        |
| Database consistency   | Single table        | Single table ✅              |
| LLM tool integration   | API exposed         | API + Intent execution       |

---

## 🎓 Key Principle

**All user intent (whether manual form or natural language) → Unified Service Layer → Single Source of Truth**

No matter which input path (form or chat), the system uses:

1. Same TaskService implementation
2. Same database table
3. Same execution logic
4. Same task format

This eliminates duplication and makes future maintenance easier.

---

## ⚡ Next Action

Proceed to wiring in this order:

1. Update `taskService.js` (Connection 1)
2. Update `nlp_intent_recognizer.py` (Connection 2)
3. Update `NaturalLanguageInput.jsx` (Connection 3)
4. Test all three paths
5. Verify no duplication

Total implementation time: **~30 minutes**
