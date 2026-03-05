# Dependency Injection Framework Exception Policy

**Last Updated:** March 4, 2026  
**Status:** Phase 3 DI-6 Complete  
**Reference:** Issue #7 (DI Standardization Epic)

---

## Overview

This document defines the **framework-level exception policy** for `app.state` usage across the Glad Labs codebase. It clarifies which `app.state` attributes are **framework-level** (allowed to remain) vs. **application-level** (must use Depends()).

### Policy Statement

> **Application-level services must use ServiceContainer + Depends() for dependency injection.**  
> **Framework-level runtime state may use app.state for critical coordination functions.**

---

## Framework-Level Exceptions (Allowed)

These `app.state` attributes are **framework-level** and may be used directly by the FastAPI application lifecycle and critical infrastructure:

### 1. **Startup Coordination**

```python
# Location: main.py lifespan (L84-85, L159-160)
app.state.startup_error     # Type: Optional[str]
app.state.startup_complete  # Type: bool
```

**Purpose:** FastAPI application startup status coordination.

**Justification:** These flags are critical for:

- Detecting startup failures before accepting requests
- Health check endpoints determining application readiness
- Sequential startup orchestration across services
- External monitoring and load balancer health checks

**Usage Pattern:**

```python
# Set during startup
app.state.startup_error = str(e)
app.state.startup_complete = True

# Check in health endpoints and middleware
if app.state.startup_error:
    # Application is degraded
    return {"status": "degraded", "error": startup_error}
elif not app.state.startup_complete:
    # Application is still starting
    return {"status": "starting"}
```

**Allowed Consumers:**

- `main.py` lifespan event handler
- Health check endpoints (`/health`, `/api/health`)
- Middleware (startup status checks)
- External monitoring systems

---

## Application-Level Services (Must Use Depends)

These attributes **must NOT** be accessed via `app.state` directly. They must be injected using FastAPI's Depends() pattern:

### Application-Level Services That Must Use Depends()

```python
# ❌ WRONG: Direct app.state access
database = app.state.database
tasks = await database.pool.fetch("SELECT * FROM tasks")

# ✅ RIGHT: Depends() injection
@app.get("/api/tasks")
async def get_tasks(database=Depends(get_database_dependency)):
    tasks = await database.pool.fetch("SELECT * FROM tasks")
    return tasks
```

### Services Using Depends() (DI Phases 1-3)

| Service | Depends Provider | Location | Status |
|---------|-------------------|----------|--------|
| `database` | `get_database_dependency()` | route_utils.py:207 | ✅ Complete |
| `orchestrator` | `get_orchestrator_dependency()` | route_utils.py:227 | ✅ Complete |
| `task_executor` | `get_task_executor_dependency()` | route_utils.py:234 | ✅ Complete |
| `redis_cache` | `get_redis_cache_dependency()` | route_utils.py:286 | ✅ Complete |
| `redis_cache` (optional) | `get_redis_cache_optional()` | route_utils.py:293 | ✅ Complete |
| `workflow_engine` | `get_workflow_engine_dependency()` | route_utils.py:268 | ✅ Complete |
| `custom_workflows_service` | `get_custom_workflows_service_dependency()` | route_utils.py:301 | ✅ Complete |
| `template_execution_service` | `get_template_execution_service_dependency()` | route_utils.py:315 | ✅ Complete |

---

## Migration Progress

### Phase 1 ✅ COMPLETE (Routes)

- **24 endpoints** migrated from `request.app.state` to Depends()
- **6 new providers** created in route_utils.py
- Files: agents_routes.py, model_routes.py, custom_workflows_routes.py, workflow_routes.py, main.py (/command)

### Phase 2 ✅ COMPLETE (Startup + Background Tasks)

- **DI-3:** Removed 11 direct service assignments from main.py lifespan
  - Kept only: `startup_error`, `startup_complete`
- **DI-4:** TaskExecutor refactored to use ServiceContainer for orchestrator resolution
- Files: main.py, task_executor.py, startup_manager.py

### Phase 3 ✅ COMPLETE (Health + Policy)

