# Phase 1 Task System - Quick Reference

## 16 Tasks Completed ✅

### Import Everything

```python
from src.cofounder_agent.tasks import (
    # Base
    Task, PureTask, TaskStatus, TaskResult, ExecutionContext, TaskRegistry,
    # Content (5)
    ResearchTask, CreativeTask, QATask, ImageSelectionTask, PublishTask,
    # Social (4)
    SocialResearchTask, SocialCreativeTask, SocialImageFormatTask, SocialPublishTask,
    # Business (3)
    FinancialAnalysisTask, MarketAnalysisTask, PerformanceReviewTask,
    # Automation (2)
    EmailGenerateTask, EmailSendTask,
    # Utility (6)
    ValidateTask, TransformTask, NotificationTask, CacheTask, MetricsTask, LogTask,
)
```

## Task Categories

### Content Generation (5 tasks)

Blog post generation pipeline with quality control

| Task               | Purpose          | Key Input                | Output                      |
| ------------------ | ---------------- | ------------------------ | --------------------------- |
| ResearchTask       | Gather research  | `topic`                  | `research_data`             |
| CreativeTask       | Generate content | `topic`, `research_data` | `content` (markdown)        |
| QATask             | Evaluate quality | `content`                | `quality_score`, `feedback` |
| ImageSelectionTask | Find images      | `topic`, `content`       | `images` list               |
| PublishTask        | Store in CMS     | `content`, `title`       | `published_url`             |

**Default Pipeline:** `["research", "creative", "qa", "image_selection", "publish"]`

### Social Media (4 tasks)

Multi-platform content distribution with platform-specific optimization

| Task                  | Purpose         | Key Input                 | Output                        |
| --------------------- | --------------- | ------------------------- | ----------------------------- |
| SocialResearchTask    | Analyze trends  | `topic`, `platforms`      | `hashtags`, `sentiment`       |
| SocialCreativeTask    | Generate posts  | `topic`, `platform`       | `social_post` (char-limited)  |
| SocialImageFormatTask | Optimize images | `platform`, `images`      | `formatted_images` with specs |
| SocialPublishTask     | Publish posts   | `platform`, `social_post` | `published` (bool), `post_id` |

**Supported Platforms:** Twitter (280 chars), LinkedIn (2000 chars), Instagram (2200 chars), TikTok (2500 chars)

**Default Pipeline:** `["social_research", "social_creative", "social_image_format", "social_publish"]`

### Business Intelligence (3 tasks)

Analytics and financial metrics

| Task                  | Purpose        | Key Input       | Output                         |
| --------------------- | -------------- | --------------- | ------------------------------ |
| FinancialAnalysisTask | Calculate ROI  | `workflow_type` | `total_cost`, `roi_percentage` |
| MarketAnalysisTask    | Analyze market | `topic`         | `market_size`, `trends`        |
| PerformanceReviewTask | Review metrics | `period`        | `summary`, `insights`          |

### Automation (2 tasks)

Email campaigns and automated notifications

| Task              | Purpose      | Key Input                       | Output                       |
| ----------------- | ------------ | ------------------------------- | ---------------------------- |
| EmailGenerateTask | Create email | `topic`                         | `subject`, `body` (HTML)     |
| EmailSendTask     | Send email   | `subject`, `body`, `recipients` | `sent` (bool), `campaign_id` |

### Utility (6 tasks)

Supporting utilities for workflow management

| Task             | Purpose         | Use Case                   |
| ---------------- | --------------- | -------------------------- |
| ValidateTask     | Check content   | Before approval/publishing |
| TransformTask    | Convert formats | markdown ↔ json ↔ html   |
| NotificationTask | Send alerts     | Keep user informed         |
| CacheTask        | Store results   | Improve performance        |
| MetricsTask      | Record metrics  | Track performance          |
| LogTask          | Log events      | Debugging and audit        |

## Quick Examples

### Example 1: Execute Single Task

```python
from src.cofounder_agent.tasks import ResearchTask, ExecutionContext

# Create task
task = ResearchTask()

# Create context
context = ExecutionContext(
    workflow_id="wf-123",
    user_id="user-456",
    workflow_type="content_generation"
)

# Execute
result = await task.execute(
    input_data={"topic": "AI Trends"},
    context=context
)

# Check result
if result.status == TaskStatus.COMPLETED:
    print(result.output["research_data"])
```

### Example 2: Use Registry to Validate Pipeline

```python
from src.cofounder_agent.tasks import TaskRegistry

# Get registry (singleton)
registry = TaskRegistry()

# Validate pipeline
pipeline = ["research", "creative", "qa", "publish"]
is_valid, error = registry.validate_pipeline(pipeline)

if is_valid:
    print("Pipeline is valid!")
else:
    print(f"Pipeline error: {error}")

# Get default pipeline
default = registry.get_default_pipeline("content_generation")
print(default)  # ["research", "creative", "qa", "image_selection", "publish"]
```

### Example 3: Access Task Output in Next Task

```python
# In any task's _execute_internal():
async def _execute_internal(self, input_data, context):
    # Get output from previous task
    prev_result = context.get_task_result("research")
    if prev_result:
        research_data = prev_result.output["research_data"]

    # Use in current task
    prompt = f"Create content from: {research_data}"
    response = await model_router.query_with_fallback(prompt)

    # Return output (will be available to next task)
    return {"content": response}
```

### Example 4: Complete Content Generation Pipeline

