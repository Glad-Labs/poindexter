"""
Ollama Routes - Health checks and warm-up for local Ollama instance

Provides endpoints for:
- Checking Ollama connection status
- Warming up Ollama models
- Getting Ollama system status
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from routes.auth_unified import get_current_user
from schemas.ollama_schemas import OllamaHealthResponse, OllamaWarmupResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ollama", tags=["ollama"])

# Rate limiter for the warmup endpoint (expensive: loads model into GPU memory)
_warmup_limiter = Limiter(key_func=get_remote_address)

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT = 5.0


@router.get("/health", response_model=OllamaHealthResponse)
async def check_ollama_health(
    _current_user: Dict[str, Any] = Depends(get_current_user),
) -> OllamaHealthResponse:
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
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Ollama returned {response.status_code}",
            )

    except httpx.ConnectError:
        logger.warning("[Ollama] Connection refused - is Ollama running?", exc_info=True)
        return OllamaHealthResponse(
            connected=False,
            status="unreachable",
            models=None,
            message="❌ Cannot connect to Ollama at http://localhost:11434. Is Ollama running? Start it with: ollama serve",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except httpx.TimeoutException:
        logger.warning("[Ollama] Health check timeout", exc_info=True)
        return OllamaHealthResponse(
            connected=False,
            status="timeout",
            models=None,
            message="⏱️ Ollama health check timed out. It may be starting up.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        logger.error(f"[Ollama] Health check failed: {str(e)}", exc_info=True)
        return OllamaHealthResponse(
            connected=False,
            status="error",
            models=None,
            message="Ollama health check failed",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


@router.get("/models", response_model=dict)
async def get_ollama_models(
    _current_user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """
    Get list of available Ollama models (FAST - no timeout/warmup)

    This endpoint quickly returns the list of available models without
    blocking operations. Used by frontend on initialization.

    **Returns:**
    ```json
    {
      "models": ["llama2", "neural-chat", "mistral"],
      "connected": true
    }
    ```
    """
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:  # Fast timeout
            response = await client.get(f"{OLLAMA_HOST}/api/tags")

            if response.status_code == 200:
                data = response.json()
                models = [model["name"].replace(":latest", "") for model in data.get("models", [])]

                logger.info(f"[Ollama] Found {len(models)} models")
                return {"models": models, "connected": True}
            logger.warning(f"[Ollama] models endpoint returned {response.status_code}")
            return {"models": [], "connected": False}

    except (httpx.ConnectError, httpx.TimeoutException):
        logger.debug("[Ollama] Could not reach Ollama - Ollama service not available")
        # Return empty list if Ollama unavailable - honest response instead of misleading defaults
        return {"models": [], "connected": False}

    except Exception as e:
        logger.debug(f"[Ollama] Error getting models: {str(e)}")
        return {"models": [], "connected": False}


@router.post("/warmup", response_model=OllamaWarmupResponse)
@_warmup_limiter.limit("5/minute")
async def warmup_ollama(
    request: Request,
    model: Optional[str] = None,
    _current_user: Dict[str, Any] = Depends(get_current_user),
) -> OllamaWarmupResponse:
    """
    Warm up an Ollama model by running a simple prompt

    This generates an initial response to pre-load the model into memory,
    making subsequent requests faster.

    **Parameters:**
    - model: Model name to warm up (optional, defaults to OLLAMA_WARMUP_MODEL env or 'mistral')

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
    # Resolve model name before try/except so it's available in all except handlers
    resolved_model: str = (
        model or os.getenv("OLLAMA_WARMUP_MODEL", "mistral:latest") or "mistral:latest"
    )
    try:
        # First check if Ollama is running
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            check_response = await client.get(f"{OLLAMA_HOST}/api/tags")
            if check_response.status_code != 200:
                return OllamaWarmupResponse(
                    status="error",
                    model=resolved_model,
                    message="❌ Ollama is not responding to health check",
                    generation_time=None,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

            # Check if requested model exists
            models_data = check_response.json()
            available_models = [m["name"] for m in models_data.get("models", [])]

            if resolved_model not in available_models:
                logger.warning(
                    f"[Ollama] Model '{resolved_model}' not found. Available: {available_models}"
                )
                return OllamaWarmupResponse(
                    status="warning",
                    model=resolved_model,
                    message=f"⚠️ Model '{resolved_model}' not found. Available models: {', '.join(available_models)}",
                    generation_time=None,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

            # Now warm up the model with a simple prompt
            logger.info(f"[Ollama] Starting warm-up for model: {resolved_model}")

            warmup_payload = {
                "model": resolved_model,
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            }

            warmup_response = await client.post(
                f"{OLLAMA_HOST}/api/chat",
                json=warmup_payload,
                timeout=30.0,  # Longer timeout for model loading
            )

            if warmup_response.status_code == 200:
                data = warmup_response.json()
                gen_time = data.get("total_duration", 0) / 1e9  # Convert nanoseconds to seconds

                logger.info(f"[Ollama] Warm-up successful for {resolved_model} in {gen_time:.2f}s")

                return OllamaWarmupResponse(
                    status="success",
                    model=resolved_model,
                    message=f"✅ Model '{resolved_model}' warmed up successfully in {gen_time:.2f} seconds",
                    generation_time=gen_time,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            logger.error(f"[Ollama] Warm-up failed with status {warmup_response.status_code}")
            return OllamaWarmupResponse(
                status="error",
                model=resolved_model,
                message=f"❌ Warm-up failed: HTTP {warmup_response.status_code}",
                generation_time=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    except httpx.TimeoutException:
        logger.warning(f"[Ollama] Warm-up timeout for model: {resolved_model}", exc_info=True)
        return OllamaWarmupResponse(
            status="warning",
            model=resolved_model,
            message="⏱️ Model warm-up timed out. The model may still be loading.",
            generation_time=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except httpx.ConnectError:
        logger.error("[Ollama] Cannot connect to Ollama during warm-up", exc_info=True)
        return OllamaWarmupResponse(
            status="error",
            model=resolved_model,
            message="❌ Cannot connect to Ollama. Is it running?",
            generation_time=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception:
        logger.error("[Ollama] Warm-up error", exc_info=True)
        return OllamaWarmupResponse(
            status="error",
            model=resolved_model,
            message="❌ Warm-up error. Check server logs for details.",
            generation_time=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


@router.get("/status")
async def get_ollama_status(
    _current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
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
                    "last_check": datetime.now(timezone.utc).isoformat(),
                }
            return {
                "running": False,
                "host": OLLAMA_HOST,
                "models_available": 0,
                "models": [],
                "last_check": datetime.now(timezone.utc).isoformat(),
                "error": f"HTTP {response.status_code}",
            }

    except Exception as e:
        logger.warning("[get_ollama_status] Health check failed: %s", e, exc_info=True)
        return {
            "running": False,
            "host": OLLAMA_HOST,
            "models_available": 0,
            "models": [],
            "last_check": datetime.now(timezone.utc).isoformat(),
            "error": "An internal error occurred",
        }


@router.get("/select-model")
async def select_ollama_model(
    model: str = Query(..., description="Model name to validate and select"),
    _current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Validate and select an Ollama model for use.

    This is a read/query operation — it checks model availability without
    mutating any state, so GET is the correct HTTP method.

    **Query Parameters:**
    - model: Model name to select (validated against available Ollama models)

    **Example:**
    GET /api/ollama/select-model?model=mistral:latest

    **Returns:**
    - success: Whether model selection was successful
    - selected_model: The selected model name
    - message: Human-readable status message
    - timestamp: When selection was made
    """
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
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                logger.warning(f"[Ollama] Model not found: {model}")
                return {
                    "success": False,
                    "selected_model": None,
                    "message": f"❌ Model '{model}' not found. Available models: {', '.join(available_models)}",
                    "available_models": available_models,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            else:
                return {
                    "success": False,
                    "selected_model": None,
                    "message": f"❌ Cannot connect to Ollama: HTTP {response.status_code}",
                    "available_models": [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

    except Exception as e:
        logger.error(f"[Ollama] Model selection error: {str(e)}", exc_info=True)
        return {
            "success": False,
            "selected_model": None,
            "message": "Model selection error",
            "available_models": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
