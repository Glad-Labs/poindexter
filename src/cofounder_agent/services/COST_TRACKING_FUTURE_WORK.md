# Cost Tracking Services - Consolidation Opportunity

**Status:** ⏸️ **POTENTIALLY DUPLICATIVE** - Three services track similar metrics

**Location:**

- [cost_calculator.py](./cost_calculator.py)
- [cost_aggregation_service.py](./cost_aggregation_service.py)
- [usage_tracker.py](./usage_tracker.py)

## Overview

Three separate services handle cost and usage tracking with overlapping responsibilities:

### 1. cost_calculator.py

**Purpose:** Calculate per-request costs
**Main Methods:**

- `calculate_cost(tokens, model, operation)`
- `estimate_cost_for_task(task_params)`
- Uses `MODEL_COSTS` from model_router.py

**Integration:** Used by UnifiedOrchestrator

### 2. cost_aggregation_service.py

**Purpose:** Aggregate costs across multiple levels
**Main Methods:**

- `aggregate_by_task(task_id)`
- `aggregate_by_agent(agent_id)`
- `aggregate_by_user(user_id)`
- `aggregate_by_period(start_date, end_date)`

**Integration:** Used by metrics_routes.py for dashboard

### 3. usage_tracker.py

**Purpose:** Track API usage and metrics
**Main Methods:**

- `track_api_call(provider, endpoint, tokens)`
- `track_agent_execution(agent_name, duration)`
- `get_usage_stats(time_range)`
- `get_provider_breakdown()`

**Integration:** Unknown - appears minimal

## Current Overlaps

| Feature | cost_calculator | cost_aggregation | usage_tracker | Status |
| --- | --- | --- | --- | --- |
| Per-request cost calculation | ✓ | ✗ | ✗ | Centralized in cost_calculator |
| Aggregation by task | ✗ | ✓ | ✗ | Centralized in cost_aggregation |
| Aggregation by agent | ✗ | ✓ | ✓ | DUPLICATE |
| Aggregation by user | ✗ | ✓ | ✗ | Centralized in cost_aggregation |
| Time-based aggregation | ✗ | ✓ | ✓ | DUPLICATE |
| API provider breakdown | ✗ | ✓ | ✓ | DUPLICATE |
| Agent execution tracking | ✗ | ✗ | ✓ | Only in usage_tracker |

## Problem Statement

Three different services do similar things in different ways:

```python
# Option 1: cost_aggregation_service
costs = aggregation_service.aggregate_by_agent("research_agent", 
                                               start=today, 
                                               end=today)

# Option 2: usage_tracker
stats = usage_tracker.get_usage_stats(time_range="today")
breakdown = usage_tracker.get_provider_breakdown()

# Option 3: cost_calculator
cost = cost_calculator.calculate_cost(tokens=2000, 
                                      model="gpt-4", 
                                      operation="chat_completion")
```

**Issues:**

- Developers don't know which to use
- Different APIs for same functionality
- Potential for data inconsistency
- Maintenance burden (bug fixes in 3 places)
- Performance (3 separate queries)

## Consolidation Path

### Option A: Consolidate into single CostTrackingService (Recommended)

**Effort:** 4-5 hours

Create unified service with all three capabilities:

```python
class CostTrackingService:
    # Calculation layer
    def calculate_cost(tokens, model, operation) -> float:
        """Calculate per-request cost"""
        
    def estimate_cost(task_params) -> float:
        """Estimate task cost before execution"""
    
    # Aggregation layer
    def aggregate_by_agent(agent_id, filter=None) -> AggregatedCost:
        """Get costs by agent"""
        
    def aggregate_by_task(task_id) -> float:
        """Get cost of single task"""
        
    def aggregate_by_user(user_id, filter=None) -> AggregatedCost:
        """Get costs by user"""
        
    # Tracking layer
    def track_api_call(provider, endpoint, tokens, duration):
        """Log API call"""
        
    def track_agent_execution(agent_id, duration):
        """Log agent execution"""
    
    # Query layer
    def get_stats(filters) -> UsageStats:
        """Get comprehensive stats"""
```

