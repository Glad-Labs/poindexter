"""
Ollama Routes - Health checks and warm-up for local Ollama instance

Provides endpoints for:
- Checking Ollama connection status
- Warming up Ollama models
- Getting Ollama system status
"""

import logging
import httpx
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ollama", tags=["ollama"])

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT = 5.0


class OllamaHealthResponse(BaseModel):
    """Response for Ollama health check"""
    connected: bool
    status: str
    models: Optional[list] = None
    message: str
    timestamp: str


class OllamaWarmupResponse(BaseModel):
    """Response for Ollama warm-up"""
    status: str
    model: str
    message: str
    generation_time: Optional[float] = None
    timestamp: str


class OllamaModelSelection(BaseModel):
    """Request body for model selection"""
    model: str


@router.get("/health", response_model=OllamaHealthResponse)
async def check_ollama_health() -> OllamaHealthResponse:
    """
    Check if Ollama is running and accessible
    
    **Returns:**
    - connected: Whether Ollama is reachable
    - status: Connection status message
    - models: List of available models (if connected)
    - message: Human-readable status message
    - timestamp: When the check was performed
    
    **Example Response (Connected):**
    ```json
    {
      "connected": true,
      "status": "running",
      "models": ["mistral", "llama2", "neural-chat"],
      "message": "✅ Ollama is running and ready",
      "timestamp": "2025-11-01T12:00:00.000Z"
    }
    ```
    
    **Example Response (Not Connected):**
    ```json
    {
      "connected": false,
      "status": "unreachable",
      "models": null,
      "message": "❌ Cannot connect to Ollama at http://localhost:11434. Is Ollama running?",
      "timestamp": "2025-11-01T12:00:00.000Z"
    }
    ```
    """
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            # Try to get tags (list of models)
            response = await client.get(f"{OLLAMA_HOST}/api/tags")
            
            if response.status_code == 200:
                data = response.json()
                models = [model["name"] for model in data.get("models", [])]
                
                logger.info(f"[Ollama] Health check successful. Found {len(models)} models")
                
                return OllamaHealthResponse(
                    connected=True,
                    status="running",
                    models=models,
                    message=f"✅ Ollama is running with {len(models)} model(s)",
                    timestamp=datetime.utcnow().isoformat()
                )
            else:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Ollama returned {response.status_code}"
                )
    
    except httpx.ConnectError:
        logger.warning("[Ollama] Connection refused - is Ollama running?")
        return OllamaHealthResponse(
            connected=False,
            status="unreachable",
            models=None,
            message="❌ Cannot connect to Ollama at http://localhost:11434. Is Ollama running? Start it with: ollama serve",
            timestamp=datetime.utcnow().isoformat()
        )
    
    except httpx.TimeoutException:
        logger.warning("[Ollama] Health check timeout")
        return OllamaHealthResponse(
            connected=False,
            status="timeout",
            models=None,
            message="⏱️ Ollama health check timed out. It may be starting up.",
            timestamp=datetime.utcnow().isoformat()
        )
    
    except Exception as e:
        logger.error(f"[Ollama] Health check failed: {str(e)}")
        return OllamaHealthResponse(
            connected=False,
            status="error",
            models=None,
            message=f"❌ Ollama health check failed: {str(e)}",
            timestamp=datetime.utcnow().isoformat()
        )


