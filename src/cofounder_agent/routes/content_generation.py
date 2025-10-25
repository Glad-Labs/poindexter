"""
Content Generation Routes

Endpoints for AI-powered blog post generation using Ollama or other models.
Handles task creation, status tracking, and content generation.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import logging
import httpx
import os

logger = logging.getLogger(__name__)

# Router for content endpoints
content_router = APIRouter(prefix="/api/content", tags=["content"])

# In-memory task storage (in production, use database)
task_store: Dict[str, Dict[str, Any]] = {}


class GenerateBlogPostRequest(BaseModel):
    """Request to generate a blog post"""
    topic: str = Field(..., min_length=5, max_length=300, description="Blog post topic")
    style: str = Field("technical", description="Writing style: technical, narrative, listicle")
    tone: str = Field("professional", description="Tone: professional, casual, academic")
    target_length: int = Field(1500, ge=300, le=5000, description="Target word count")
    tags: Optional[List[str]] = Field(None, description="Tags for categorization")


class GenerateBlogPostResponse(BaseModel):
    """Response with task tracking info"""
    task_id: str
    status: str  # "pending" or "completed"
    message: str


class TaskStatus(BaseModel):
    """Task status info"""
    task_id: str
    status: str
    created_at: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# Ollama Configuration
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")


async def call_ollama(prompt: str) -> str:
    """Call Ollama API to generate content"""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:  # 5 minute timeout
            response = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,  # Get full response at once
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
    except Exception as e:
        logger.error(f"Ollama API error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate content: {str(e)}")


def generate_blog_post_prompt(topic: str, style: str, tone: str, target_length: int) -> str:
    """Generate prompt for blog post creation"""
    return f"""
Write a {target_length}-word blog post about: {topic}

Style: {style}
Tone: {tone}

