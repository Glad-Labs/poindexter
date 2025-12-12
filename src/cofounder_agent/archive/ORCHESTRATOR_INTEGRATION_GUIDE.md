"""
INTEGRATION GUIDE: Unified Orchestrator & Quality Service

Step-by-step guide for integrating the new consolidated services into main.py

Current Status:
✅ UnifiedOrchestrator created (services/unified_orchestrator.py)
✅ UnifiedQualityService created (services/quality_service.py)
⏳ Integration into main.py (this document guides the process)
⏳ Route updates to use new services

Files Affected:

- src/cofounder_agent/main.py (startup initialization)
- src/cofounder_agent/routes/\* (route files using the services)
  """

# ============================================================================

# PART 1: UPDATE main.py STARTUP

# ============================================================================

"""
In main.py, update the lifespan() function to initialize the new services.

Replace the old orchestrator initialization with:
"""

# OLD CODE IN main.py (in lifespan function):

# orchestrator = Orchestrator()

# intelligent_orchestrator = IntelligentOrchestrator()

# content_orchestrator = ContentOrchestrator()

# NEW CODE IN main.py (in lifespan function):

async def initialize_unified_services(app):
"""Initialize consolidated services"""

    # Get database service
    db_service = app.state.db_service
    model_router = app.state.model_router
    memory_system = getattr(app.state, 'memory_system', None)

    # Initialize quality service
    quality_service = UnifiedQualityService(
        model_router=model_router,
        database_service=db_service,
        qa_agent=None  # Optional QA agent if available
    )

    # Initialize content orchestrator (kept for compatibility)
    content_orchestrator = ContentOrchestrator(
        database_service=db_service,
        model_router=model_router,
        quality_service=quality_service
    )

    # Initialize unified orchestrator with all agents
    unified_orchestrator = UnifiedOrchestrator(
        database_service=db_service,
        model_router=model_router,
        quality_service=quality_service,
        memory_system=memory_system,
        # Inject all available agents
        content_orchestrator=content_orchestrator,
        financial_agent=getattr(app.state, 'financial_agent', None),
        compliance_agent=getattr(app.state, 'compliance_agent', None),
    )

    # Store in app state
    app.state.quality_service = quality_service
    app.state.unified_orchestrator = unified_orchestrator
    app.state.content_orchestrator = content_orchestrator  # Keep for backward compatibility

    logger.info("✅ Unified services initialized")
    return quality_service, unified_orchestrator

# In the lifespan function, add this after database initialization:

startup_manager = StartupManager(db_service, model_router)
await startup_manager.run()

# Add this new line:

quality_service, unified_orchestrator = await initialize_unified_services(app)

# ============================================================================

# PART 2: DEPENDENCY INJECTION IN ROUTES

# ============================================================================

"""
Update route files to access the unified services via dependency injection.

OLD PATTERN (in routes):

```python
@router.post("/content/create")
async def create_content(request: CreateContentRequest):
    # Get services
    if not db_service:
        raise HTTPException(status_code=500, detail="Services not initialized")
    # ... do work
```

NEW PATTERN (in routes):

```python
from fastapi import Depends

def get_unified_orchestrator(request: Request) -> UnifiedOrchestrator:
    return request.app.state.unified_orchestrator

def get_quality_service(request: Request) -> UnifiedQualityService:
    return request.app.state.quality_service

def get_database_service(request: Request) -> DatabaseService:
    return request.app.state.db_service

@router.post("/content/create")
async def create_content(
    request: CreateContentRequest,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
    quality_service: UnifiedQualityService = Depends(get_quality_service),
):
    # Services are now available as parameters
    # ... do work with orchestrator and quality_service
```

"""

# ============================================================================

# PART 3: ROUTE UPDATES - CONTENT ROUTES

# ============================================================================

"""
Update content_routes.py to use UnifiedOrchestrator:

OLD ENDPOINT (in content_routes.py):

```python
@router.post("/content/generate")
async def generate_content(request: GenerateRequest):
    # Call orchestrator and quality service
    # manually initialized
```

NEW ENDPOINT:

```python
from services.unified_orchestrator import UnifiedOrchestrator
from services.quality_service import UnifiedQualityService, EvaluationMethod

def get_unified_orchestrator(request: Request):
    return request.app.state.unified_orchestrator

def get_quality_service(request: Request):
    return request.app.state.quality_service

@router.post("/content/generate")
async def generate_content(
    request: GenerateRequest,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
    quality_service: UnifiedQualityService = Depends(get_quality_service),
):
    '''
    Generate content using unified orchestrator with natural language support
    '''
    try:
        # Process through unified orchestrator
        result = await orchestrator.process_request(
            user_input=request.prompt,
            context={
                "topic": request.topic,
                "keywords": request.keywords,
                "audience": request.audience,
                "content_type": request.content_type,
            }
        )

        # Quality service integration (if content was created)
        if result.get("task_id"):
            assessment = await quality_service.evaluate(
                content=result.get("output", ""),
                context={
                    "topic": request.topic,
                    "keywords": request.keywords,
                }
            )
            result["quality"] = assessment.to_dict()

        return result

    except Exception as e:
        logger.error(f"Content generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

"""

