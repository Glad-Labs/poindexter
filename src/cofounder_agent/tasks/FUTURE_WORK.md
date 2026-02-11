# Tasks Framework - Future Work

**Status:** ⏸️ **NOT CURRENTLY USED** - Preserved for future architectural evolution

## Overview

The `tasks/` folder contains an alternative task execution model designed for a potential future refactoring. Currently, the production content pipeline uses **Agents** directly ([src/cofounder_agent/agents/](../agents/)) rather than this Task framework.

## Current Architecture (Active)

**Execution Model:** Agent-based  
**Location:** [agents/content_agent/agents/](../agents/content_agent/agents/)  
**Entry Point:** [services/unified_orchestrator.py](../services/unified_orchestrator.py)

```
API Route → Task stored in DB → TaskExecutor polls DB 
  → UnifiedOrchestrator._handle_content_creation() 
    → ResearchAgent.run() 
    → CreativeAgent.run() 
    → QAAgent.run() 
    → PublishingAgent.run()
```

## Future Alternative (This Folder)

**Proposed Model:** Task-based with registry pattern  
**Files:** This folder contains prototype implementations

- `base.py` - Abstract task class with standardized interface
- `content_tasks.py` - Content generation task implementations
- `business_tasks.py` - Business logic task implementations
- `automation_tasks.py` - Automation task implementations
- `utility_tasks.py` - Utility task implementations
- `registry.py` - Task factory and discovery system
- `social_tasks.py` - Social media task implementations

## Why This Exists But Isn't Used

During a refactoring phase, this Task framework was created as a potential way to:

1. **Standardize task execution** - Single interface for all task types
2. **Improve discoverability** - Task registry system for dynamic loading
3. **Enable chaining** - Pipeline tasks together via TaskExecutor
4. **Better separation of concerns** - Tasks as pure execution units

However, the current **Agent-based approach** works well and is production-stable, so the Task refactoring was deferred.

## When to Use This

**Consider reviving this framework when:**

- Need to chain multiple tasks in a single pipeline
- Want dynamic task discovery and routing
- Migration from Agent-based to Task-based execution is prioritized
- Need standardized task lifecycle (init → execute → cleanup)

## What Would Need to Happen

To fully adopt the Task framework:

1. **Implement `_execute_internal()`** for each Task subclass
   - Currently stubbed/prototyped
   - Must integrate with agents or implement business logic directly

2. **Update UnifiedOrchestrator** to use task registry

   ```python
   # Instead of:
   research_agent = ResearchAgent()
   data = await research_agent.run(topic)
   
   # Would be:
   research_task = task_registry.create("research", topic=topic)
   data = await research_task.execute()
   ```

3. **Update TaskExecutor** to route through task registry

   ```python
   task = task_registry.create(task_type, **params)
   result = await task.execute()
   ```

4. **Wire task results** through unified response model

5. **Add task result persistence** to database

6. **Test thoroughly** against current Agent-based pipeline

## Verification

Confirmed not used (as of Feb 10, 2026):

- No imports of ResearchTask, CreativeTask, QATask, PublishingTask in active code
- Task registry not referenced by orchestrator or routes
- All content generation flows through agents, not tasks

## References

- **Current Architecture:** [docs/02-ARCHITECTURE_AND_DESIGN.md](../../../docs/02-ARCHITECTURE_AND_DESIGN.md)
- **Unified Orchestrator:** [services/unified_orchestrator.py](../services/unified_orchestrator.py)
- **Agents:** [agents/content_agent/agents/](../agents/content_agent/agents/)
- **Task Executor:** [services/task_executor.py](../services/task_executor.py)

---

**Decision Date:** February 10, 2026  
**Decision:** Preserve for future work, not currently active  
**Last Review:** Codebase cleanup phase 4
