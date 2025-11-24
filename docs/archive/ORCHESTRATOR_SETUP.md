# Intelligent Orchestrator Setup Guide

Complete integration guide for the advanced IntelligentOrchestrator system.

## Overview

The IntelligentOrchestrator is a sophisticated multi-agent coordination system that:

- Parses natural language business requests
- Discovers available tools via MCP (Model Context Protocol)
- Designs optimal execution workflows
- Executes in parallel where possible
- Implements quality feedback loops with automatic retry
- Learns from every execution (persistent memory)
- Accumulates training data for proprietary LLM fine-tuning
- Correlates workflows with business metrics
- Supports human-in-the-loop approval workflows

## Architecture Overview

```
User (Oversight Hub)
    ↓ Natural Language Request

Intelligent Orchestrator
  1. Planning Phase → Parse intent, design workflow
  2. Tool Discovery → Find available tools via MCP
  3. Execution Phase → Run workflow steps (parallel execution)
  4. Quality Check → Assess result quality across dimensions
  5. Refinement Phase → Retry on quality failures
  6. Formatting Phase → Prepare for user approval
  7. Learning Phase → Store patterns & training data

Result
  ↓ Ready for User Approval
```

## Created Files

1. **services/intelligent_orchestrator.py** (900+ lines)
   - Core `IntelligentOrchestrator` class
   - Workflow planning and execution
   - Quality assessment and refinement
   - Training data generation

2. **services/orchestrator_memory_extensions.py** (300+ lines)
   - `EnhancedMemorySystem` wrapper
   - Pattern learning and accumulation
   - Business metrics correlation
   - Learned pattern export

3. **routes/intelligent_orchestrator_routes.py** (540+ lines)
   - REST API endpoints
   - Task management and approval workflows
   - Training data export/import
   - Learning patterns and metrics access

## Quick Start Integration

### Step 1: Import Required Classes

```python
from services.intelligent_orchestrator import IntelligentOrchestrator, ToolSpecification
from services.orchestrator_memory_extensions import EnhancedMemorySystem
from mcp_integration import MCPEnhancedCoFounder
```

### Step 2: Initialize in main.py Lifespan

```python
# In your FastAPI lifespan setup
intelligent_orchestrator = IntelligentOrchestrator(
    llm_client=your_llm_client,
    database_service=database_service,
    memory_system=enhanced_memory,
    mcp_orchestrator=mcp_orchestrator
)

# Attach to main orchestrator for access
orchestrator.intelligent_orchestrator = intelligent_orchestrator
```

### Step 3: Register Routes

```python
from routes.intelligent_orchestrator_routes import router as intelligent_orchestrator_router
app.include_router(intelligent_orchestrator_router)
```

### Step 4: Register Tools

```python
orchestrator.register_tool(ToolSpecification(
    tool_id="research_agent",
    name="Research & Fact Checking",
    description="Gather information, verify facts, find sources",
    category="research",
    input_schema={...},
    output_schema={...},
    estimated_cost=0.50,
    estimated_duration=120,
    success_rate=0.95,
    requires_approval=False,
    source="builtin"
))
```

## API Endpoints

### Process Request

```
POST /api/orchestrator/process
{
  "request": "Generate blog post about AI trends",
  "business_metrics": {
    "revenue_monthly": 50000,
    "traffic_monthly": 25000,
    "conversion_rate": 0.04
  },
  "preferences": {
    "tone": "professional",
    "channels": ["blog", "linkedin"]
  }
}
```

Returns: `task_id` for polling

### Check Status

```
GET /api/orchestrator/status/{task_id}
```

### View for Approval

```
GET /api/orchestrator/approval/{task_id}
```

### Approve & Publish

```
POST /api/orchestrator/approve/{task_id}
{
  "approved": true,
  "publish_to_channels": ["blog", "linkedin"]
}
```

### Get Learned Patterns

```
GET /api/orchestrator/learning-patterns
```

### Analyze Business Metrics

```
GET /api/orchestrator/business-metrics-analysis
```

### Export Training Data

```
POST /api/orchestrator/training-data/export
{
  "format": "jsonl",
  "filter_by_quality": 0.8,
  "limit": 10000
}
```

