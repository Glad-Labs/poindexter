# PerformanceMonitor - Consolidation Opportunity

**Status:** ⏸️ **POTENTIALLY UNUSED** - May duplicate telemetry.py functionality

**Location:** [performance_monitor.py](./performance_monitor.py) (461 lines)

## Overview

PerformanceMonitor provides comprehensive system and application metrics:

**Metrics Tracked:**

- Agent execution times (research, creative, QA, etc.)
- Database query performance
- LLM API response times (latency)
- Memory usage per agent
- Cost per operation
- Error rates and retry logic
- Queue depths and SLA compliance

## Current Functionality

**Main Metrics:**

- `track_agent_execution(agent_name, duration, tokens_used)`
- `track_db_query(query_time)`
- `track_api_call(provider, model, response_time, tokens)`
- `get_metrics(metric_type, time_range)`
- `calculate_sla_compliance()`

**Current Status:**

- Defined as comprehensive service (461 lines)
- No active imports found in routes or services
- Appears to be legacy monitoring
- Alternative: [telemetry.py](./telemetry.py) - May provide same functionality

## Issue: Potential Duplication with telemetry.py

### PerformanceMonitor approach

- Agent-centric metrics (per agent)
- Database query timing
- LLM API endpoint tracking
- Manual metric collection

### Telemetry approach

- System-level metrics
- Automatic instrumentation
- OpenTelemetry standard
- Integration with monitoring backends

**Current Overlap:**

| Metric | PerformanceMonitor | Telemetry | Status |
|--------|-------------------|-----------|--------|
| Agent execution time | ✓ | ✓ | DUPLICATE |
| API call latency | ✓ | ? | DUPLICATE |
| Database query timing | ✓ | ? | DUPLICATE |
| Error tracking | ✓ | ✓ | DUPLICATE |
| Cost calculation | ✓ | ✗ | Unique to PerformanceMonitor |

## Consolidation Path

### Option A: Merge into telemetry.py (Recommended)

**Effort:** 3-4 hours

Consolidate PerformanceMonitor metrics into unified telemetry system:

```python
# Current (manual):
perf_monitor.track_agent_execution("research", 5.2, 2000)

# Proposed (automatic):
@telemetry.instrument_function("research_agent")
async def run(self):
    # Telemetry auto-tracks time, errors, etc.
    ...
```

**Benefits:**

- Single source of truth for metrics
- Automatic instrumentation (less code)
- OpenTelemetry standards compliance
- Better integration with monitoring tools
- Cost tracking integrated

**Implementation:**

1. Review all metrics collected by PerformanceMonitor
2. Add custom metrics to telemetry.py for agent execution
3. Add custom metrics for cost tracking
4. Migrate all metric calls to telemetry decorators
5. Delete PerformanceMonitor
6. Update test suite

### Option B: Keep as Custom Metrics Wrapper (If Needed)

**Keep if:**

- telemetry.py doesn't support agent-level metrics
- Custom SLA calculations needed
- Specialized cost tracking not in telemetry

**Document usage:**

- Clarify when to use PerformanceMonitor vs telemetry
- Add integration points

## Cost Tracking: Unique Feature

**PerformanceMonitor provides:**

```python
cost_per_request = tokens_used * cost_per_token[model]
total_daily_cost = sum(costs)
cost_by_agent = costs.groupby(agent_name)
```

**This is NOT currently in telemetry.py.**

**Recommendation:** Extract cost tracking as separate concern:

- Keep in unified_orchestrator for real-time calculation
- Export to metrics_routes for dashboard
- Remove from PerformanceMonitor

## Decision Points

**Questions to answer:**

1. ✅ Is PerformanceMonitor used?
   - Grep result: No active imports in routes/services
   - Status: Likely dead code

2. ✅ Does telemetry.py provide equivalent functionality?
   - Review telemetry.py capabilities
   - Compare feature matrix above

3. ✅ Where should cost tracking live?
   - Currently: PerformanceMonitor
   - Proposed: Integrate into task_executor or metrics_routes

## Recommendation

**Consolidate into telemetry.py in next sprint.**

**Prerequisite:**

- Review [telemetry.py](./telemetry.py) and confirm it covers PerformanceMonitor metrics
- Determine cost tracking strategy

**Suggested Task:**

- Title: "Consolidate PerformanceMonitor metrics into OpenTelemetry"
- Effort: 3-4 hours
- Depends on: telemetry.py review
- Blocks: Cost tracking refactor

## References

- [telemetry.py](./telemetry.py) - Unified telemetry service
- [metrics_routes.py](../routes/metrics_routes.py) - Metrics API
- [unified_orchestrator.py](./unified_orchestrator.py) - Orchestrator (where metrics originate)
- [task_executor.py](./task_executor.py) - Task execution (collects metrics)

---

**Decision Date:** February 10, 2026

**Status:** Consolidation candidate for next sprint

**Maintenance:** Not actively maintained, potentially superseded by telemetry

**Last Review:** Codebase cleanup phase 4