**Table Structure (PostgreSQL):**

```sql
-- Unified cost tracking table
CREATE TABLE cost_tracking (
    id UUID PRIMARY KEY,
    timestamp TIMESTAMP,
    
    -- Operation details
    task_id UUID,
    agent_id UUID,
    user_id UUID,
    operation_type VARCHAR,
    
    -- Costs
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    model_name VARCHAR,
    cost_usd DECIMAL(10, 6),
    
    -- Metadata
    provider VARCHAR,
    endpoint VARCHAR,
    duration_ms INTEGER,
    status VARCHAR,
    
    INDEXES ON (task_id, agent_id, user_id, timestamp)
);
```

**Benefits:**

- Single source of truth for costs
- Unified API
- Easier to query and maintain
- Better performance (single table scan)
- Consistent data format
- Easier testing

**Implementation Steps:**

1. Create `CostTrackingService` class
2. Create PostgreSQL table + migration
3. Move logic from 3 services → unified service
4. Update all callers to use new service
5. Add comprehensive unit tests
6. Delete 3 old services
7. Update documentation

### Option B: Keep Separation with Clear Ownership (If Specialization Needed)

**Keep if:**

- Each service needs to optimize differently
- Different query patterns and performance needs
- Services are used in very different contexts

**Document ownership:**

- cost_calculator: Single request cost
- cost_aggregation: Business analytics
- usage_tracker: Operational monitoring

## Decision Points

**Questions to answer:**

1. Are these services actively used?
   - cost_calculator: Appears active (used by orchestrator)
   - cost_aggregation: Active (used by metrics_routes)
   - usage_tracker: Unclear (low usage)

2. Are there data synchronization issues?
   - Do they read/write same data?
   - Can they have inconsistent state?

3. What does the database schema look like?
   - Are there separate tables per service?
   - Is there a shared cost_tracking table?

4. What are current performance characteristics?
   - Are query patterns expensive?
   - Is there N+1 query problem?

## Recommendation

**Consolidate into single CostTrackingService in next sprint.**

**Prerequisite:**

1. Audit current database schema for cost tracking
2. Verify actual usage of all three services
3. Identify query patterns and performance requirements

**Suggested Task:**

- Title: "Consolidate cost tracking services into unified CostTrackingService"
- Effort: 4-5 hours
- Depends on: Database audit
- Blocks: Cost dashboard improvements (NEXT_SPRINT_IMPROVEMENTS section 1.3)

**Quick Audit Commands:**

```bash
# Find usage of each service
grep -r "cost_calculator\|cost_aggregation\|usage_tracker" \
  src/cofounder_agent --include="*.py" | grep -v "test"

# Check database migrations for cost tables
ls -la src/cofounder_agent/migrations/*cost* || echo "No cost migrations found"

# Review current implementations
wc -l src/cofounder_agent/services/cost_*.py usage_tracker.py
```

## References

- [cost_calculator.py](./cost_calculator.py) - Per-request cost calculation
- [cost_aggregation_service.py](./cost_aggregation_service.py) - Aggregated reporting
- [usage_tracker.py](./usage_tracker.py) - Operational metrics
- [metrics_routes.py](../routes/metrics_routes.py) - Analytics endpoint
- [model_router.py](./model_router.py) - Cost data source (MODEL_COSTS)
- [NEXT_SPRINT_IMPROVEMENTS.md](../../docs/NEXT_SPRINT_IMPROVEMENTS.md) - Cost dashboard plans

---

**Decision Date:** February 10, 2026

**Status:** Consolidation candidate for next sprint

**Maintenance:** cost_calculator and cost_aggregation appear active; usage_tracker unclear

**Action Item:** Conduct database audit before consolidation

**Last Review:** Codebase cleanup phase 4