# ============================================================================

# PART 4: ROUTE UPDATES - TASK ROUTES

# ============================================================================

"""
Update task_routes.py to use UnifiedOrchestrator:

NEW NATURAL LANGUAGE TASK ENDPOINT:

```python
@router.post("/tasks/from-prompt")
async def create_task_from_prompt(
    prompt: str,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
    db_service: DatabaseService = Depends(get_database_service),
):
    '''
    Create a task from natural language prompt using unified orchestrator

    Supports:
    - "Create content about AI marketing"
    - "Research machine learning benefits"
    - "Check compliance for GDPR requirements"
    - etc.
    '''
    try:
        # Let unified orchestrator determine what to do
        result = await orchestrator.process_request(
            user_input=prompt,
            context={"auto_task_creation": True}
        )

        if result.get("status") == "completed" and result.get("task_id"):
            return {
                "success": True,
                "task_id": result["task_id"],
                "request_type": result.get("request_type"),
                "status": result.get("status"),
                "message": f"Task created: {result.get('task_id')}"
            }
        else:
            return {
                "success": False,
                "error": "Failed to create task",
                "details": result
            }
    except Exception as e:
        logger.error(f"Task creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

KEEP EXISTING ENDPOINT:
The manual task creation endpoint already exists and continues to work:

```python
@router.post("/tasks/create")
async def create_task(request: CreateTaskRequest):
    # This endpoint still works as-is
    # Supports manual task creation when user knows exactly what they want
```

"""

# ============================================================================

# PART 5: QUALITY SERVICE USAGE IN ROUTES

# ============================================================================

"""
Use UnifiedQualityService for quality assessment:

EVALUATE CONTENT:

```python
@router.post("/quality/evaluate")
async def evaluate_content(
    content: str,
    topic: str = "",
    quality_service: UnifiedQualityService = Depends(get_quality_service),
):
    '''
    Evaluate content quality using 7-criteria framework
    '''
    assessment = await quality_service.evaluate(
        content=content,
        context={"topic": topic},
        method=EvaluationMethod.PATTERN_BASED  # or HYBRID, LLM_BASED
    )

    return {
        "overall_score": assessment.overall_score,
        "passing": assessment.passing,
        "dimensions": assessment.dimensions.to_dict(),
        "feedback": assessment.feedback,
        "suggestions": assessment.suggestions,
        "evaluation_method": assessment.evaluation_method.value,
    }
```

GET STATISTICS:

```python
@router.get("/quality/stats")
async def get_quality_stats(
    quality_service: UnifiedQualityService = Depends(get_quality_service),
):
    '''
    Get quality service statistics
    '''
    return quality_service.get_statistics()
```

"""

# ============================================================================

# PART 6: SUBTASK ROUTES INTEGRATION

# ============================================================================

"""
Update subtask_routes.py to use UnifiedOrchestrator for subtasks:

EXISTING SUBTASK ENDPOINTS (keep, but enhance):

```python
@router.post("/content/research")
async def start_research(
    request: ResearchRequest,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
):
    '''
    Start research subtask (existing endpoint, enhanced with unified orchestrator)
    '''
    try:
        result = await orchestrator.process_request(
            user_input=f"Research about {request.topic}",
            context={
                "research_type": "academic",
                "keywords": request.keywords,
                "auto_task_creation": True,
            }
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/content/creative")
async def start_creative(
    request: CreativeRequest,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
):
    '''
    Start creative subtask using unified orchestrator
    '''
    result = await orchestrator.process_request(
        user_input=f"Create content about {request.topic}",
        context={"creative_style": request.style}
    )
    return result

# Similar updates for QA, images, format...
```

"""

# ============================================================================

# PART 7: BACKWARD COMPATIBILITY

# ============================================================================

"""
Maintain backward compatibility with existing code:

1. Keep old orchestrator imports and functions for now
   - They can coexist with new unified services
   - Gradually migrate routes one by one

2. Both work paths are valid:
   Path 1 (New): Use UnifiedOrchestrator for all requests
   Path 2 (Old): Use legacy orchestrators directly

3. Migration strategy:
   - Update high-priority routes first (content, task management)
   - Test each route thoroughly before moving to next
   - Deprecate old orchestrators once all routes migrated
   - Remove old orchestrator files in next major version

Example of supporting both:

```python
@router.post("/content/generate")
async def generate_content(request: Request):
    use_unified = request.query_params.get("unified", "true").lower() == "true"

    if use_unified:
        # New unified path
        orchestrator = request.app.state.unified_orchestrator
        result = await orchestrator.process_request(...)
    else:
        # Legacy path (for testing/fallback)
        orchestrator = request.app.state.content_orchestrator
        result = await orchestrator.run(...)

    return result
```

"""

