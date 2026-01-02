# Phase 2 Implementation Plan: Unified Service Layer Integration

**Timeline:** ~2-3 hours  
**Risk Level:** 🟢 Very Low (additive, non-breaking)  
**Status:** Ready to Begin

---

## Overview

Phase 2 integrates the service layer to support both **manual task creation** (CreateTaskModal) and **natural language intent** (Poindexter Agent) through a unified backend.

### What Changes

| Component             | Current                  | After Phase 2                                            | Impact                  |
| --------------------- | ------------------------ | -------------------------------------------------------- | ----------------------- |
| CreateTaskModal       | Direct API: `/api/tasks` | Service Layer: `/api/services/tasks/actions/create_task` | ✅ Transparent to user  |
| taskService.js        | Direct API calls         | Service layer endpoints                                  | ✅ Internal change only |
| NaturalLanguageInput  | Chat-only                | Chat + Agent modes                                       | ✅ New feature          |
| nlp_intent_recognizer | Parse intent only        | Parse + Execute via service                              | ✅ New integration      |
| main.py               | No service registry      | Initialize + register                                    | ✅ Additive             |

### What Stays the Same

- ✅ CreateTaskModal.jsx (unchanged)
- ✅ PostgreSQL schema (unchanged)
- ✅ Existing `/api/tasks` endpoint (unchanged, kept for backward compat)
- ✅ All authentication logic (unchanged)
- ✅ CreateTaskModal UI/UX (unchanged)

---

## Implementation Steps

### STEP 1: Update main.py - Add Imports

**File:** `src/cofounder_agent/main.py`

**Time:** 5 minutes

**Current State:**

```python
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
# ... other imports
```

**Action: Add these imports**

```python
# Add after existing imports:
from src.cofounder_agent.services.service_base import get_service_registry
from src.cofounder_agent.services.task_service_example import TaskService
from src.cofounder_agent.routes.services_registry_routes import router as services_router
```

**Verification:**

```bash
cd src/cofounder_agent
python -c "from main import get_service_registry, TaskService, services_router; print('✓ Imports OK')"
```

**Expected Output:**

```
✓ Imports OK
```

---

### STEP 2: Update main.py - Initialize ServiceRegistry

**File:** `src/cofounder_agent/main.py`

**Time:** 5 minutes

**Location:** After app initialization, before route registration

**Current Code (find this):**

```python
app = FastAPI(
    title="Co-Founder Agent API",
    version="1.0.0",
    description="AI Co-Founder orchestration system"
)

# Middleware setup
app.add_middleware(CORSMiddleware, ...)
# ... other middleware ...

# Route registration
app.include_router(task_routes)
# ... other routes ...
```

**Action: Add initialization between middleware and routes**

```python
app = FastAPI(
    title="Co-Founder Agent API",
    version="1.0.0",
    description="AI Co-Founder orchestration system"
)

# Middleware setup
app.add_middleware(CORSMiddleware, ...)
# ... other middleware ...

# NEW: Initialize Service Registry
try:
    service_registry = get_service_registry()
    logger.info("✓ ServiceRegistry initialized successfully")
except Exception as e:
    logger.error(f"✗ Failed to initialize ServiceRegistry: {e}")
    service_registry = None

# Register core services if registry available
if service_registry:
    try:
        task_service = TaskService(service_registry)
        service_registry.register(task_service)
        logger.info("✓ TaskService registered with ServiceRegistry")
    except Exception as e:
        logger.error(f"✗ Failed to register TaskService: {e}")

# Route registration
app.include_router(task_routes)
app.include_router(services_router)  # NEW: Add service registry routes
# ... other routes ...
```

**Verification:**

```bash
cd src/cofounder_agent
python -m uvicorn main:app --reload &
sleep 3
curl http://localhost:8000/health
```

**Expected Output:**

```json
{ "status": "ok", "services": 1 }
```

---

### STEP 3: Update taskService.js - Switch to Service Layer

**File:** `web/oversight-hub/src/services/taskService.js`

**Time:** 10 minutes

**Current Code:**

```javascript
export const createTask = async (taskData) => {
  const result = await makeRequest('/api/tasks', 'POST', taskData, ...);

  if (result.error) {
    throw new Error(`Could not create task: ${result.error}`);
  }

  return result.id || result;
};
```