- **DI-5:** Health endpoints refactored to use Depends()
  - `/api/health` → uses `Depends(get_database_dependency)` + `Depends(get_redis_cache_optional)`
  - `/` → uses `Depends(get_database_dependency)`
  - `/api/metrics` → uses `Depends(get_database_dependency)`
- **DI-6:** Framework exception policy documented (this file)
- Files: main.py, health_service.py

---

## Implementation Patterns

### Pattern 1: Required Dependency

```python
from fastapi import Depends
from utils.route_utils import get_database_dependency

@app.get("/api/tasks")
async def list_tasks(database=Depends(get_database_dependency)):
    """Database is required, will raise RuntimeError if not initialized"""
    return await database.get_tasks()
```

### Pattern 2: Optional Dependency

```python
from fastapi import Depends
from utils.route_utils import get_redis_cache_optional

@app.get("/api/health")
async def health_check(cache=Depends(get_redis_cache_optional)):
    """Cache is optional, returns None if not initialized"""
    if cache:
        cached_value = await cache.get("key")
    return {"status": "ok"}
```

### Pattern 3: Multiple Dependencies

```python
@app.post("/api/workflow/execute")
async def execute_workflow(
    workflow: WorkflowRequest,
    database=Depends(get_database_dependency),
    orchestrator=Depends(get_orchestrator_dependency),
    cache=Depends(get_redis_cache_optional),
):
    """Multiple services injected via Depends()"""
    # Services are available as parameters
    result = await orchestrator.execute(workflow)
    await database.save_result(result)
    if cache:
        await cache.set("result", result)
    return result
```

---

## Why This Policy?

### Benefits of Depends() Pattern

1. **Testability:** Easy to mock dependencies in unit tests
2. **Type Safety:** IDE provides better autocomplete and type checking
3. **Documentation:** Function signature clearly shows dependencies
4. **Consistency:** All services accessed through unified pattern
5. **Flexibility:** Can change service implementation without changing routes

### Why Framework Exception Exists

Startup coordination state (`startup_error`, `startup_complete`) is an exception because:

1. **FastAPI Framework Requirement:** The lifespan context manager needs to set app state
2. **Health Check Dependency:** External monitoring needs to check startup status immediately
3. **Critical Coordination:** Determines whether application can accept requests
4. **No Alternative:** FastAPI doesn't provide other mechanisms for startup status injection

---

## Compliance Checklist

When adding new code to Glad Labs, verify:

- [ ] All services are injected via `Depends(get_*_dependency())`
- [ ] Direct `app.state` access is only for `startup_error` or `startup_complete`
- [ ] Health endpoints use Depends() for database and cache access
- [ ] Route functions have type hints (return type and parameter types)
- [ ] Services are registered in ServiceContainer during startup
- [ ] Depends providers are imported from utils.route_utils

---

## Future Enhancements

Potential improvements for framework-level state:

1. **Custom Context Manager:** Replace app.state for startup coordination
2. **Event System:** Emit startup complete event instead of polling app.state
3. **Factory Functions:** Create dependency providers dynamically at startup
4. **Service Registry:** Built-in FastAPI service registry (when FastAPI supports it)

---

## References

| Document | Purpose |
|----------|---------|
| [TECHNICAL_DEBT_TRACKING.md](./archive-active/root-cleanup-feb2026/TECHNICAL_DEBT_TRACKING.md) | Overall tech debt plan |
| [02-ARCHITECTURE_AND_DESIGN.md](./02-ARCHITECTURE_AND_DESIGN.md) | System architecture |
| `src/cofounder_agent/utils/route_utils.py` | Depends() provider implementations |
| `src/cofounder_agent/main.py` | Framework-level app.state usage |

---

## Questions?

For questions about this policy:

1. Check the [TECHNICAL_DEBT_TRACKING.md](./archive-active/root-cleanup-feb2026/TECHNICAL_DEBT_TRACKING.md) **Issue #7 DI Standardization** section
2. Review Route Files migrated in Phase 1 for implementation examples
3. Examine route_utils.py for all available Depends() providers

**Policy Owner:** Development Team  
**Last Review:** March 4, 2026
