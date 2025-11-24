# Quick Reference - Intelligent Orchestrator

## File Locations

```
src/cofounder_agent/
├── services/
│   ├── intelligent_orchestrator.py          # Main engine (900+ lines)
│   └── orchestrator_memory_extensions.py    # Learning system (300+ lines)
├── routes/
│   └── intelligent_orchestrator_routes.py   # REST endpoints (540+ lines)
├── ORCHESTRATOR_SETUP.md                    # Integration guide
└── IMPLEMENTATION_SUMMARY.md                # This implementation
```

## Key Classes

### IntelligentOrchestrator

Main orchestration engine:

```python
class IntelligentOrchestrator:
    async def process_request(request, user_id, business_metrics)
    async def _create_execution_plan(request, business_metrics, preferences)
    async def _discover_tools(plan) -> Dict[str, ToolSpecification]
    async def _execute_workflow(plan, tools) -> Dict[str, Any]
    async def _assess_quality(outputs, plan) -> QualityAssessment
    async def _refine_results(outputs, quality, plan) -> Tuple[int, Dict]
    async def _format_for_approval(outputs, plan, preferences)
    async def _accumulate_learning(request_id, request, plan, outputs, quality)

    def register_tool(tool: ToolSpecification)
    def deregister_tool(tool_id: str)
    def list_tools() -> List[ToolSpecification]

    def set_custom_orchestrator_llm(llm_client)
    def disable_custom_orchestrator_llm()
    def get_training_dataset() -> List[TrainingExample]
    def export_training_dataset(format: str) -> str
```

### EnhancedMemorySystem

Learning system:

```python
class EnhancedMemorySystem:
    async def record_execution(request, workflow_steps, quality, business_metrics, outcome)
    async def _update_workflow_patterns(workflow_steps, quality, outcome)

    def get_workflow_patterns(min_frequency, min_success_rate) -> List[ExecutionPattern]
    async def correlate_with_business_metrics() -> Dict[str, Any]
    async def get_recommended_workflow(request) -> Optional[List[str]]

    def export_learned_patterns(format: str) -> str
    def get_quality_correlations() -> Dict[str, Any]
```

## Data Structures

### ExecutionPlan

Blueprint for execution:

```python
@dataclass
class ExecutionPlan:
    plan_id: str
    user_request: str
    intent: str
    requirements: List[str]
    workflow_steps: List[WorkflowStep]
    workflow_source: WorkflowSource  # user_request, learned_pattern, hybrid
    estimated_duration: float  # seconds
    estimated_cost: float  # USD
    priority: str  # low, medium, high, critical
    business_metrics: Dict[str, Any]
```

### WorkflowStep

Individual execution step:

```python
@dataclass
class WorkflowStep:
    step_id: str
    tool_id: str
    description: str
    input_data: Dict[str, Any]
    dependencies: List[str]  # prerequisite step IDs
    retry_count: int
    max_retries: int
    quality_threshold: float  # 0-1
    timeout_seconds: int
    result: Optional[Dict[str, Any]]
    error: Optional[str]
```

### ToolSpecification

Tool registration schema:

```python
@dataclass
class ToolSpecification:
    tool_id: str
    name: str
    description: str
    category: str  # research, content, qa, publishing, seo, etc
    input_schema: Dict[str, Any]  # JSON schema
    output_schema: Dict[str, Any]
    estimated_cost: float  # USD
    estimated_duration: float  # seconds
    success_rate: float  # 0-1
    requires_approval: bool
    source: str  # builtin, mcp, api
    metadata: Dict[str, Any]
```

### ExecutionResult

Complete execution outcome:

```python
@dataclass
class ExecutionResult:
    result_id: str
    plan_id: str
    request_id: str
    status: DecisionOutcome  # success, partial_success, failure, etc
    outputs: Dict[str, Any]  # step_id → result
    quality_assessment: QualityAssessment
    total_cost: float
    total_duration: float
    refinement_attempts: int
    final_formatting: Optional[Dict[str, Any]]
    execution_trace: List[Dict[str, Any]]
```

### QualityAssessment

Quality metrics:

```python
@dataclass
class QualityAssessment:
    score: float  # 0-1
    passed: bool
    issues: List[str]
    suggestions: List[str]
    dimension_scores: Dict[str, float]  # accuracy, completeness, coherence, etc
    retry_needed: bool
```

### TrainingExample

Training data for fine-tuning:

```python
@dataclass
class TrainingExample:
    example_id: str
    request: str
    reasoning_trace: str  # How orchestrator thought through it
    executed_plan: ExecutionPlan
    result: ExecutionResult
    business_metrics_before: Dict[str, Any]
    business_metrics_after: Dict[str, Any]
    improvement: float  # -1 to 1
    feedback_label: str  # excellent, good, acceptable, poor
```

## REST Endpoints

### Process Request

```
POST /api/orchestrator/process
Input:  ProcessRequestBody
Output: {"task_id": "task-xxx", "status_url": "...", "approval_url": "..."}
Background: Runs full 7-phase orchestration
```

### Check Status

```
GET /api/orchestrator/status/{task_id}
Output: ExecutionStatusResponse
        {task_id, status, progress_percentage, current_phase, error}
```

