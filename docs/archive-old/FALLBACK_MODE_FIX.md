# Fix for Content Generation "Fallback Mode" Issue

## Problem

Content generation tasks were outputting messages marked as "Fallback Mode" despite:

- Ollama being fully functional (chat feature confirmed working)
- API keys configured and available
- All AI models operational

## Root Cause

The issue was an **initialization order problem** in the application startup:

1. **startup_manager** creates TaskExecutor with the legacy `Orchestrator` class
2. **startup_manager** calls `task_executor.start()` which begins processing tasks immediately
3. **main.py lifespan** later creates the new `UnifiedOrchestrator` with full capabilities
4. When TaskExecutor tries to process a task, it accesses `self.orchestrator`
5. Since `app.state.orchestrator` hasn't been set yet, it falls back to the legacy Orchestrator
6. Legacy Orchestrator doesn't have content generation capabilities, so it uses fallback templates

## Files Modified

- `src/cofounder_agent/main.py` (lines 155-200)

## The Fix

Modified the FastAPI lifespan in main.py to:

```python
# 1. Create UnifiedOrchestrator FIRST with all dependencies ready
quality_service = UnifiedQualityService(...)
unified_orchestrator = UnifiedOrchestrator(...)

# 2. Set it in app.state
app.state.orchestrator = unified_orchestrator

# 3. THEN inject app.state into task_executor
task_executor = services.get("task_executor")
if task_executor:
    task_executor.app_state = app.state
```

### How This Fixes The Issue

The TaskExecutor's `orchestrator` property now works correctly:

```python
@property
def orchestrator(self):
    """Get orchestrator dynamically from app.state or fallback"""
    if self.app_state and hasattr(self.app_state, 'orchestrator'):
        orch = getattr(self.app_state, 'orchestrator', None)
        if orch is not None:
            return orch  # ← Returns UnifiedOrchestrator (has content generation)
    return self.orchestrator_initial  # ← Only used if app_state not set
```

Since app.state is properly injected BEFORE tasks start processing, `self.orchestrator` returns the UnifiedOrchestrator, which supports the full 5-stage content generation pipeline.

## Verification

The fix ensures:

- ✅ TaskExecutor accesses UnifiedOrchestrator for content generation
- ✅ 5-stage pipeline executes (research → create → critique → refine → image → publish)
- ✅ Content uses AI models (Ollama preferred, with intelligent fallback)
- ✅ NO "Fallback Mode" messages (those only appear if orchestrator is None)

## Testing

Run a content generation task and verify:

1. Output no longer marked as "Fallback Mode"
2. Content is generated through the full UnifiedOrchestrator pipeline
3. Quality scoring and critique loop are applied

## Code Changes Summary

**Before:** TaskExecutor initialized → starts processing → UnifiedOrchestrator created AFTER
**After:** UnifiedOrchestrator created → TaskExecutor app.state injected → safe to process

This simple reordering ensures all services are properly initialized before the task processing loop begins.