```python
from src.cofounder_agent.tasks import (
    ResearchTask, CreativeTask, QATask, ImageSelectionTask, PublishTask,
    ExecutionContext, TaskRegistry
)

# Create context
context = ExecutionContext(
    workflow_id="wf-123",
    user_id="user-456",
    workflow_type="content_generation"
)

# Tasks
research = ResearchTask()
creative = CreativeTask()
qa = QATask()
images = ImageSelectionTask()
publish = PublishTask()

# Execute pipeline (manually - Phase 2 will automate this)
result1 = await research.execute({"topic": "AI"}, context)
result2 = await creative.execute({"topic": "AI", "research_data": result1.output["research_data"]}, context)
result3 = await qa.execute({"content": result2.output["content"]}, context)
result4 = await images.execute({"topic": "AI", "content": result2.output["content"]}, context)
result5 = await publish.execute({
    "content": result2.output["content"],
    "title": result2.output["title"],
    "images": result4.output["images"]
}, context)

print(f"Published: {result5.output['published_url']}")
```

## Phase 1 Stats

| Category     | Tasks    | Lines      | Status          |
| ------------ | -------- | ---------- | --------------- |
| Base Classes | 6        | 700        | ✅ Complete     |
| Content      | 5        | 380        | ✅ Complete     |
| Social       | 4        | 320        | ✅ Complete     |
| Business     | 3        | 300        | ✅ Complete     |
| Automation   | 2        | 240        | ✅ Complete     |
| Utility      | 6        | 280        | ✅ Complete     |
| **TOTAL**    | **16+6** | **~1,950** | **✅ COMPLETE** |

## File Structure

```
src/cofounder_agent/tasks/
├── __init__.py              # Exports all 16 tasks
├── base.py                  # Task, PureTask, ExecutionContext, etc.
├── registry.py              # TaskRegistry singleton
├── content_tasks.py         # 5 content tasks
├── social_tasks.py          # 4 social tasks
├── business_tasks.py        # 3 business tasks
├── automation_tasks.py      # 2 automation tasks
└── utility_tasks.py         # 6 utility tasks
```

## Next Phases

### Phase 2: Pipeline Execution (2-3 weeks)

- [ ] ModularPipelineExecutor - Auto-chain tasks
- [ ] WorkflowRequest schema - Single input format
- [ ] WorkflowResponse schema - Consistent output

### Phase 3: Unified Router (2-3 weeks)

- [ ] UnifiedWorkflowRouter - /api/workflow/execute endpoint
- [ ] NLP intent recognition - Convert "generate blog post" to task pipeline
- [ ] Chat and form integration

### Phase 4: Approval Workflows (3-4 weeks)

- [ ] ApprovalGateTask checkpoint/resume
- [ ] Oversight Hub approval interface
- [ ] Email notifications

### Phase 5: Integration & Testing (2-3 weeks)

- [ ] Wire into existing interfaces
- [ ] Comprehensive testing
- [ ] Performance optimization

### Phase 6: Cleanup (1 week)

- [ ] Delete deprecated orchestrators
- [ ] Verify full system
- [ ] Production deployment

## Task Status Reference

```python
# Import TaskStatus enum
from src.cofounder_agent.tasks import TaskStatus

TaskStatus.PENDING        # Not yet started
TaskStatus.RUNNING        # Currently executing
TaskStatus.COMPLETED      # Finished successfully
TaskStatus.FAILED         # Execution failed (error captured)
TaskStatus.SKIPPED        # Task was skipped (conditional)
TaskStatus.AWAITING_INPUT # Paused for user approval (ApprovalGateTask)
```

## Error Handling

All tasks use automatic error handling via `PureTask`:

```python
class AnyTask(PureTask):
    async def _execute_internal(self, input_data, context):
        # If this raises an exception, it's automatically caught
        # and wrapped in TaskResult with status=FAILED
        dangerous_operation()
        return {"result": "ok"}

# No exceptions escape - all errors in TaskResult.error
result = await task.execute(data, context)
if result.status == TaskStatus.FAILED:
    print(f"Error: {result.error}")  # Error message captured
```

## LLM Integration

All content tasks use model_router with automatic fallback:

```
Ollama (free, local)
  ↓ (if unavailable)
Claude 3 Opus (best quality)
  ↓ (if error)
GPT-4 (fast)
  ↓ (if error)
Gemini Pro (low cost)
```

No LLM failures will crash pipeline - degraded mode always available.

## Performance Estimates

| Task              | Duration | Notes              |
| ----------------- | -------- | ------------------ |
| Research          | 30-60s   | LLM call           |
| Creative          | 60-120s  | Content generation |
| QA                | 30-60s   | Quality evaluation |
| ImageSelection    | 20-40s   | Image search       |
| Publish           | 5-10s    | Database insert    |
| SocialResearch    | 30-60s   | Trend analysis     |
| SocialCreative    | 20-40s   | Per platform       |
| FinancialAnalysis | 30-60s   | Calculation        |
| MarketAnalysis    | 60-120s  | Research           |

**Full Pipelines:**

- Content generation: ~5-8 minutes
- Social campaign: ~2-4 minutes
- Financial analysis: ~1-2 minutes

## Key Concepts

**ExecutionContext** - Carries state through entire pipeline

- `workflow_id`: Unique ID for this workflow
- `user_id`: Who is running this
- `task_history`: All completed tasks
- `workflow_data`: Accumulated data from all tasks

**TaskResult** - Complete execution telemetry

- `status`: Did it succeed?
- `output`: Task results
- `error`: Error message if failed
- `duration_seconds`: How long it took

**TaskRegistry** - Central task management

- `register()`: Add a task
- `get()`: Retrieve a task
- `validate_pipeline()`: Check all tasks exist
- `get_default_pipeline()`: Standard pipeline for workflow type

**LLM Integration Pattern** - Used by all content tasks

- Input validation
- LLM call with model_router
- JSON parsing with fallback to string
- Error handling with retry

---

**Status:** Phase 1 ✅ COMPLETE  
**Next Action:** Review tasks, then proceed to Phase 2 (Pipeline Executor)
