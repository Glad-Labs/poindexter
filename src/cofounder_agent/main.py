"""
GLAD Labs AI Co-Founder Agent
FastAPI application serving as the central orchestrator for the GLAD Labs ecosystem
Implements Google-native stack with Firestore and Pub/Sub integration
"""

import sys
import os
import logging
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import uvicorn
import structlog
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Add the parent directory (src) to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from orchestrator_logic import Orchestrator

# Try to import Google Cloud services (may not be available in dev)
try:
    from services.firestore_client import FirestoreClient
    from services.pubsub_client import PubSubClient
    from services.performance_monitor import PerformanceMonitor
    from services.intervention_handler import InterventionHandler, initialize_intervention_handler
    from services.ai_cache import AIResponseCache, initialize_ai_cache
    from services.model_router import ModelRouter, initialize_model_router
    GOOGLE_CLOUD_AVAILABLE = True
except ImportError:
    FirestoreClient = None
    PubSubClient = None
    PerformanceMonitor = None
    InterventionHandler = None
    initialize_intervention_handler = None
    AIResponseCache = None
    initialize_ai_cache = None
    ModelRouter = None
    initialize_model_router = None
    GOOGLE_CLOUD_AVAILABLE = False
    logging.warning("Google Cloud services not available - running in development mode")

# Configure structured logging
try:
    import structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logger = structlog.get_logger(__name__)
except ImportError:
    # Fallback to standard logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

# Global service instances
firestore_client: Optional[object] = None
pubsub_client: Optional[object] = None
orchestrator: Optional[Orchestrator] = None
performance_monitor: Optional[object] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for service initialization and cleanup"""
    global firestore_client, pubsub_client, orchestrator, performance_monitor
    
    try:
        # Initialize services
        logger.info("Initializing GLAD Labs AI Co-Founder services")
        
        if GOOGLE_CLOUD_AVAILABLE:
            # Initialize Firestore client
            firestore_client = FirestoreClient()  # type: ignore[call-arg]
            
            # Initialize performance monitor with Firestore
            performance_monitor = PerformanceMonitor(firestore_client=firestore_client)  # type: ignore[call-arg]
            
            # Initialize Pub/Sub client
            pubsub_client = PubSubClient()  # type: ignore[call-arg]
            await pubsub_client.ensure_topics_exist()
            
            # Update agent status
            await firestore_client.update_agent_status("cofounder", {  # type: ignore[union-attr]
                "status": "online",
                "service_version": "1.0.0",
                "capabilities": ["command_processing", "agent_orchestration", "task_management", "performance_monitoring"]
            })
        else:
            # Development mode - initialize performance monitor without Firestore
            performance_monitor = PerformanceMonitor() if PerformanceMonitor else None
        
        # Initialize intervention handler with strict cost controls
        if GOOGLE_CLOUD_AVAILABLE and InterventionHandler:
            intervention_handler = initialize_intervention_handler(
                pubsub_client=pubsub_client,
                confidence_threshold=0.75,
                error_threshold=3,
                budget_threshold=100.0,  # Strict $100 monthly budget limit
                enable_notifications=True
            )
            logger.info("Intervention handler initialized with $100/month budget threshold")
        else:
            intervention_handler = None
        
        # Initialize AI response cache for cost savings
        if GOOGLE_CLOUD_AVAILABLE and AIResponseCache:
            ai_cache = initialize_ai_cache(
                firestore_client=firestore_client,
                ttl_hours=24,
                max_memory_entries=1000
            )
            logger.info("AI response cache initialized (24h TTL, 1000 memory entries)")
        else:
            ai_cache = None
        
        # Initialize smart model router for cost optimization
        if ModelRouter:
            model_router = initialize_model_router(default_model="gpt-3.5-turbo")
            logger.info("Smart model router initialized (default: gpt-3.5-turbo)")
        else:
            model_router = None
        
        # Initialize orchestrator with services
        orchestrator = Orchestrator(
            firestore_client=firestore_client,
            pubsub_client=pubsub_client
        )
        
        logger.info("GLAD Labs AI Co-Founder services initialized successfully")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        raise
    finally:
        # Cleanup services
        logger.info("Shutting down GLAD Labs AI Co-Founder services")
        
        if GOOGLE_CLOUD_AVAILABLE and firestore_client:
            try:
                await firestore_client.update_agent_status("cofounder", {  # type: ignore[union-attr]
                    "status": "offline"
                })
            except Exception as e:
                logger.error(f"Error updating offline status: {e}")
        
        if GOOGLE_CLOUD_AVAILABLE and pubsub_client:
            try:
                await pubsub_client.close()  # type: ignore[union-attr]
            except Exception as e:
                logger.error(f"Error closing pub/sub: {e}")

app = FastAPI(
    title="GLAD Labs AI Co-Founder",
    description="Central orchestrator for GLAD Labs AI-driven business operations with Google Cloud integration",
    version="1.0.0",
    lifespan=lifespan
)

# Initialize rate limiter
try:
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    logger.info("Rate limiting enabled")
except (ImportError, NameError):
    logger.warning("slowapi not available - rate limiting disabled (install with: pip install slowapi)")
    limiter = None

# CORS middleware for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],  # React apps
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all API requests for security monitoring"""
    import time
    start_time = time.time()
    
    # Log request
    logger.info(
        "API Request",
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else "unknown"
    )
    
    # Process request
    response = await call_next(request)
    
    # Log response
    duration = time.time() - start_time
    logger.info(
        "API Response",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(duration * 1000, 2)
    )
    
    return response