### View for Approval

```
GET /api/orchestrator/approval/{task_id}
Output: ApprovalResponse
        {task_id, quality_score, main_content, channel_variants, metadata}
```

### Approve & Publish

```
POST /api/orchestrator/approve/{task_id}
Input:  ApprovalAction {approved, publish_to_channels, feedback, modifications}
Output: {"status": "approved_and_publishing" | "rejected"}
```

### Get Execution History

```
GET /api/orchestrator/history?user_id=xxx&limit=50&status_filter=xxx
Output: List of recent executions for user
```

### Export Training Data

```
POST /api/orchestrator/training-data/export
Input:  TrainingDataExportRequest {format, filter_by_quality, limit}
Output: {"format": "jsonl", "example_count": 5000, "data": "..."}
```

### Upload Custom LLM

```
POST /api/orchestrator/training-data/upload-model
Input:  {model_file, model_name, enable_immediately}
Output: {"status": "loaded", "enabled": bool}
```

### Get Learning Patterns

```
GET /api/orchestrator/learning-patterns
Output: {patterns_markdown, pattern_count}
```

### Analyze Business Metrics

```
GET /api/orchestrator/business-metrics-analysis
Output: {correlations, insight_count}
```

### List Tools

```
GET /api/orchestrator/tools
Output: List of available tools with specs
```

## Enumerations

### ExecutionPhase

```python
planning, tool_discovery, delegation, execution,
quality_check, refinement, formatting, approval, learning
```

### WorkflowSource

```python
user_request, learned_pattern, mcp_discovery, previous_success, hybrid
```

### DecisionOutcome

```python
success, partial_success, failure, cancelled, requires_human_intervention
```

### PatternType

```python
workflow, decision, user_preference, business_objective,
tool_combination, quality_issue
```

## Integration Quick Start

```python
# 1. Import
from services.intelligent_orchestrator import IntelligentOrchestrator, ToolSpecification
from services.orchestrator_memory_extensions import EnhancedMemorySystem
from routes.intelligent_orchestrator_routes import router

# 2. Initialize in main.py
orchestrator = IntelligentOrchestrator(
    llm_client=llm_client,
    database_service=database_service,
    memory_system=EnhancedMemorySystem(existing_memory),
    mcp_orchestrator=mcp_orch
)

# 3. Register in FastAPI
app.include_router(router)

# 4. Register tools
orchestrator.register_tool(ToolSpecification(...))

# 5. Use via API
POST /api/orchestrator/process
```

## Monitoring Metrics

- **Success Rate**: `(completed / total) * 100`
- **Quality Score**: Average assessment score
- **Cost Efficiency**: `cost / successful_tasks`
- **Pattern Adoption**: `(using_learned / total) * 100`
- **Custom LLM Improvement**: Quality delta with custom model
- **Average Duration**: Total time / task count
- **Tool Success Rate**: Per-tool reliability tracking

## Configuration Options

### Quality Thresholds

```python
step.quality_threshold = 0.75  # Pass if >= this score
plan.quality_threshold = 0.80  # Overall requirement
```

### Retry Settings

```python
step.max_retries = 3           # Attempts on failure
refinement_max = 2             # Quality improvement attempts
exponential_backoff = True     # Wait between retries
```

### Learning Settings

```python
min_pattern_frequency = 2      # Track patterns after N executions
min_success_rate = 0.70        # Include patterns with >70% success
confidence_threshold = 0.75    # Use patterns with >75% confidence
```

## Tool Categories

- **research** - Information gathering, fact-checking
- **content** - Writing, generation, creation
- **qa** - Quality assessment, critique
- **compliance** - Legal, regulatory, brand checks
- **image** - Visual asset selection, generation
- **publishing** - Format for channels, distribution
- **seo** - Search optimization
- **analysis** - Data analysis, insights
- **planning** - Strategic planning, decision support
- **execution** - Task automation, implementation

## Performance Tips

1. **Parallel Execution**: Structure workflows with minimal dependencies
2. **Cost Optimization**: Use cheaper models for simple tasks
3. **Quality Tuning**: Adjust thresholds based on your needs
4. **Tool Selection**: Register only tools you actually use
5. **Memory Management**: Export old training data periodically
6. **Monitoring**: Track success rates and costs weekly

## Troubleshooting

**Task stuck in "processing"?**

- Check orchestrator logs for errors
- Verify LLM client is responding
- Check MCP connections if using

**Quality checks always failing?**

- Lower quality_threshold
- Review failed step outputs
- Check tool output_schema accuracy

**No learning patterns?**

- Need minimum N executions first
- Check pattern frequency threshold
- Verify memory system initialized

**Training data empty?**

- Ensure sufficient executions
- Check export quality filter
- Verify database connectivity

## Next Steps

1. Review the three main files
2. Read ORCHESTRATOR_SETUP.md
3. Update main.py
4. Register tools
5. Test with sample requests
6. Build React UI
7. Collect training data
8. Fine-tune proprietary LLM
9. Deploy improvements
10. Monitor & iterate

---

**Your intelligent orchestrator is ready to serve your organization!** 🚀