# ============================================================================

# PART 8: TESTING THE INTEGRATION

# ============================================================================

"""
Test the unified orchestrator integration:

Test 1: Natural Language Content Creation

```bash
curl -X POST http://localhost:8000/content/generate \\
  -H "Content-Type: application/json" \\
  -d '{
    "prompt": "Create a blog post about AI marketing trends",
    "topic": "AI Marketing",
    "keywords": ["AI", "marketing", "trends"]
  }'
```

Expected response:

```json
{
  "request_id": "uuid",
  "status": "completed",
  "request_type": "content_creation",
  "task_id": "task-uuid",
  "output": "Generated content...",
  "quality": {
    "overall_score": 8.2,
    "passing": true,
    "dimensions": {...},
    "feedback": "Excellent content quality"
  }
}
```

Test 2: Quality Evaluation

```bash
curl -X POST http://localhost:8000/quality/evaluate \\
  -H "Content-Type: application/json" \\
  -d '{
    "content": "Your generated content here",
    "topic": "AI Marketing"
  }'
```

Test 3: Subtask via Unified Orchestrator

```bash
curl -X POST http://localhost:8000/content/research \\
  -H "Content-Type: application/json" \\
  -d '{
    "topic": "Machine Learning",
    "keywords": ["ML", "AI"]
  }'
```

Test 4: Statistics

```bash
curl http://localhost:8000/quality/stats
```

Expected:

```json
{
  "total_evaluations": 42,
  "passing_count": 38,
  "failing_count": 4,
  "pass_rate": 90.5,
  "average_score": 7.8
}
```

"""

# ============================================================================

# PART 9: MIGRATION CHECKLIST

# ============================================================================

"""
Checklist for completing the integration:

PHASE 1: Startup & Initialization
☐ Update main.py lifespan() to initialize UnifiedQualityService
☐ Update main.py lifespan() to initialize UnifiedOrchestrator
☐ Verify services are accessible in app.state
☐ Add Depends() functions for dependency injection
☐ Test app starts without errors

PHASE 2: Core Routes (High Priority)
☐ Update content_routes.py to use UnifiedOrchestrator
☐ Update task_routes.py to use UnifiedOrchestrator
☐ Add new POST /tasks/from-prompt endpoint
☐ Test both content and task creation endpoints
☐ Verify quality assessment integrates

PHASE 3: Subtask Routes
☐ Update subtask_routes.py (research, creative, QA, images, format)
☐ Test each subtask endpoint
☐ Verify natural language routing works

PHASE 4: Quality Service Integration
☐ Add GET /quality/evaluate endpoint
☐ Add GET /quality/stats endpoint
☐ Test quality assessment across different content
☐ Verify statistics tracking

PHASE 5: Testing & Validation
☐ Integration tests for unified orchestrator
☐ Test natural language understanding and routing
☐ Verify backward compatibility with existing code
☐ Load testing with multiple concurrent requests
☐ Database persistence verification

PHASE 6: Deprecation
☐ Mark old orchestrator classes as deprecated
☐ Document migration path for other developers
☐ Plan removal of old orchestrators in next major version
☐ Update documentation

TOTAL ESTIMATED TIME: 4-6 hours
"""

# ============================================================================

# PART 10: CODE SNIPPETS FOR COPY-PASTE

# ============================================================================

# Import statements for routes

import_snippet = """
from fastapi import Depends, HTTPException, Request
from services.unified_orchestrator import UnifiedOrchestrator
from services.quality_service import UnifiedQualityService, EvaluationMethod
from services.database_service import DatabaseService
"""

# Dependency injection setup for routes

dependency_snippet = """
def get_unified_orchestrator(request: Request) -> UnifiedOrchestrator:
orchestrator = getattr(request.app.state, 'unified_orchestrator', None)
if not orchestrator:
raise HTTPException(status_code=500, detail="Unified orchestrator not initialized")
return orchestrator

def get_quality_service(request: Request) -> UnifiedQualityService:
service = getattr(request.app.state, 'quality_service', None)
if not service:
raise HTTPException(status_code=500, detail="Quality service not initialized")
return service

def get_database_service(request: Request) -> DatabaseService:
service = getattr(request.app.state, 'db_service', None)
if not service:
raise HTTPException(status_code=500, detail="Database service not initialized")
return service
"""

print(import_snippet)
print(dependency_snippet)