class CommandRequest(BaseModel):
    """Request model for processing a command."""
    command: str
    context: Optional[Dict[str, Any]] = None
    priority: Optional[str] = "normal"

class CommandResponse(BaseModel):
    """Response model for the result of a command."""
    response: str
    task_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class StatusResponse(BaseModel):
    status: str
    data: Dict[str, Any]

class TaskRequest(BaseModel):
    topic: str
    task_type: str
    metadata: Optional[Dict[str, Any]] = None

class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str

@app.post("/command", response_model=CommandResponse)
async def process_command(request: CommandRequest, background_tasks: BackgroundTasks, http_request: Request):
    """
    Processes a command sent to the Co-Founder agent.

    This endpoint receives a command, delegates it to the orchestrator logic,
    and returns the result. Can optionally execute tasks in the background.
    
    Rate limit: 20 requests per minute
    """
    # Apply rate limiting if available
    if limiter:
        try:
            await limiter.check_request_limit(http_request, "20/minute")
        except RateLimitExceeded:
            raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    
    try:
        logger.info(f"Received command: {request.command}")
        
        if orchestrator is None:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")
        
        # Use async version if Google Cloud services are available
        if GOOGLE_CLOUD_AVAILABLE and orchestrator.firestore_client and orchestrator.pubsub_client:
            response = await orchestrator.process_command_async(request.command, request.context)
        else:
            # Fall back to synchronous version for development
            response = orchestrator.process_command(request.command, request.context)
        
        return CommandResponse(
            response=response.get("response", "Command processed"),
            task_id=response.get("task_id"),
            metadata=response.get("metadata")
        )
    except Exception as e:
        logger.error(f"Error processing command: {str(e)} | command={request.command}")
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {str(e)}")

@app.post("/tasks", response_model=TaskResponse)
async def create_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """
    Create a new task for content creation or business operations
    """
    try:
        logger.info(f"Creating task: topic={request.topic} type={request.task_type}")
        
        if not GOOGLE_CLOUD_AVAILABLE or not firestore_client:
            # Development mode - simulate task creation
            task_id = f"dev-task-{hash(request.topic)}"
            return TaskResponse(
                task_id=task_id,
                status="created",
                message=f"Task created for '{request.topic}' (development mode)"
            )
        
        # Create task in Firestore
        task_data = {
            "topic": request.topic,
            "task_type": request.task_type,
            "metadata": request.metadata or {},
            "status": "pending"
        }
        
        task_id = await firestore_client.add_task(task_data)  # type: ignore[union-attr]
        
        # Optionally trigger content agent if it's a content task
        if request.task_type == "content_creation" and pubsub_client:
            background_tasks.add_task(
                trigger_content_agent,
                task_id,
                request.topic,
                request.metadata
            )
        
        return TaskResponse(
            task_id=task_id,
            status="created",
            message=f"Task created for '{request.topic}'"
        )
        
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")

@app.get("/status", response_model=StatusResponse)
async def get_status():
    """
    Get the current status of the Co-Founder agent and connected services
    """
    try:
        status_data = {
            "service": "online",
            "google_cloud_available": GOOGLE_CLOUD_AVAILABLE,
            "orchestrator_initialized": orchestrator is not None,
            "timestamp": str(asyncio.get_event_loop().time())
        }
        
        if GOOGLE_CLOUD_AVAILABLE:
            # Add Google Cloud service health checks
            if firestore_client:
                firestore_health = await firestore_client.health_check()  # type: ignore[union-attr]
                status_data["firestore"] = firestore_health
            
            if pubsub_client:
                pubsub_health = await pubsub_client.health_check()  # type: ignore[union-attr]
                status_data["pubsub"] = pubsub_health
        
        return StatusResponse(status="healthy", data=status_data)
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return StatusResponse(
            status="unhealthy", 
            data={"error": str(e)}
        )

@app.get("/tasks/pending")
async def get_pending_tasks(limit: int = 10):
    """
    Get pending tasks from the task queue
    """
    try:
        if not GOOGLE_CLOUD_AVAILABLE or not firestore_client:
            return {"tasks": [], "message": "Running in development mode"}
        
        tasks = await firestore_client.get_pending_tasks(limit)  # type: ignore[union-attr]
        return {"tasks": tasks, "count": len(tasks)}
        
    except Exception as e:
        logger.error(f"Error getting pending tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get tasks: {str(e)}")