Requirements:
1. Start with a compelling headline (as a markdown # header)
2. Include an engaging introduction paragraph
3. Break content into clear sections with headers (## headers)
4. Use bullet points where appropriate
5. Include practical examples or tips
6. Write a strong conclusion
7. Format as markdown
8. Aim for approximately {target_length} words

Generate the blog post now:
"""


async def generate_post_background(task_id: str, request: GenerateBlogPostRequest):
    """Generate blog post in background"""
    try:
        # Update status to processing
        task_store[task_id]["status"] = "processing"
        logger.info(f"Starting content generation for task {task_id}: topic='{request.topic}'")
        
        # Generate prompt
        prompt = generate_blog_post_prompt(
            request.topic,
            request.style,
            request.tone,
            request.target_length
        )
        
        # Call Ollama
        logger.info(f"Calling Ollama model '{OLLAMA_MODEL}' for task {task_id}")
        content = await call_ollama(prompt)
        
        if not content:
            raise ValueError("Ollama returned empty response")
        
        # Parse title from content
        lines = content.split("\n")
        title = next(
            (line.replace("# ", "").strip() for line in lines if line.startswith("# ")),
            request.topic
        )
        
        # Create slug
        slug = title.lower().replace(" ", "-").replace(",", "").replace(".", "")[:50]
        
        # Store result
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {
            "title": title,
            "slug": slug,
            "content": content,
            "topic": request.topic,
            "style": request.style,
            "tone": request.tone,
            "tags": request.tags or [request.topic.lower()],
            "generated_at": datetime.utcnow().isoformat()
        }
        
        logger.info(f"Task {task_id} completed successfully. Title: '{title}'")
        
    except Exception as e:
        task_store[task_id]["status"] = "error"
        task_store[task_id]["error"] = str(e)
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)


@content_router.post("/generate", response_model=GenerateBlogPostResponse)
async def generate_blog_post(
    request: GenerateBlogPostRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate a blog post using Ollama
    
    Returns immediately with task_id. Check /status/{task_id} for progress.
    
    Example:
    ```
    POST /api/content/generate
    {
        "topic": "Getting Started with FastAPI",
        "style": "technical",
        "tone": "professional",
        "target_length": 2000,
        "tags": ["fastapi", "python", "api"]
    }
    ```
    """
    # Create task
    task_id = str(uuid.uuid4())
    task_store[task_id] = {
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "request": request.dict()
    }
    
    logger.info(f"Created generation task {task_id} for topic: '{request.topic}'")
    
    # Start background generation
    background_tasks.add_task(generate_post_background, task_id, request)
    
    return GenerateBlogPostResponse(
        task_id=task_id,
        status="pending",
        message=f"Post generation started. Check /api/content/status/{task_id} for progress."
    )


@content_router.get("/status/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """
    Get the status of a content generation task
    
    Example:
    ```
    GET /api/content/status/uuid-12345
    ```
    
    Response:
    - status: "pending" | "processing" | "completed" | "error"
    - result: null until completed
    - error: error message if failed
    """
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    task = task_store[task_id]
    return TaskStatus(
        task_id=task_id,
        status=task["status"],
        created_at=task["created_at"],
        result=task.get("result"),
        error=task.get("error")
    )


@content_router.get("/tasks", response_model=Dict[str, Any])
async def list_tasks():
    """
    Get all generation tasks
    
    Returns:
    {
        "total": 5,
        "tasks": [
            {"task_id": "...", "status": "completed", "created_at": "...", ...}
        ]
    }
    """
    tasks = []
    for task_id, task in task_store.items():
        tasks.append({
            "task_id": task_id,
            "status": task["status"],
            "created_at": task["created_at"],
            "topic": task.get("request", {}).get("topic")
        })
    
    return {
        "total": len(tasks),
        "tasks": tasks
    }


@content_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a task from memory"""
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    del task_store[task_id]
    return {"message": f"Task {task_id} deleted"}


# ============================================================================
# STRAPI INTEGRATION ENDPOINTS
# ============================================================================


class SavePostRequest(BaseModel):
    """Request to save generated post to Strapi"""
    task_id: str = Field(..., description="Task ID from generate endpoint")
    publish: bool = Field(False, description="Auto-publish post (default: draft)")


class SavePostResponse(BaseModel):
    """Response with saved post info"""
    strapi_post_id: int
    title: str
    slug: str
    status: str
    message: str


@content_router.post("/save-to-strapi", response_model=SavePostResponse)
async def save_post_to_strapi(request: SavePostRequest):
    """
    Save generated blog post to Strapi CMS
    
    Takes a completed generation task and saves it to Strapi CMS.
    Can automatically publish the post or save as draft.
    
    Example:
    ```
    POST /api/content/save-to-strapi
    {
        "task_id": "uuid-from-generate",
        "publish": true
    }
    ```
    
    Response:
    ```
    {
        "strapi_post_id": 123,
        "title": "Generated Post Title",
        "slug": "generated-post-title",
        "status": "published",
        "message": "Post saved to Strapi successfully"
    }
    ```
    """
    # Get task
    if request.task_id not in task_store:
        raise HTTPException(
            status_code=404,
            detail=f"Task {request.task_id} not found. Check task_id."
        )
    
    task = task_store[request.task_id]
    
    if task["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task not ready for saving. Current status: {task['status']}. "
                   f"Wait for task to complete before saving to Strapi."
        )
    
    result = task.get("result")
    if not result:
        raise HTTPException(
            status_code=400,
            detail="No result found for task"
        )
    
    # Import here to avoid circular imports
    from services.strapi_client import StrapiClient, StrapiEnvironment
    
    # Save to Strapi
    try:
        logger.info(f"Saving post to Strapi: '{result['title']}'")
        
        strapi = StrapiClient(environment=StrapiEnvironment.PRODUCTION)
        
        strapi_response = await strapi.create_blog_post(
            title=result["title"],
            content=result["content"],
            summary=f"Generated blog post about {result['topic']}",
            tags=result.get("tags", []),
            publish=request.publish
        )
        
        post_id = strapi_response.get("data", {}).get("id")
        status_text = "published" if request.publish else "draft"
        
        logger.info(f"Post saved to Strapi successfully. ID: {post_id}, Status: {status_text}")
        
        return SavePostResponse(
            strapi_post_id=post_id,
            title=result["title"],
            slug=result["slug"],
            status=status_text,
            message=f"Post saved to Strapi as {status_text}. Post ID: {post_id}"
        )
    
    except Exception as e:
        logger.error(f"Failed to save to Strapi: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save post to Strapi: {str(e)}. "
                   f"Check Strapi connection and API token."
        )