@router.post("/warmup", response_model=OllamaWarmupResponse)
async def warmup_ollama(model: str = "mistral") -> OllamaWarmupResponse:
    """
    Warm up an Ollama model by running a simple prompt
    
    This generates an initial response to pre-load the model into memory,
    making subsequent requests faster.
    
    **Parameters:**
    - model: Model name to warm up (default: mistral)
    
    **Returns:**
    - status: Warm-up status (success, warning, error)
    - model: The model that was warmed up
    - message: Human-readable status message
    - generation_time: Time taken to generate response (in seconds)
    - timestamp: When warm-up was performed
    
    **Example Request:**
    ```json
    POST /api/ollama/warmup
    {
      "model": "mistral"
    }
    ```
    
    **Example Response:**
    ```json
    {
      "status": "success",
      "model": "mistral",
      "message": "✅ Model warmed up successfully in 2.34 seconds",
      "generation_time": 2.34,
      "timestamp": "2025-11-01T12:00:00.000Z"
    }
    ```
    """
    try:
        # First check if Ollama is running
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            check_response = await client.get(f"{OLLAMA_HOST}/api/tags")
            if check_response.status_code != 200:
                return OllamaWarmupResponse(
                    status="error",
                    model=model,
                    message="❌ Ollama is not responding to health check",
                    generation_time=None,
                    timestamp=datetime.utcnow().isoformat()
                )
            
            # Check if requested model exists
            models_data = check_response.json()
            available_models = [m["name"] for m in models_data.get("models", [])]
            
            if model not in available_models:
                logger.warning(f"[Ollama] Model '{model}' not found. Available: {available_models}")
                return OllamaWarmupResponse(
                    status="warning",
                    model=model,
                    message=f"⚠️ Model '{model}' not found. Available models: {', '.join(available_models)}",
                    generation_time=None,
                    timestamp=datetime.utcnow().isoformat()
                )
            
            # Now warm up the model with a simple prompt
            logger.info(f"[Ollama] Starting warm-up for model: {model}")
            
            warmup_payload = {
                "model": model,
                "prompt": "Hi",  # Simple prompt to load model
                "stream": False
            }
            
            warmup_response = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json=warmup_payload,
                timeout=30.0  # Longer timeout for model loading
            )
            
            if warmup_response.status_code == 200:
                data = warmup_response.json()
                gen_time = data.get("total_duration", 0) / 1e9  # Convert nanoseconds to seconds
                
                logger.info(f"[Ollama] Warm-up successful for {model} in {gen_time:.2f}s")
                
                return OllamaWarmupResponse(
                    status="success",
                    model=model,
                    message=f"✅ Model '{model}' warmed up successfully in {gen_time:.2f} seconds",
                    generation_time=gen_time,
                    timestamp=datetime.utcnow().isoformat()
                )
            else:
                logger.error(f"[Ollama] Warm-up failed with status {warmup_response.status_code}")
                return OllamaWarmupResponse(
                    status="error",
                    model=model,
                    message=f"❌ Warm-up failed: HTTP {warmup_response.status_code}",
                    generation_time=None,
                    timestamp=datetime.utcnow().isoformat()
                )
    
    except httpx.TimeoutException:
        logger.warning(f"[Ollama] Warm-up timeout for model: {model}")
        return OllamaWarmupResponse(
            status="warning",
            model=model,
            message=f"⏱️ Model warm-up timed out. The model may still be loading.",
            generation_time=None,
            timestamp=datetime.utcnow().isoformat()
        )
    
    except httpx.ConnectError:
        logger.error("[Ollama] Cannot connect to Ollama during warm-up")
        return OllamaWarmupResponse(
            status="error",
            model=model,
            message="❌ Cannot connect to Ollama. Is it running?",
            generation_time=None,
            timestamp=datetime.utcnow().isoformat()
        )
    
    except Exception as e:
        logger.error(f"[Ollama] Warm-up error: {str(e)}")
        return OllamaWarmupResponse(
            status="error",
            model=model,
            message=f"❌ Warm-up error: {str(e)}",
            generation_time=None,
            timestamp=datetime.utcnow().isoformat()
        )


@router.get("/status")
async def get_ollama_status() -> Dict[str, Any]:
    """
    Get detailed Ollama system status
    
    **Returns:**
    - running: Whether Ollama is running
    - host: Ollama host URL
    - models_available: Number of available models
    - models: List of available model names
    - last_check: When this check was performed
    """
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.get(f"{OLLAMA_HOST}/api/tags")
            
            if response.status_code == 200:
                data = response.json()
                models = [model["name"] for model in data.get("models", [])]
                
                return {
                    "running": True,
                    "host": OLLAMA_HOST,
                    "models_available": len(models),
                    "models": models,
                    "last_check": datetime.utcnow().isoformat()
                }
            else:
                return {
                    "running": False,
                    "host": OLLAMA_HOST,
                    "models_available": 0,
                    "models": [],
                    "last_check": datetime.utcnow().isoformat(),
                    "error": f"HTTP {response.status_code}"
                }
    
    except Exception as e:
        return {
            "running": False,
            "host": OLLAMA_HOST,
            "models_available": 0,
            "models": [],
            "last_check": datetime.utcnow().isoformat(),
            "error": str(e)
        }


@router.post("/select-model")
async def select_ollama_model(request: OllamaModelSelection) -> Dict[str, Any]:
    """
    Validate and select an Ollama model for use
    
    **Parameters:**
    - model: Model name to select (will be validated against available models)
    
    **Returns:**
    - success: Whether model selection was successful
    - selected_model: The selected model name
    - message: Human-readable status message
    - timestamp: When selection was made
    
    **Example Request:**
    ```json
    {"model": "mistral:latest"}
    ```
    
    **Example Response:**
    ```json
    {
      "success": true,
      "selected_model": "mistral:latest",
      "message": "✅ Model 'mistral:latest' selected successfully",
      "timestamp": "2025-11-01T12:00:00.000Z"
    }
    ```
    """
    model = request.model
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            # Get list of available models
            response = await client.get(f"{OLLAMA_HOST}/api/tags")
            
            if response.status_code == 200:
                data = response.json()
                available_models = [m["name"] for m in data.get("models", [])]
                
                # Check if requested model is available
                if model in available_models:
                    logger.info(f"[Ollama] Model selected: {model}")
                    return {
                        "success": True,
                        "selected_model": model,
                        "message": f"✅ Model '{model}' selected successfully",
                        "available_models": available_models,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                else:
                    logger.warning(f"[Ollama] Model not found: {model}")
                    return {
                        "success": False,
                        "selected_model": None,
                        "message": f"❌ Model '{model}' not found. Available models: {', '.join(available_models)}",
                        "available_models": available_models,
                        "timestamp": datetime.utcnow().isoformat()
                    }
            else:
                return {
                    "success": False,
                    "selected_model": None,
                    "message": f"❌ Cannot connect to Ollama: HTTP {response.status_code}",
                    "available_models": [],
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    except Exception as e:
        logger.error(f"[Ollama] Model selection error: {str(e)}")
        return {
            "success": False,
            "selected_model": None,
            "message": f"❌ Model selection error: {str(e)}",
            "available_models": [],
            "timestamp": datetime.utcnow().isoformat()
        }
