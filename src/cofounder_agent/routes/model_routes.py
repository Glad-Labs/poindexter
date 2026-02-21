"""
Model Management Routes for Cofounder Agent

Provides endpoints for:
- Listing available AI models
- Getting provider status
- Model recommendations
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from schemas.models_schemas import (
    ModelInfo,
    ModelsListResponse,
    ProvidersStatusResponse,
    ProviderStatus,
)
from services.model_consolidation_service import get_model_consolidation_service
from services.model_constants import PROVIDER_ICONS

logger = logging.getLogger(__name__)

# Router for all model-related endpoints
models_router = APIRouter(prefix="/api/models", tags=["models"])


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


async def _get_provider_health_cached(redis_cache):
    """
    Get provider health status with caching.

    Shared logic for all provider health endpoints to avoid duplication.

    Args:
        redis_cache: Redis cache instance (can be None)

    Returns:
        Dict with timestamp and provider status
    """
    cache_key = "provider_health_status"

    # Try to get from cache first
    if redis_cache:
        cached_result = await redis_cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Provider health status cache hit")
            return cached_result

    # Cache miss - fetch fresh data
    try:
        service = get_model_consolidation_service()
        status = service.get_status()

        result = {"timestamp": datetime.now().isoformat(), "providers": status}

        # Cache the result with 60s TTL
        if redis_cache:
            await redis_cache.set(cache_key, result, ttl=60)
            logger.debug(f"Provider health status cached with TTL 60s")

        return result
    except Exception as e:
        logger.error(f"Error fetching provider health: {e}")
        raise


# ============================================================================
# API ENDPOINTS
# ============================================================================


@models_router.get(
    "/available", response_model=ModelsListResponse, description="Get list of available AI models"
)
async def get_available_models():
    """
    Get all currently available models from the unified model consolidation service.

    Returns all models across all providers (Ollama, HuggingFace, Google, Anthropic, OpenAI)
    with unified interface and automatic fallback chain support.
    """
    try:
        service = get_model_consolidation_service()
        models_dict = service.list_models()

        # Flatten models from all providers
        models_list = []

        for provider, model_names in models_dict.items():
            icon = PROVIDER_ICONS.get(provider, "🤖")
            for model_name in model_names:
                models_list.append(
                    ModelInfo(
                        name=model_name,
                        displayName=f"{model_name} ({provider})",
                        provider=provider,
                        isFree=provider in ["ollama", "huggingface"],
                        size="unknown",
                        estimatedVramGb=0,
                        description=f"Model from {provider}",
                        icon=icon,
                        requiresInternet=provider != "ollama",
                    )
                )

        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )

    except Exception as e:
        logger.error(f"Error getting available models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting available models: {str(e)}")


@models_router.get("/status", description="Get status of all model providers")
async def get_provider_status(request: Request):
    """
    Get availability status of all model providers in the consolidation service.

    Returns provider statuses including:
    - Availability (up/down)
    - Last check time
    - Response metrics
    - Number of available models
    """
    try:
        redis_cache = getattr(request.app.state, "redis_cache", None)
        return await _get_provider_health_cached(redis_cache)
    except Exception as e:
        logger.error(f"Error getting provider status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting provider status: {str(e)}")


@models_router.post("/health/refresh", description="Refresh model provider health check cache")
async def refresh_provider_health(request: Request):
    """
    Refresh the provider health check cache.

    Use this endpoint to immediately update provider status cache instead of waiting for TTL expiration.
    Useful for testing or when you want to force a fresh health check.

    Returns:
        Current provider status immediately after cache invalidation and fresh check
    """
    try:
        redis_cache = getattr(request.app.state, "redis_cache", None)
        cache_key = "provider_health_status"

        # Invalidate cache
        if redis_cache:
            await redis_cache.delete(cache_key)
            logger.debug(f"Provider health status cache invalidated")

        # Fetch fresh data and cache it
        result = await _get_provider_health_cached(redis_cache)
        result["cache_refreshed"] = True
        return result
    except Exception as e:
        logger.error(f"Error refreshing provider health: {e}")
        raise HTTPException(status_code=500, detail=f"Error refreshing provider health: {str(e)}")


@models_router.get(
    "/recommended",
    response_model=ModelsListResponse,
    description="Get recommended models for current environment",
)
async def get_recommended_models():
    """
    Get models recommended by the model consolidation service.

    Uses the fallback chain priority to recommend best models:
    1. Ollama (local, free, zero latency)
    2. HuggingFace (free tier, reasonable quality)
    3. Google Gemini (paid, high quality)
    4. Anthropic Claude (paid, very high quality)
    5. OpenAI GPT (expensive, best quality)
    """
    try:
        service = get_model_consolidation_service()
        models_dict = service.list_models()

        # Return models in fallback chain priority order
        models_list = []
        provider_order = ["ollama", "huggingface", "google", "anthropic", "openai"]

        for provider in provider_order:
            model_names = models_dict.get(provider, [])
            icon = PROVIDER_ICONS.get(provider, "🤖")
            for model_name in model_names[:1]:  # Just first model per provider
                models_list.append(
                    ModelInfo(
                        name=model_name,
                        displayName=f"{model_name} (Recommended)",
                        provider=provider,
                        isFree=provider in ["ollama", "huggingface"],
                        size="unknown",
                        estimatedVramGb=0,
                        description=f"Recommended model from {provider}",
                        icon=icon,
                        requiresInternet=provider != "ollama",
                    )
                )

        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )

    except Exception as e:
        logger.error(f"Error getting recommended models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting recommended models: {str(e)}")


@models_router.get(
    "/rtx5070",
    response_model=ModelsListResponse,
    description="Get models optimized for RTX 5070 (12GB VRAM)",
)
async def get_rtx5070_models():
    """
    Get models that fit within RTX 5070's 12GB VRAM.

    RTX 5070 can efficiently run 7B-13B parameter models.
    Falls back to cloud providers if local models exhausted.
    """
    try:
        service = get_model_consolidation_service()
        models_dict = service.list_models()

        models_list = []

        # Ollama models first (local, use VRAM), then cloud models
        provider_order = ["ollama", "huggingface", "google", "anthropic", "openai"]

        for provider in provider_order:
            model_names = models_dict.get(provider, [])
            icon = PROVIDER_ICONS.get(provider, "🤖")

            # Limit Ollama to 2 models to respect VRAM
            limit = 2 if provider == "ollama" else 3

            for model_name in model_names[:limit]:
                models_list.append(
                    ModelInfo(
                        name=model_name,
                        displayName=f"{model_name} (RTX5070 compatible)",
                        provider=provider,
                        isFree=provider in ["ollama", "huggingface"],
                        size="7B-13B" if provider == "ollama" else "unknown",
                        estimatedVramGb=8 if provider == "ollama" else 0,
                        description=f"Optimized for RTX 5070 from {provider}",
                        icon=icon,
                        requiresInternet=provider != "ollama",
                    )
                )

        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )

    except Exception as e:
        logger.error(f"Error getting RTX5070 models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting RTX5070 models: {str(e)}")


# ========== ADDITIONAL ENDPOINTS FOR /api/models (legacy support) ==========