### Upload Custom LLM

```
POST /api/orchestrator/training-data/upload-model
{
  "model_file": "/path/to/model",
  "model_name": "custom-orchestrator-v1",
  "enable_immediately": false
}
```

## Key Features

### 1. Natural Language Understanding

The orchestrator parses requests to extract:

- Core intent (what user actually wants)
- Specific requirements
- Business context
- User preferences

### 2. MCP Tool Discovery

Tools discovered from:

- Built-in registry
- MCP servers (dynamic)
- External APIs (wrapped)

### 3. Quality Feedback Loops

Automatic refinement if quality < threshold:

- Identifies problematic steps
- Re-executes with corrective feedback
- Retries up to N times
- Escalates to human if still failing

### 4. Persistent Learning

Every execution contributes to learning:

- Workflow patterns (which sequences work)
- Success correlations (what drives results)
- Quality issues (what to avoid)
- User preferences (evolving over time)

### 5. Training Data Generation

Automatically exports structured data for fine-tuning:

- Request and reasoning trace
- Executed workflow
- Quality assessment
- Business metrics before/after
- Outcome labels

### 6. Proprietary LLM Support

After accumulating data:

- Fine-tune custom orchestrator LLM
- Deploy gradually with A/B testing
- Monitor quality improvements
- Your org's unique AI assistant

## Tool Definition Schema

Each tool needs:

```python
ToolSpecification(
    tool_id="unique_id",                    # Identifier
    name="Display Name",                    # For UI
    description="What it does",              # Purpose
    category="research|content|qa|etc",     # Category
    input_schema={...},                      # JSON Schema for inputs
    output_schema={...},                     # JSON Schema for outputs
    estimated_cost=0.50,                     # USD cost estimate
    estimated_duration=120,                  # Seconds
    success_rate=0.95,                       # Historical success rate
    requires_approval=False,                 # Need human sign-off?
    source="builtin|mcp|api"               # Where tool comes from
)
```

## Execution Flow

1. **Planning** - LLM designs workflow based on request
2. **Tool Discovery** - Find tools needed for workflow via MCP
3. **Execution** - Run workflow steps (parallel where possible)
4. **Quality Check** - LLM assesses quality across dimensions
5. **Refinement** - If needed, re-execute problematic steps
6. **Formatting** - Prepare results for user
7. **Learning** - Store patterns and training data
8. **Approval** - Wait for human decision to publish

## Monitoring

Track these metrics:

- Success rate: `(completed / total) * 100`
- Quality score: Average quality assessment
- Cost efficiency: `cost / successful_tasks`
- Pattern adoption: `(using_learned_workflows / total) * 100`
- Custom LLM improvement: Quality delta with custom model

## Best Practices

1. **Start with core tools** - Don't register everything at once
2. **Monitor quality thresholds** - Adjust based on your needs
3. **Review learned patterns** - Weekly or when adding new tools
4. **Collect feedback** - User input improves model over time
5. **Cost management** - Use cheaper models for simple tasks
6. **Transparency** - Show reasoning to build trust
7. **Incremental rollout** - Test with custom LLM before full deployment

## Troubleshooting

### IntelligentOrchestrator Not Initialized

Check that:

- LLM client is available
- Memory system initialized (if using)
- MCP orchestrator optional but helpful
- Routes registered in FastAPI

### Quality Checks Failing

- Increase max_retries for steps
- Lower quality_threshold if too strict
- Review failed execution traces
- Improve tool specifications

### Training Data Export Empty

- Ensure sufficient executions have occurred
- Check quality filters (maybe too strict)
- Verify database connectivity
- Review training data schema

## Next Steps

1. ✅ Review `intelligent_orchestrator.py`
2. ✅ Review `orchestrator_memory_extensions.py`
3. ✅ Review `intelligent_orchestrator_routes.py`
4. ✅ Integrate into main.py
5. ✅ Register tools
6. ✅ Test with sample requests
7. ✅ Build React UI component
8. ✅ Collect training data
9. ✅ Fine-tune proprietary LLM
10. ✅ Deploy improvements

Your organization's unique AI co-founder is ready! 🚀