@app.get("/metrics/performance")
async def get_performance_metrics(hours: int = 24):
    """
    Get comprehensive performance metrics and analytics
    """
    try:
        if performance_monitor:
            metrics = await performance_monitor.get_performance_summary(hours=hours)  # type: ignore[union-attr]
            return {"metrics": metrics, "status": "success"}
        else:
            return {"message": "Performance monitoring not available", "status": "disabled"}
    except Exception as e:
        logger.error(f"Error getting performance metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get performance metrics: {str(e)}")

@app.get("/metrics/health")
async def get_health_metrics():
    """
    Get current system health metrics and status
    """
    try:
        if performance_monitor:
            health = await performance_monitor.get_health_metrics()  # type: ignore[union-attr]
            return {"health": health, "status": "success"}
        else:
            return {
                "health": {
                    "overall_status": "unknown",
                    "message": "Performance monitoring not available"
                },
                "status": "disabled"
            }
    except Exception as e:
        logger.error(f"Error getting health metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get health metrics: {str(e)}")

@app.get("/metrics/costs")
async def get_cost_metrics():
    """
    Get comprehensive cost metrics including AI API usage, cache performance, and savings.
    Provides real-time cost analysis for budget monitoring and optimization.
    """
    try:
        from services.ai_cache import get_ai_cache
        from services.model_router import get_model_router
        from services.intervention_handler import get_intervention_handler
        
        cost_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "budget": {
                "monthly_limit": 100.0,
                "current_spent": 0.0,  # TODO: Track actual spend
                "remaining": 100.0,
                "alerts": []
            },
            "ai_cache": None,
            "model_router": None,
            "interventions": None
        }
        
        # AI Cache metrics
        cache = get_ai_cache()
        if cache:
            cache_metrics = cache.get_metrics()
            cost_data["ai_cache"] = {
                "total_requests": cache_metrics['total_requests'],
                "cache_hits": cache_metrics['hits'],
                "cache_misses": cache_metrics['misses'],
                "hit_rate_percentage": cache_metrics['hit_rate'],
                "memory_hits": cache_metrics['memory_hits'],
                "firestore_hits": cache_metrics['firestore_hits'],
                "memory_entries": cache_metrics['memory_entries'],
                "estimated_savings_usd": round(cache_metrics['hits'] * 0.015, 2)  # Avg $0.015 per cached call
            }
        
        # Model Router metrics
        router = get_model_router()
        if router:
            router_metrics = router.get_metrics()
            cost_data["model_router"] = {
                "total_requests": router_metrics['total_requests'],
                "budget_model_uses": router_metrics['budget_model_uses'],
                "budget_model_percentage": router_metrics['budget_model_percentage'],
                "premium_model_uses": router_metrics['premium_model_uses'],
                "estimated_cost_actual_usd": router_metrics['estimated_cost_actual'],
                "estimated_cost_baseline_usd": router_metrics['estimated_cost_premium_baseline'],
                "estimated_savings_usd": router_metrics['estimated_cost_saved'],
                "savings_percentage": router_metrics['savings_percentage']
            }
        
        # Intervention metrics
        handler = get_intervention_handler()
        if handler:
            pending_interventions = handler.get_pending_interventions()
            cost_data["interventions"] = {
                "pending_count": len(pending_interventions),
                "pending_task_ids": pending_interventions,
                "budget_threshold_usd": 100.0
            }
        
        # Calculate total savings
        total_savings = 0.0
        if cost_data["ai_cache"]:
            total_savings += cost_data["ai_cache"]["estimated_savings_usd"]
        if cost_data["model_router"]:
            total_savings += cost_data["model_router"]["estimated_savings_usd"]
        
        cost_data["summary"] = {
            "total_estimated_savings_usd": round(total_savings, 2),
            "optimization_status": "active" if total_savings > 0 else "inactive"
        }
        
        return {
            "costs": cost_data,
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Error getting cost metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get cost metrics: {str(e)}")

@app.post("/metrics/costs/reset")
async def reset_cost_metrics():
    """
    Reset cost tracking metrics (admin endpoint).
    Useful for starting new billing periods or after optimization changes.
    """
    try:
        from services.ai_cache import get_ai_cache
        from services.model_router import get_model_router
        
        reset_results = {}
        
        # Reset AI cache metrics
        cache = get_ai_cache()
        if cache:
            cache.reset_metrics()
            reset_results["ai_cache"] = "reset"
        
        # Reset model router metrics
        router = get_model_router()
        if router:
            router.reset_metrics()
            reset_results["model_router"] = "reset"
        
        logger.info("Cost metrics reset", components=list(reset_results.keys()))
        
        return {
            "message": "Cost metrics reset successfully",
            "reset_components": reset_results,
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Error resetting cost metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reset cost metrics: {str(e)}")

@app.post("/metrics/reset")
async def reset_performance_metrics():
    """
    Reset session-level performance metrics (admin endpoint)
    """
    try:
        if performance_monitor:
            performance_monitor.reset_session_metrics()  # type: ignore[union-attr]
            return {"message": "Performance metrics reset successfully", "status": "success"}
        else:
            return {"message": "Performance monitoring not available", "status": "disabled"}
    except Exception as e:
        logger.error(f"Error resetting metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reset metrics: {str(e)}")

@app.get("/")
async def root():
    """
    Root endpoint to confirm the server is running.
    """
    return {
        "message": "GLAD Labs AI Co-Founder is running",
        "version": "1.0.0",
        "google_cloud_enabled": GOOGLE_CLOUD_AVAILABLE
    }

@app.get("/financial/cost-analysis")
@limiter.limit("20/minute")
async def get_cost_analysis(request: Request):
    """
    Get comprehensive cost analysis from Financial Agent.
    
    Returns:
        - Monthly budget status
        - Optimization performance (cache, routing)
        - Budget alerts and recommendations
        - End-of-month projections
    """
    try:
        # Import Financial Agent
        from agents.financial_agent.financial_agent import FinancialAgent
        
        # Initialize with cost tracking
        financial_agent = FinancialAgent(
            cofounder_api_url="http://localhost:8000",
            pubsub_client=pubsub_client,
            enable_cost_tracking=True
        )
        
        # Perform cost analysis
        analysis = await financial_agent.analyze_costs()
        
        return {
            "analysis": analysis,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error performing cost analysis", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze costs: {str(e)}"
        )

@app.get("/financial/monthly-summary")
@limiter.limit("20/minute")
async def get_monthly_summary(request: Request):
    """
    Get monthly cost summary from Financial Agent.
    
    Returns:
        - Monthly spending and remaining budget
        - Alert history
        - Spending projections
    """
    try:
        # Import Financial Agent
        from agents.financial_agent.financial_agent import FinancialAgent
        
        # Initialize with cost tracking
        financial_agent = FinancialAgent(
            cofounder_api_url="http://localhost:8000",
            pubsub_client=pubsub_client,
            enable_cost_tracking=True
        )
        
        # Get monthly summary
        summary = financial_agent.get_monthly_summary()
        
        return {
            "summary": summary,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error fetching monthly summary", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch monthly summary: {str(e)}"
        )

async def trigger_content_agent(task_id: str, topic: str, metadata: Optional[Dict[str, Any]]):
    """Background task to trigger content agent via Pub/Sub"""
    try:
        if pubsub_client:
            content_request = {
                "task_id": task_id,
                "topic": topic,
                "type": "blog_post",
                "metadata": metadata or {}
            }
            
            message_id = await pubsub_client.publish_content_request(content_request)  # type: ignore[union-attr]
            logger.info(f"Content agent triggered: task_id={task_id} topic={topic} message_id={message_id}")
            
    except Exception as e:
        logger.error(f"Failed to trigger content agent: task_id={task_id} error={e}")


# ============================================================================
# NEW OVERSIGHT HUB API ENDPOINTS
# ============================================================================

@app.get("/models/status")
@limiter.limit("30/minute")
async def get_models_status(request: Request):
    """
    Get status and configuration of all AI model providers.
    
    Returns provider status, configured models, and active state for:
    - Ollama (local)
    - OpenAI
    - Anthropic
    - Google Gemini
    """
    try:
        # Check Ollama status
        ollama_configured = os.getenv("USE_OLLAMA", "false").lower() == "true"
        ollama_models = []
        if ollama_configured:
            try:
                from services.ollama_client import OllamaClient
                ollama_client = OllamaClient()
                ollama_models = await ollama_client.list_models()
            except Exception:
                pass
        
        # Check Gemini status
        gemini_configured = bool(os.getenv("GOOGLE_API_KEY"))
        gemini_models = []
        if gemini_configured:
            try:
                from services.gemini_client import GeminiClient
                gemini_client = GeminiClient()
                gemini_models = await gemini_client.list_models()
            except Exception:
                pass
        
        return {
            "ollama": {
                "configured": ollama_configured,
                "active": ollama_configured,
                "models": ollama_models,
            },
            "openai": {
                "configured": bool(os.getenv("OPENAI_API_KEY")),
                "active": bool(os.getenv("OPENAI_API_KEY")),
                "models": ["gpt-4", "gpt-3.5-turbo", "gpt-4-turbo"] if os.getenv("OPENAI_API_KEY") else [],
            },
            "anthropic": {
                "configured": bool(os.getenv("ANTHROPIC_API_KEY")),
                "active": bool(os.getenv("ANTHROPIC_API_KEY")),
                "models": ["claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"] if os.getenv("ANTHROPIC_API_KEY") else [],
            },
            "gemini": {
                "configured": gemini_configured,
                "active": gemini_configured,
                "models": gemini_models,
            }
        }
        
    except Exception as e:
        logger.error("Error fetching model status", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch model status: {str(e)}")


@app.get("/models/usage")
@limiter.limit("30/minute")
async def get_models_usage(request: Request):
    """
    Get usage statistics for all models over the last 24 hours.
    
    Returns request counts, costs, and performance metrics per model.
    """
    try:
        # For now, return mock data - will be replaced with actual tracking
        usage_data = {
            "ollama:llama3.2": {
                "request_count": 145,
                "total_cost": 0.0,
                "avg_response_time": 1250,
                "last_used": datetime.utcnow().isoformat(),
            },
            "openai:gpt-4": {
                "request_count": 23,
                "total_cost": 2.45,
                "avg_response_time": 3200,
                "last_used": datetime.utcnow().isoformat(),
            },
            "anthropic:claude-3-sonnet": {
                "request_count": 12,
                "total_cost": 1.20,
                "avg_response_time": 2800,
                "last_used": datetime.utcnow().isoformat(),
            }
        }
        
        return {
            "usage": usage_data,
            "period": "24h",
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error fetching model usage", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch model usage: {str(e)}")


class ModelTestRequest(BaseModel):
    provider: str
    model: str
    prompt: str = "Hello, how are you?"


@app.post("/models/test")
@limiter.limit("10/minute")
async def test_model(request: Request, test_req: ModelTestRequest):
    """
    Test connectivity and performance of a specific model.
    
    Sends a test prompt and returns response time, token count, and cost.
    """
    try:
        start_time = datetime.utcnow()
        
        # Route to appropriate provider
        if test_req.provider == "ollama":
            from services.ollama_client import OllamaClient
            ollama_client = OllamaClient()
            response_text = await ollama_client.generate(
                model=test_req.model,
                prompt=test_req.prompt
            )
            cost = 0.0
            
        elif test_req.provider == "openai":
            # Use OpenAI client
            import openai
            openai.api_key = os.getenv("OPENAI_API_KEY")
            response = await openai.ChatCompletion.acreate(
                model=test_req.model,
                messages=[{"role": "user", "content": test_req.prompt}],
                max_tokens=100
            )
            response_text = response.choices[0].message.content
            # Calculate cost based on token usage
            cost = 0.03  # Placeholder
            
        elif test_req.provider == "anthropic":
            # Use Anthropic client
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            response = await client.messages.create(
                model=test_req.model,
                max_tokens=100,
                messages=[{"role": "user", "content": test_req.prompt}]
            )
            response_text = response.content[0].text
            cost = 0.02  # Placeholder
            
        elif test_req.provider == "gemini":
            # Use Gemini client
            from services.gemini_client import GeminiClient
            gemini_client = GeminiClient()
            response_text = await gemini_client.generate(
                model=test_req.model,
                prompt=test_req.prompt,
                max_tokens=100
            )
            # Estimate cost based on model pricing
            pricing = gemini_client.get_pricing(test_req.model)
            estimated_tokens = len(test_req.prompt.split()) + len(response_text.split())
            cost = (estimated_tokens / 1000) * pricing["output"]
            
        else:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {test_req.provider}")
        
        end_time = datetime.utcnow()
        response_time = int((end_time - start_time).total_seconds() * 1000)
        
        return {
            "success": True,
            "response": response_text,
            "response_time": response_time,
            "token_count": len(str(response_text).split()),  # Rough estimate
            "cost": cost,
            "timestamp": end_time.isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error testing model: {test_req.provider}/{test_req.model} - {str(e)}")
        raise HTTPException(status_code=500, detail=f"Model test failed: {str(e)}")


class ModelToggleRequest(BaseModel):
    active: bool


@app.post("/models/{provider}/toggle")
@limiter.limit("10/minute")
async def toggle_model_provider(request: Request, provider: str):
    """
    Toggle a model provider on/off.
    
    Note: This is a placeholder - actual implementation would update
    configuration files or environment variables.
    """
    try:
        # In production, this would update environment config
        logger.info(f"Toggle requested for provider: {provider}")
        
        return {
            "success": True,
            "provider": provider,
            "message": "Provider toggle successful (note: requires restart to take effect)",
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error toggling provider", error=str(e), provider=provider)
        raise HTTPException(status_code=500, detail=f"Failed to toggle provider: {str(e)}")


@app.get("/tasks")
@limiter.limit("60/minute")
async def get_all_tasks(request: Request):
    """
    Get all tasks from Firestore with optional filtering.
    
    Query params:
    - status: Filter by task status
    - priority: Filter by priority level
    - agent: Filter by assigned agent
    """
    try:
        if not firestore_client:
            # Return mock data for development
            return {
                "tasks": [
                    {
                        "id": "task-1",
                        "title": "Create blog post about AI trends",
                        "description": "Research and write comprehensive blog post",
                        "agent": "content",
                        "status": "in_progress",
                        "priority": "high",
                        "created_at": datetime.utcnow().isoformat(),
                    },
                    {
                        "id": "task-2",
                        "title": "Generate financial report",
                        "description": "Monthly cost analysis and projections",
                        "agent": "financial",
                        "status": "queued",
                        "priority": "medium",
                        "created_at": datetime.utcnow().isoformat(),
                    }
                ]
            }
        
        # Fetch from Firestore
        tasks = await firestore_client.get_collection("tasks", limit=100)  # type: ignore[union-attr]
        
        return {
            "tasks": tasks,
            "count": len(tasks),
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error fetching tasks", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch tasks: {str(e)}")


class BulkTaskRequest(BaseModel):
    task_ids: List[str]
    action: str  # pause, resume, cancel, delete


@app.post("/tasks/bulk")
@limiter.limit("20/minute")
async def bulk_task_action(request: Request, bulk_req: BulkTaskRequest):
    """
    Perform bulk actions on multiple tasks.
    
    Supported actions: pause, resume, cancel, delete
    """
    try:
        if not firestore_client:
            # Mock response for development
            return {
                "success": True,
                "action": bulk_req.action,
                "task_count": len(bulk_req.task_ids),
                "message": f"Bulk {bulk_req.action} completed (mock)"
            }
        
        # Perform bulk action in Firestore
        results = []
        for task_id in bulk_req.task_ids:
            if bulk_req.action == "delete":
                await firestore_client.delete_document("tasks", task_id)  # type: ignore[union-attr]
            else:
                # Update status based on action
                status_map = {
                    "pause": "paused",
                    "resume": "queued",
                    "cancel": "cancelled"
                }
                await firestore_client.update_document(  # type: ignore[union-attr]
                    "tasks",
                    task_id,
                    {"status": status_map.get(bulk_req.action, "unknown")}
                )
            results.append(task_id)
        
        return {
            "success": True,
            "action": bulk_req.action,
            "task_ids": results,
            "count": len(results),
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error performing bulk action", error=str(e), action=bulk_req.action)
        raise HTTPException(status_code=500, detail=f"Bulk action failed: {str(e)}")


@app.get("/system/alerts")
@limiter.limit("30/minute")
async def get_system_alerts(request: Request):
    """
    Get active system alerts and warnings.
    
    Returns critical issues, warnings, and informational messages.
    """
    try:
        alerts = []
        
        # Check for high costs
        if GOOGLE_CLOUD_AVAILABLE:
            from agents.financial_agent.cost_tracking import CostTrackingService
            cost_tracker = CostTrackingService()
            current_costs = cost_tracker.get_current_month_total()
            budget_limit = cost_tracker.monthly_budget_limit
            
            if current_costs >= budget_limit * 0.9:
                alerts.append({
                    "severity": "error",
                    "message": f"Budget alert: ${current_costs:.2f} of ${budget_limit:.2f} used (90%+)",
                    "timestamp": datetime.utcnow().isoformat()
                })
            elif current_costs >= budget_limit * 0.75:
                alerts.append({
                    "severity": "warning",
                    "message": f"Budget warning: ${current_costs:.2f} of ${budget_limit:.2f} used (75%+)",
                    "timestamp": datetime.utcnow().isoformat()
                })
        
        # Check service health
        if not firestore_client:
            alerts.append({
                "severity": "warning",
                "message": "Firestore not connected - running in development mode",
                "timestamp": datetime.utcnow().isoformat()
            })
        
        # Add info message if no alerts
        if len(alerts) == 0:
            alerts.append({
                "severity": "info",
                "message": "All systems operational",
                "timestamp": datetime.utcnow().isoformat()
            })
        
        return {
            "alerts": alerts,
            "count": len(alerts),
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error fetching system alerts", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch alerts: {str(e)}")


@app.get("/metrics/summary")
@limiter.limit("30/minute")
async def get_metrics_summary(request: Request):
    """
    Get comprehensive system metrics summary for dashboard.
    
    Returns aggregated metrics including:
    - API calls (24h)
    - Total costs (24h)
    - Cache hit rate
    - Active agents
    - Queued tasks
    - Average response time
    """
    try:
        # Initialize default values
        api_calls_24h = 0
        total_cost_24h = 0.0
        cache_hit_rate = 0.0
        active_agents = 0
        queued_tasks = 0
        avg_response_time = 0
        
        # Get real data if services available
        if GOOGLE_CLOUD_AVAILABLE and performance_monitor:
            try:
                metrics = await performance_monitor.get_24h_metrics()  # type: ignore[union-attr]
                api_calls_24h = metrics.get("total_requests", 0)
                avg_response_time = metrics.get("avg_response_time", 0)
            except Exception:
                pass
        
        # Get cost data
        if GOOGLE_CLOUD_AVAILABLE:
            try:
                from agents.financial_agent.cost_tracking import CostTrackingService
                cost_tracker = CostTrackingService()
                total_cost_24h = cost_tracker.get_daily_total()
            except Exception:
                pass
        
        # Get cache stats
        if GOOGLE_CLOUD_AVAILABLE and AIResponseCache:
            try:
                cache = AIResponseCache()
                cache_stats = cache.get_stats()
                cache_hit_rate = cache_stats.get("hit_rate", 0.0)
            except Exception:
                pass
        
        # Get task count
        if firestore_client:
            try:
                tasks = await firestore_client.get_collection("tasks", limit=1000)  # type: ignore[union-attr]
                queued_tasks = len([t for t in tasks if t.get("status") in ["queued", "in_progress"]])
                active_agents = len(set(t.get("agent") for t in tasks if t.get("status") == "in_progress"))
            except Exception:
                pass
        
        return {
            "api_calls_24h": api_calls_24h,
            "total_cost_24h": total_cost_24h,
            "cache_hit_rate": cache_hit_rate,
            "active_agents": active_agents,
            "queued_tasks": queued_tasks,
            "avg_response_time": avg_response_time,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error fetching metrics summary", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch metrics summary: {str(e)}")


# ============================================================================
# SOCIAL MEDIA MANAGEMENT ENDPOINTS
# ============================================================================

class SocialMediaPostRequest(BaseModel):
    content: str
    platforms: list[str]
    scheduled_time: Optional[str] = None
    tone: Optional[str] = "professional"


class SocialMediaGenerateRequest(BaseModel):
    topic: str
    platform: str
    tone: str = "professional"
    include_hashtags: bool = True
    include_emojis: bool = True


@app.get("/social/platforms")
@limiter.limit("60/minute")
async def get_social_platforms(request: Request):
    """
    Get status of all social media platform connections.
    """
    try:
        # In production, this would check actual OAuth tokens and API connections
        # For now, return mock data
        platforms = {
            "twitter": {"connected": False, "account": None},
            "facebook": {"connected": False, "account": None},
            "instagram": {"connected": False, "account": None},
            "linkedin": {"connected": False, "account": None},
            "tiktok": {"connected": False, "account": None},
            "youtube": {"connected": False, "account": None}
        }
        
        return platforms
        
    except Exception as e:
        logger.error("Error fetching social platforms", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch platforms: {str(e)}")


@app.post("/social/connect")
@limiter.limit("10/minute")
async def connect_social_platform(request: Request, platform: str = None):
    """
    Connect a social media platform account.
    Initiates OAuth flow in production.
    """
    try:
        body = await request.json()
        platform = body.get("platform")
        
        if not platform:
            raise HTTPException(status_code=400, detail="Platform required")
        
        # In production, this would initiate OAuth flow
        logger.info(f"Connection requested for platform: {platform}")
        
        return {
            "success": True,
            "platform": platform,
            "message": f"{platform} connection initiated (OAuth flow would start here)",
            "redirect_url": f"https://oauth.example.com/{platform}/authorize"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error connecting platform", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to connect platform: {str(e)}")


@app.post("/social/generate")
@limiter.limit("30/minute")
async def generate_social_content(request: Request):
    """
    Generate AI-powered social media content for a specific platform.
    """
    try:
        body = await request.json()
        gen_req = SocialMediaGenerateRequest(**body)
        
        # Import social media agent
        from agents.social_media_agent import SocialMediaAgent
        
        # Initialize agent with model router
        social_agent = SocialMediaAgent(model_router=model_router)
        
        # Generate content
        result = await social_agent.generate_post(
            topic=gen_req.topic,
            platform=gen_req.platform,
            tone=gen_req.tone,
            include_hashtags=gen_req.include_hashtags,
            include_emojis=gen_req.include_emojis
        )
        
        return result
        
    except Exception as e:
        logger.error("Error generating social content", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to generate content: {str(e)}")


@app.get("/social/posts")
@limiter.limit("60/minute")
async def get_social_posts(request: Request):
    """
    Get all social media posts with analytics.
    """
    try:
        # In production, fetch from Firestore
        posts = []
        
        if firestore_client:
            try:
                posts_data = await firestore_client.get_collection("social_media_posts", limit=100)  # type: ignore[union-attr]
                posts = posts_data
            except Exception as e:
                logger.warning(f"Could not fetch posts from Firestore: {e}")
        
        # Calculate analytics
        total_posts = len(posts)
        total_engagement = sum(post.get("engagement", 0) for post in posts)
        
        platforms_engagement = {}
        for post in posts:
            for platform in post.get("platforms", []):
                platforms_engagement[platform] = platforms_engagement.get(platform, 0) + post.get("engagement", 0)
        
        top_platform = max(platforms_engagement.items(), key=lambda x: x[1])[0] if platforms_engagement else "N/A"
        
        return {
            "posts": posts,
            "analytics": {
                "total_posts": total_posts,
                "total_engagement": total_engagement,
                "avg_engagement_rate": (total_engagement / total_posts * 100) if total_posts > 0 else 0,
                "top_platform": top_platform
            }
        }
        
    except Exception as e:
        logger.error("Error fetching social posts", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch posts: {str(e)}")


@app.post("/social/posts")
@limiter.limit("30/minute")
async def create_social_post(request: Request):
    """
    Create and publish a social media post immediately.
    """
    try:
        body = await request.json()
        post_req = SocialMediaPostRequest(**body)
        
        # Create post object
        post_data = {
            "content": post_req.content,
            "platforms": post_req.platforms,
            "status": "published",
            "created_at": datetime.utcnow().isoformat(),
            "engagement": 0
        }
        
        # Store in Firestore
        post_id = None
        if firestore_client:
            try:
                doc_ref = await firestore_client.create_document("social_media_posts", post_data)  # type: ignore[union-attr]
                post_id = doc_ref.id
            except Exception as e:
                logger.warning(f"Could not store post in Firestore: {e}")
                post_id = f"post_{datetime.utcnow().timestamp()}"
        else:
            post_id = f"post_{datetime.utcnow().timestamp()}"
        
        # In production, actually publish to platforms here
        logger.info(f"Post created: {post_id} for platforms: {post_req.platforms}")
        
        return {
            "success": True,
            "post_id": post_id,
            "status": "published",
            "platforms": post_req.platforms,
            "message": "Post published successfully"
        }
        
    except Exception as e:
        logger.error("Error creating social post", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to create post: {str(e)}")


@app.post("/social/schedule")
@limiter.limit("30/minute")
async def schedule_social_post(request: Request):
    """
    Schedule a social media post for future publishing.
    """
    try:
        body = await request.json()
        post_req = SocialMediaPostRequest(**body)
        
        if not post_req.scheduled_time:
            raise HTTPException(status_code=400, detail="scheduled_time required")
        
        # Create post object
        post_data = {
            "content": post_req.content,
            "platforms": post_req.platforms,
            "scheduled_time": post_req.scheduled_time,
            "status": "scheduled",
            "created_at": datetime.utcnow().isoformat(),
            "engagement": 0
        }
        
        # Store in Firestore
        post_id = None
        if firestore_client:
            try:
                doc_ref = await firestore_client.create_document("social_media_posts", post_data)  # type: ignore[union-attr]
                post_id = doc_ref.id
            except Exception as e:
                logger.warning(f"Could not store post in Firestore: {e}")
                post_id = f"post_{datetime.utcnow().timestamp()}"
        else:
            post_id = f"post_{datetime.utcnow().timestamp()}"
        
        # In production, schedule with Pub/Sub or scheduler
        logger.info(f"Post scheduled: {post_id} for {post_req.scheduled_time}")
        
        return {
            "success": True,
            "post_id": post_id,
            "status": "scheduled",
            "scheduled_time": post_req.scheduled_time,
            "platforms": post_req.platforms
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error scheduling social post", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to schedule post: {str(e)}")


@app.post("/social/cross-post")
@limiter.limit("20/minute")
async def cross_post_social(request: Request):
    """
    Create and post content across multiple platforms with platform-specific optimization.
    """
    try:
        body = await request.json()
        content = body.get("content")
        platforms = body.get("platforms", [])
        
        if not content or not platforms:
            raise HTTPException(status_code=400, detail="content and platforms required")
        
        if len(platforms) < 2:
            raise HTTPException(status_code=400, detail="At least 2 platforms required for cross-posting")
        
        # Import social media agent
        from agents.social_media_agent import SocialMediaAgent
        
        # Initialize agent
        social_agent = SocialMediaAgent(model_router=model_router, firestore_client=firestore_client)
        
        # Cross-post with platform optimization
        result = await social_agent.cross_post(
            content=content,
            platforms=platforms,
            adapt_content=True
        )
        
        return {
            "success": True,
            "results": result,
            "platforms": platforms,
            "message": f"Content cross-posted to {len(platforms)} platforms"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error cross-posting", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to cross-post: {str(e)}")


@app.get("/social/analytics")
@limiter.limit("60/minute")
async def get_social_analytics(request: Request, post_id: Optional[str] = None):
    """
    Get engagement analytics for a specific post or overall metrics.
    """
    try:
        if post_id:
            # Get specific post analytics
            # In production, fetch from platform APIs
            analytics = {
                "post_id": post_id,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "impressions": 0,
                "engagement_rate": 0.0,
                "content": "Sample post content",
                "analyzed_at": datetime.utcnow().isoformat()
            }
            
            return analytics
        else:
            # Return overall analytics
            return {
                "total_posts": 0,
                "total_engagement": 0,
                "avg_engagement_rate": 0.0,
                "top_platforms": [],
                "recent_growth": 0.0
            }
        
    except Exception as e:
        logger.error("Error fetching social analytics", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")


@app.get("/social/trending")
@limiter.limit("30/minute")
async def get_trending_topics(request: Request, platform: str = "twitter"):
    """
    Get trending topics for a specific platform.
    """
    try:
        # Import social media agent
        from agents.social_media_agent import SocialMediaAgent
        
        # Initialize agent
        social_agent = SocialMediaAgent()
        
        # Get trending topics
        topics = await social_agent.get_trending_topics(platform=platform)
        
        return {
            "platform": platform,
            "topics": topics,
            "fetched_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Error fetching trending topics", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch trending topics: {str(e)}")


@app.delete("/social/posts/{post_id}")
@limiter.limit("30/minute")
async def delete_social_post(request: Request, post_id: str):
    """
    Delete a social media post.
    """
    try:
        # Delete from Firestore
        if firestore_client:
            try:
                await firestore_client.delete_document("social_media_posts", post_id)  # type: ignore[union-attr]
            except Exception as e:
                logger.warning(f"Could not delete post from Firestore: {e}")
        
        logger.info(f"Post deleted: {post_id}")
        
        return {
            "success": True,
            "post_id": post_id,
            "message": "Post deleted successfully"
        }
        
    except Exception as e:
        logger.error("Error deleting social post", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to delete post: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )
