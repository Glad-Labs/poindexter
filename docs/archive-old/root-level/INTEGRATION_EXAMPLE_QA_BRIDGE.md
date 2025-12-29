"""
Example: Complete QA Agent + Quality Evaluator Integration

This example shows how to:

1. Use existing QA Agent
2. Use new Quality Evaluator
3. Bridge them together
4. Integrate into content routes
5. Automatically refine on low scores

Copy/paste ready code for your content routes.
"""

# ============================================================================

# SETUP: Add these imports to your content_routes.py

# ============================================================================

from typing import Optional, Dict, Any
from pydantic import BaseModel

# Quality evaluation imports

from ..services.quality_evaluator import QualityEvaluator
from ..services.quality_score_persistence import QualityScorePersistence
from ..services.qa_agent_bridge import QAAgentBridge, HybridQualityResult
from ..services.unified_quality_orchestrator import UnifiedQualityOrchestrator

# Existing imports

from ...agents.content_agent.agents.qa_agent import QAAgent
from ...agents.content_agent.models import BlogPost

# ============================================================================

# STEP 1: Input/Output Models

# ============================================================================

class EvaluateContentRequest(BaseModel):
"""Request model for content evaluation endpoint"""
content: str
topic: Optional[str] = None
primary_keyword: Optional[str] = None
target_audience: Optional[str] = None
use_qa_agent: bool = True
use_pattern_eval: bool = True

class EvaluationResult(BaseModel):
"""Response model for evaluation endpoint"""
content_preview: str
pattern_evaluation: Optional[Dict[str, Any]] = None
qa_evaluation: Optional[Dict[str, Any]] = None
hybrid_evaluation: Optional[Dict[str, Any]] = None
passing: bool
feedback: str
recommendations: list
evaluation_method: str
timestamp: str

# ============================================================================

# STEP 2: Initialize Services (usually in main.py or dependency injection)

# ============================================================================

async def initialize_quality_services():
"""
Initialize quality evaluation services.
Call this once at application startup.

    Usage in main.py:
        @app.on_event("startup")
        async def startup_event():
            await initialize_quality_services()
    """

    # Initialize singleton services
    quality_evaluator = await QualityEvaluator.get_instance()
    qa_agent = await QAAgent.get_instance()  # Uses existing LLMClient
    qa_bridge = await QAAgentBridge.get_instance()
    persistence = await QualityScorePersistence.get_instance()

    # Create orchestrator
    orchestrator = UnifiedQualityOrchestrator(
        quality_evaluator=quality_evaluator,
        qa_agent=qa_agent,
        qa_bridge=qa_bridge,
        persistence=persistence
    )

    # Store in app state for dependency injection
    # (implementation depends on your FastAPI setup)

    return orchestrator

# ============================================================================

# STEP 3: Integration into Content Generation Route

# ============================================================================

async def generate_and_evaluate_content(
topic: str,
orchestrator: UnifiedQualityOrchestrator,
content_agent # Your existing content agent
) -> Dict[str, Any]:
"""
Generate content and automatically evaluate with QA Agent + patterns.

    Flow:
    1. Content agent generates content
    2. Quality evaluation runs (pattern + QA Agent)
    3. If passing: return result
    4. If failing: get recommendations

    Args:
        topic: Content topic
        orchestrator: UnifiedQualityOrchestrator instance
        content_agent: Your content generation agent

    Returns:
        Dict with content and evaluation results
    """

    # Step 1: Generate content using existing content agent
    print(f"Generating content for: {topic}")
    blog_post = await content_agent.generate(topic=topic)

    # Step 2: Evaluate with unified orchestrator (pattern + QA Agent)
    print("Evaluating content quality...")
    evaluation_result = await orchestrator.evaluate_content(
        content=blog_post.content,
        post=blog_post,
        context={
            "topic": topic,
            "keyword": blog_post.primary_keyword,
            "audience": blog_post.target_audience
        }
    )

    # Step 3: If not passing, get refinement recommendations
    if not evaluation_result.get("passing"):
        print(f"⚠️ Content score: {evaluation_result.get('hybrid_evaluation', {}).get('overall', 'N/A')}/10")
        print(f"Recommendations: {evaluation_result.get('recommendations', [])}")

        # Option 1: Return with recommendations for manual refinement
        return {
            "content": blog_post,
            "evaluation": evaluation_result,
            "needs_refinement": True
        }

        # Option 2: In production, call content agent to auto-refine
        # refined_content = await content_agent.refine(
        #     original_content=blog_post.content,
        #     feedback=evaluation_result['feedback'],
        #     recommendations=evaluation_result['recommendations']
        # )
        # Re-evaluate refined content...

    return {
        "content": blog_post,
        "evaluation": evaluation_result,
        "needs_refinement": False
    }