**Action: Update to use service layer**

```javascript
export const createTask = async (taskData) => {
  // UPDATED: Now calls service layer instead of direct API
  // Same behavior, but goes through unified service registry
  const result = await makeRequest(
    '/api/services/tasks/actions/create_task',
    'POST',
    taskData,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not create task: ${result.error}`);
  }

  return result.data?.id || result.id || result;
};
```

**Similarly update for other task operations:**

```javascript
// getTasks - update endpoint
export const getTasks = async (offset = 0, limit = 20, filters = {}) => {
  const params = new URLSearchParams({
    offset: offset.toString(),
    limit: limit.toString(),
    ...(filters.status && { status: filters.status }),
  });

  // UPDATED: Use service layer endpoint
  const result = await makeRequest(
    `/api/services/tasks/actions/list_tasks?${params}`,
    'GET',
    null,
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not fetch tasks: ${result.error}`);
  }

  // Service layer returns wrapped response
  return result.data?.tasks || result.tasks || [];
};

// getTask - update endpoint
export const getTask = async (taskId) => {
  const result = await makeRequest(
    `/api/services/tasks/actions/get_task`,
    'POST',
    { task_id: taskId },
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not fetch task: ${result.error}`);
  }

  return result.data || result;
};

// updateTask - update endpoint
export const updateTask = async (taskId, updates) => {
  const result = await makeRequest(
    `/api/services/tasks/actions/update_task_status`,
    'POST',
    { task_id: taskId, ...updates },
    false,
    null,
    API_TIMEOUT
  );

  if (result.error) {
    throw new Error(`Could not update task: ${result.error}`);
  }

  return result.data || result;
};
```

**Verification:**

```bash
cd web/oversight-hub
npm run build
# Should build successfully with no errors
```

**Expected Output:**

```
✓ Compiled successfully
```

---

### STEP 4: Create Intelligent Orchestrator Routes

**File:** `src/cofounder_agent/routes/intelligent_orchestrator_routes.py`

**Time:** 20 minutes

**Action: Create new file with following content**

```python
"""
Intelligent Orchestrator Routes

Bridges NLP intent recognition and service layer execution.
Supports both conversation and agent modes for Poindexter Assistant.
"""

import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException

from src.cofounder_agent.models.user_models import User
from src.cofounder_agent.middleware.auth import get_current_user
from src.cofounder_agent.services.nlp_intent_recognizer import NLPIntentRecognizer
from src.cofounder_agent.services.service_base import get_service_registry
from src.cofounder_agent.models.orchestrator_models import (
    IntentActionRequest,
    IntentActionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["intelligent-orchestrator"])

# Initialize NLP recognizer
nlp_recognizer = NLPIntentRecognizer()


class IntentActionRequest:
    """Request for intent parsing and action execution."""
    mode: str  # "conversation" or "agent"
    message: str
    context: Optional[Dict[str, Any]] = None


class IntentActionResponse:
    """Response from intent action processing."""
    mode: str
    success: bool
    message: Optional[str]
    task_id: Optional[str] = None
    intent_type: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/intent-action", response_model=dict)