# ============================================================================

# STEP 4: FastAPI Route - Evaluate Content Endpoint

# ============================================================================

# Add this to your content_routes.py file

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api", tags=["content"])

@router.post("/evaluate", response_model=EvaluationResult)
async def evaluate_content_endpoint(
request: EvaluateContentRequest,
orchestrator: UnifiedQualityOrchestrator = Depends(get_unified_orchestrator)
) -> EvaluationResult:
"""
Evaluate content using QA Agent + pattern-based evaluation.

    Example request:
    ```json
    {
        "content": "Full content text here...",
        "topic": "AI in Business",
        "primary_keyword": "artificial intelligence",
        "use_qa_agent": true,
        "use_pattern_eval": true
    }
    ```

    Example response:
    ```json
    {
        "content_preview": "Full content text here...",
        "pattern_evaluation": {
            "clarity": 8.5,
            "accuracy": 8.0,
            "completeness": 7.5,
            "relevance": 9.0,
            "seo_quality": 7.0,
            "readability": 8.5,
            "engagement": 8.0,
            "overall": 8.1
        },
        "qa_evaluation": {
            "approved": true,
            "feedback": "Well-written content..."
        },
        "hybrid_evaluation": {
            "overall": 8.2,
            "qa_weight": 0.4,
            "pattern_weight": 0.6,
            "feedback": "Excellent quality content...",
            "recommendations": ["Add more examples"]
        },
        "passing": true,
        "feedback": "Content meets publication standards",
        "recommendations": ["Add more examples"],
        "evaluation_method": "hybrid",
        "timestamp": "2025-10-25T..."
    }
    ```
    """

    try:
        # Create BlogPost object for QA Agent
        blog_post = BlogPost(
            title="Content for Evaluation",
            topic=request.topic,
            primary_keyword=request.primary_keyword,
            target_audience=request.target_audience or "general"
        )

        # Run unified evaluation
        result = await orchestrator.evaluate_content(
            content=request.content,
            post=blog_post,
            context={
                "topic": request.topic,
                "keyword": request.primary_keyword,
                "audience": request.target_audience
            },
            use_qa_agent=request.use_qa_agent,
            use_pattern_eval=request.use_pattern_eval,
            store_result=True
        )

        return EvaluationResult(
            content_preview=request.content[:100] + "...",
            pattern_evaluation=result.get("pattern_evaluation"),
            qa_evaluation=result.get("qa_evaluation"),
            hybrid_evaluation=result.get("hybrid_evaluation"),
            passing=result.get("passing", False),
            feedback=result.get("feedback", ""),
            recommendations=result.get("recommendations", []),
            evaluation_method=result.get("evaluation_method", "unknown"),
            timestamp=str(result.get("timestamp", ""))
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-and-evaluate")
async def generate_and_evaluate_endpoint(
topic: str,
orchestrator: UnifiedQualityOrchestrator = Depends(get_unified_orchestrator),
content_agent = Depends() # Your content agent dependency
) -> Dict[str, Any]:
"""
Generate content and evaluate it in one call.

    Returns both the generated content and quality evaluation.
    If content doesn't pass, includes refinement recommendations.
    """

    try:
        result = await generate_and_evaluate_content(
            topic=topic,
            orchestrator=orchestrator,
            content_agent=content_agent
        )
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/evaluate-and-refine")
async def evaluate_and_refine_endpoint(
request: EvaluateContentRequest,
orchestrator: UnifiedQualityOrchestrator = Depends(get_unified_orchestrator),
content_agent = Depends() # Content agent with refinement capability
) -> Dict[str, Any]:
"""
Evaluate content and automatically refine if score is too low.

    This endpoint will:
    1. Evaluate content
    2. If passing: return result
    3. If failing: request refinement from content agent
    4. Re-evaluate refined content
    5. Repeat up to 2 times

    Useful for ensuring publication-ready content.
    """

    try:
        # Create BlogPost object
        blog_post = BlogPost(
            title="Content for Evaluation",
            topic=request.topic,
            primary_keyword=request.primary_keyword,
            target_audience=request.target_audience or "general"
        )

        # Run evaluate_and_refine workflow
        result = await orchestrator.evaluate_and_refine(
            content=request.content,
            post=blog_post,
            context={
                "topic": request.topic,
                "keyword": request.primary_keyword,
                "audience": request.target_audience
            },
            max_refinements=2
        )

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================

# STEP 5: Register Routes in main.py

# ============================================================================

# In your main.py file, add:

"""
from .routes.evaluation_routes import router as evaluation_router
from .routes.evaluation_routes import initialize_quality_services

@app.on_event("startup")
async def startup():
await initialize_quality_services()

@app.include_router(evaluation_router)
"""

# ============================================================================

# STEP 6: Usage Examples

# ============================================================================

# Example 1: cURL - Evaluate content

"""
curl -X POST http://localhost:8000/api/evaluate \
 -H "Content-Type: application/json" \
 -d '{
"content": "# AI in Business\n\nArtificial intelligence is transforming...",
"topic": "AI in Business",
"primary_keyword": "artificial intelligence",
"use_qa_agent": true,
"use_pattern_eval": true
}'
"""

# Example 2: Python - Evaluate content

"""
import httpx

async with httpx.AsyncClient() as client:
response = await client.post(
"http://localhost:8000/api/evaluate",
json={
"content": "Full content text...",
"topic": "AI in Business",
"primary_keyword": "artificial intelligence",
"use_qa_agent": True,
"use_pattern_eval": True
}
)
evaluation_result = response.json()
print(f"Overall score: {evaluation_result['hybrid_evaluation']['overall']}/10")
print(f"Passing: {evaluation_result['passing']}")
"""

# Example 3: Generate and evaluate in one call

"""
curl -X POST "http://localhost:8000/api/generate-and-evaluate?topic=AI%20in%20Business"
"""

# Example 4: Evaluate with automatic refinement

"""
curl -X POST http://localhost:8000/api/evaluate-and-refine \
 -H "Content-Type: application/json" \
 -d '{
"content": "Content that might need refinement...",
"topic": "AI in Business"
}'
"""

# ============================================================================

# STEP 7: Database Migrations

# ============================================================================

# The quality evaluation system uses these database tables:

# Run migration: migrations/002_quality_evaluation.sql

#

# Tables created:

# - quality_evaluations: Stores evaluation results

# - quality_improvement_logs: Tracks refinement iterations

# - quality_metrics_daily: Daily aggregated metrics

#

# These are automatically created by your migration system

# ============================================================================

# STEP 8: Monitoring and Observability

# ============================================================================

# The system automatically tracks:

# - Evaluation method (pattern_only, qa_only, hybrid)

# - Score breakdown (7 criteria)

# - QA Agent approval/rejection

# - Refinement history

# - Timestamp and duration

#

# Query examples:

"""
-- Get average scores by criterion
SELECT
ROUND(AVG(clarity), 2) as clarity,
ROUND(AVG(accuracy), 2) as accuracy,
...
FROM quality_evaluations
WHERE evaluation_timestamp >= NOW() - INTERVAL 30 DAY;

-- Find content that failed QA but passed pattern eval
SELECT \* FROM quality_evaluations
WHERE evaluation_method = 'hybrid'
AND overall_score >= 7.0
AND metadata->>'qa_agent_approved' = 'false';

-- Track refinement attempts
SELECT COUNT(\*) as refinements, AVG(iterations) as avg_refinements
FROM quality_improvement_logs;
"""