async def process_intent_action(
    request: IntentActionRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Process user request in conversation or agent mode.

    Args:
        request: Intent/action request with mode and message
        current_user: Authenticated user

    Returns:
        Response with execution result or conversation response
    """

    if request.mode == "conversation":
        # Traditional chat - respond conversationally
        return await handle_conversation_mode(
            request.message,
            request.context or {},
            current_user
        )

    elif request.mode == "agent":
        # Intent recognition and action execution
        return await handle_agent_mode(
            request.message,
            request.context or {},
            current_user
        )

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {request.mode}. Must be 'conversation' or 'agent'"
        )


async def handle_conversation_mode(
    message: str,
    context: Dict[str, Any],
    user: User
) -> dict:
    """Handle traditional chat conversation.

    User asks questions, LLM responds conversationally.
    No action execution.
    """
    logger.info(f"Conversation mode: {message[:100]}")

    try:
        # Import LLM service here to avoid circular imports
        from src.cofounder_agent.services.llm_service import LLMService

        llm_service = LLMService()
        response = await llm_service.chat(
            message=message,
            conversation_history=context.get("history", []),
            user_id=user.id
        )

        return {
            "mode": "conversation",
            "success": True,
            "message": response,
            "intent_type": None
        }

    except Exception as e:
        logger.error(f"Conversation error: {e}")
        return {
            "mode": "conversation",
            "success": False,
            "message": "I encountered an error processing your request. Please try again.",
            "error": str(e)
        }


async def handle_agent_mode(
    message: str,
    context: Dict[str, Any],
    user: User
) -> dict:
    """Handle agent mode with intent recognition and action execution.

    1. Parse user message to recognize intent
    2. Extract parameters from message
    3. Execute appropriate service action
    4. Return result
    """
    logger.info(f"Agent mode: {message[:100]}")

    try:
        # Step 1: Recognize intent from message
        intent_match = await nlp_recognizer.recognize_intent(message, context)

        if not intent_match:
            return {
                "mode": "agent",
                "success": False,
                "message": (
                    "I couldn't understand that request. "
                    "Try saying something like: 'Create a blog post about AI trends' "
                    "or 'List my current tasks'"
                ),
                "intent_type": None,
                "error": "No matching intent"
            }

        logger.info(
            f"Intent: {intent_match.intent_type} "
            f"(confidence: {intent_match.confidence:.2f})"
        )

        # Step 2: Map intent to service action
        service_action_mapping = {
            "content_generation": ("tasks", "create_task"),
            "social_media": ("tasks", "create_task"),
            "task_list": ("tasks", "list_tasks"),
            "task_update": ("tasks", "update_task_status"),
            "market_analysis": ("market_insight", "analyze_market"),
            "financial_analysis": ("financial", "analyze_costs"),
        }

        service_action = service_action_mapping.get(intent_match.intent_type)

        if not service_action:
            return {
                "mode": "agent",
                "success": False,
                "message": f"I don't know how to handle '{intent_match.intent_type}' yet.",
                "intent_type": intent_match.intent_type,
                "error": "No service mapping"
            }

        service_name, action_name = service_action

        # Step 3: Extract parameters
        parameters = intent_match.parameters

        # Step 4: Execute via service layer
        registry = get_service_registry()

        if not registry:
            return {
                "mode": "agent",
                "success": False,
                "message": "Service registry not available. Please try again.",
                "error": "Registry unavailable"
            }

        result = await registry.execute_action(
            service=service_name,
            action=action_name,
            input_data=parameters,
            user_id=user.id
        )

        # Step 5: Format response
        if result.status == "SUCCESS":
            # Build friendly response message
            response_message = format_action_result(
                intent_match.intent_type,
                result
            )

            return {
                "mode": "agent",
                "success": True,
                "message": response_message,
                "intent_type": intent_match.intent_type,
                "task_id": result.data.get("id"),
                "parameters": parameters
            }

        else:
            error_msg = result.data.get("error", "Unknown error")
            return {
                "mode": "agent",
                "success": False,
                "message": f"Failed to execute action: {error_msg}",
                "intent_type": intent_match.intent_type,
                "error": error_msg
            }

    except Exception as e:
        logger.error(f"Agent mode error: {e}", exc_info=True)
        return {
            "mode": "agent",
            "success": False,
            "message": "An error occurred while processing your request.",
            "error": str(e)
        }


def format_action_result(intent_type: str, result: dict) -> str:
    """Format service action result as friendly chat message."""

    if intent_type == "content_generation":
        task = result.data
        return (
            f"✓ Blog post task created!\n\n"
            f"📝 **{task.get('task_name', 'Blog Post')}**\n"
            f"Status: {task.get('status', 'pending').upper()}\n"
            f"ID: {task.get('id')}\n\n"
            f"The content generation pipeline will:\n"
            f"1. Research your topic\n"
            f"2. Create a draft\n"
            f"3. Review and refine\n"
            f"4. Generate featured image\n"
            f"5. Publish to blog\n\n"
            f"Estimated time: ~15 minutes"
        )

    elif intent_type == "social_media":
        task = result.data
        return (
            f"✓ Social media task created!\n\n"
            f"📱 Social Post Content\n"
            f"Status: {task.get('status', 'pending').upper()}\n"
            f"ID: {task.get('id')}\n\n"
            f"I'll create and schedule posts across your platforms."
        )

    elif intent_type == "task_list":
        tasks = result.data.get("tasks", [])
        if not tasks:
            return "You don't have any tasks yet."

        task_list = "\n".join([
            f"  • {t.get('task_name')} ({t.get('status')})"
            for t in tasks[:5]
        ])

        return (
            f"✓ You have {len(tasks)} task(s):\n\n"
            f"{task_list}"
        )

    else:
        # Generic success message
        return f"✓ Action completed successfully!"


@router.get("/services")
async def list_available_services(
    current_user: User = Depends(get_current_user),
):
    """List all available services and their actions."""
    registry = get_service_registry()

    if not registry:
        raise HTTPException(status_code=503, detail="Service registry unavailable")

    return {
        "services": registry.list_services(),
        "service_count": len(registry.services)
    }
```

**Verification:**

```bash
cd src/cofounder_agent
python -c "from routes.intelligent_orchestrator_routes import router; print('✓ Routes OK')"
```

---

### STEP 5: Update main.py - Include New Routes

**File:** `src/cofounder_agent/main.py`

**Time:** 5 minutes

**Find the route registration section:**

```python
# Route registration
app.include_router(task_routes)
# ... other routers ...
```

**Add the new router:**

```python
# Route registration
app.include_router(task_routes)
app.include_router(services_router)  # Service registry API
app.include_router(intelligent_orchestrator_router)  # NLP + intent handling
# ... other routers ...
```

**Also add import at top:**

```python
from src.cofounder_agent.routes.intelligent_orchestrator_routes import router as intelligent_orchestrator_router
```

---

### STEP 6: Update nlp_intent_recognizer.py - Fix Pattern Compilation

**File:** `src/cofounder_agent/services/nlp_intent_recognizer.py`

**Time:** 10 minutes

**Issue:** The `_compile_patterns()` method is incomplete

**Fix the method:**

```python
def _compile_patterns(self) -> None:
    """Compile regex patterns for faster matching."""
    for intent_type, config in self.INTENT_PATTERNS.items():
        compiled = []
        for keyword_pattern in config["keywords"]:
            try:
                compiled.append(re.compile(keyword_pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Invalid pattern for {intent_type}: {keyword_pattern}: {e}")

        self.patterns[intent_type] = {
            "compiled_patterns": compiled,
            "confidence_boost": config["confidence_boost"],
            "parameter_extractors": config.get("parameter_extractors", []),
        }
```

**Also fix the recognize_intent method:**

```python
async def recognize_intent(
    self,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[IntentMatch]:
    """Recognize user intent from message."""

    if not message or not isinstance(message, str):
        return None

    message_lower = message.lower().strip()
    best_match = None
    best_confidence = 0.0

    for intent_type, pattern_config in self.patterns.items():
        for pattern in pattern_config["compiled_patterns"]:
            if pattern.search(message_lower):
                confidence = min(
                    0.99,  # Cap at 99%
                    pattern_config["confidence_boost"]
                )

                if confidence > best_confidence:
                    best_confidence = confidence

                    # Extract parameters
                    parameters = asyncio.run(self._extract_parameters(
                        intent_type,
                        message,
                        context or {},
                        pattern_config["parameter_extractors"]
                    ))

                    best_match = IntentMatch(
                        intent_type=intent_type,
                        confidence=confidence,
                        workflow_type=intent_type,
                        parameters=parameters,
                        raw_message=message
                    )

                break  # Move to next intent type

    return best_match
```

---

### STEP 7: Test the Integration

**Time:** 15 minutes

**Test 1: Manual Path Still Works**

```bash
# Start all services
npm run dev

# In another terminal, test manual task creation
curl -X POST http://localhost:8000/api/services/tasks/actions/create_task \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "task_name": "Test Blog Post",
    "topic": "AI Integration",
    "category": "blog_post"
  }'

# Expected: { "success": true, "task_id": "...", ... }
```

**Test 2: Open Oversight Hub**

```
1. Open http://localhost:3001 in browser
2. Click "Task Management"
3. Click "Create Task"
4. Fill form: Blog Post, Topic: "Test Topic"
5. Submit
6. Verify task appears in list below
```

**Test 3: NLP Intent Recognition (via curl or chat)**

```bash
curl -X POST http://localhost:8000/api/agents/intent-action \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "mode": "agent",
    "message": "Create a blog post about machine learning"
  }'

# Expected: Task created with proper intent parsing
```

**Test 4: Verify Database**

```bash
# Check PostgreSQL
psql -U postgres -d glad_labs

# Inside psql:
SELECT COUNT(*) as task_count FROM tasks;
SELECT task_name, status FROM tasks ORDER BY created_at DESC LIMIT 3;
```

---

## Rollback Plan

If something breaks, rollback is simple (all changes are additive):

### Rollback Step 1: Revert taskService.js

```bash
git checkout web/oversight-hub/src/services/taskService.js
```

### Rollback Step 2: Revert main.py changes

```bash
git checkout src/cofounder_agent/main.py
```

### Rollback Step 3: Delete new files

```bash
rm -f src/cofounder_agent/routes/intelligent_orchestrator_routes.py
```

### Rollback Step 4: Restart services

```bash
npm run dev
```

**Result:** System operates on original endpoints, zero impact.

---

## Verification Checklist

After completing all steps:

- [ ] `npm run build` completes successfully (web/oversight-hub)
- [ ] Backend starts without errors: `python -m uvicorn main:app --reload`
- [ ] CreateTaskModal still shows on http://localhost:3001/tasks
- [ ] Creating task via form still works
- [ ] Tasks appear in task list
- [ ] Service registry endpoints respond: `curl http://localhost:8000/api/services`
- [ ] Intent action endpoint works: `curl http://localhost:8000/api/agents/intent-action`
- [ ] All tasks in PostgreSQL

---

## Success Criteria

✅ **Phase 2 Complete When:**

1. Both manual form and agent mode create tasks
2. Both paths create identical task records in PostgreSQL
3. taskService.js calls new service layer endpoints
4. nlp_intent_recognizer recognizes and executes intents
5. No breaking changes to existing CreateTaskModal
6. All tests pass
7. Backward compatibility maintained

---

## Estimated Timeline

| Step                                      | Time   | Cumulative      |
| ----------------------------------------- | ------ | --------------- |
| 1: Update main.py imports                 | 5 min  | 5 min           |
| 2: Initialize ServiceRegistry             | 5 min  | 10 min          |
| 3: Update taskService.js                  | 10 min | 20 min          |
| 4: Create intelligent_orchestrator_routes | 20 min | 40 min          |
| 5: Include new routes in main.py          | 5 min  | 45 min          |
| 6: Fix nlp_intent_recognizer              | 10 min | 55 min          |
| 7: Test integration                       | 15 min | 70 min          |
| **TOTAL**                                 |        | **~70 minutes** |

---

## Common Issues & Fixes

### Issue: "ServiceRegistry not found"

```
Fix: Ensure service_base.py is in src/cofounder_agent/services/
Verify: ls -la src/cofounder_agent/services/service_base.py
```

### Issue: "Import error for intelligent_orchestrator_routes"

```
Fix: Ensure file created at src/cofounder_agent/routes/intelligent_orchestrator_routes.py
Verify: python -c "from routes.intelligent_orchestrator_routes import router"
```

### Issue: "Tasks not appearing in database"

```
Fix: Check PostgreSQL is running and DATABASE_URL is correct in .env.local
Verify: psql -U postgres -d glad_labs -c "SELECT * FROM tasks LIMIT 1;"
```

### Issue: "Authentication failures on service endpoints"

```
Fix: Ensure JWT token is valid and included in Authorization header
Verify: curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8000/api/services
```

---

## Next: Phase 3 Planning

After Phase 2 completes successfully, Phase 3 will:

- [ ] Migrate ModelRouter to ServiceBase
- [ ] Migrate PublishingService to ServiceBase
- [ ] Migrate ContentService to ServiceBase
- [ ] Extend NLP intent recognition for additional workflows
- [ ] Add service composition (chain actions)

---

## Questions?

Refer to:

- [Unified Business Management System Architecture](./UNIFIED_BUSINESS_MANAGEMENT_SYSTEM.md)
- [Service Layer Architecture](./SERVICE_LAYER_ARCHITECTURE.md)
- [Backward Compatibility Guarantee](./SERVICE_LAYER_BACKWARD_COMPATIBILITY.md)
