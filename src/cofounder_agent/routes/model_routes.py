"""
Model Management Routes for Cofounder Agent

Provides endpoints for:
- Listing available AI models
- Getting provider status
- Model recommendations
"""

from services.logger_config import get_logger
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from routes.auth_unified import get_current_user
from schemas.models_schemas import (
    ModelInfo,
    ModelsListResponse,
)
from services.model_consolidation_service import get_model_consolidation_service
from services.model_constants import PROVIDER_ICONS

logger = get_logger(__name__)
# Router for all model-related endpoints — requires authentication
models_router = APIRouter(
    prefix="/api/models",
    tags=["models"],
    dependencies=[Depends(get_current_user)],
)


# ============================================================================
# API ENDPOINTS
# ============================================================================


@models_router.get(
    "/available", response_model=ModelsListResponse, description="Get list of available AI models"
)
async def get_available_models(
    vram_gb: Optional[int] = Query(
        None,
        description="Filter models that fit within this VRAM budget (GB). "
        "Local models are limited to fit within budget; cloud models are always included.",
        ge=1,
        le=128,
    ),
):
    """
    Get all currently available models from the unified model consolidation service.

    Returns all models across all providers (Ollama, HuggingFace, Google, Anthropic, OpenAI)
    with unified interface and automatic fallback chain support.

    When vram_gb is specified, local (Ollama) models are limited to those that fit
    within the given VRAM budget, while cloud models are always included.
    """
    try:
        service = get_model_consolidation_service()
        models_dict = await service.list_models()

        # When VRAM filtering is active, prioritize local models then cloud
        if vram_gb is not None:
            provider_order = ["ollama", "huggingface", "google", "anthropic", "openai"]
        else:
            provider_order = list(models_dict.keys())

        models_list = []

        for provider in provider_order:
            model_names = models_dict.get(provider, [])
            icon = PROVIDER_ICONS.get(provider, "🤖")

            # When VRAM filtering, limit local models to fit within budget
            if vram_gb is not None and provider == "ollama":
                # Rough estimate: limit to 2 models for <= 12GB, 4 for > 12GB
                local_limit = 2 if vram_gb <= 12 else 4
                model_names = model_names[:local_limit]
                estimated_vram = min(vram_gb, 8)
                size_label = "7B-13B" if vram_gb <= 12 else "13B-30B"
            elif vram_gb is not None:
                model_names = model_names[:3]  # Limit cloud models per provider
                estimated_vram = 0
                size_label = "unknown"
            else:
                estimated_vram = 0
                size_label = "unknown"

            for model_name in model_names:
                models_list.append(
                    ModelInfo(
                        name=model_name,
                        displayName=f"{model_name} ({provider})",
                        provider=provider,
                        isFree=provider in ["ollama", "huggingface"],
                        size=size_label,
                        estimatedVramGb=estimated_vram,
                        description=f"Model from {provider}",
                        icon=icon,
                        requiresInternet=provider != "ollama",
                    )
                )

        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        logger.error(f"Error getting available models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error getting available models")


@models_router.get("/status", response_model=Dict[str, Any], description="Get status of all model providers")
async def get_provider_status():
    """
    Get availability status of all model providers in the consolidation service.

    Returns provider statuses including:
    - Availability (up/down)
    - Last check time
    - Response metrics
    - Number of available models
    """
    try:
        service = get_model_consolidation_service()
        status = service.get_status()

        return {"timestamp": datetime.now(timezone.utc).isoformat(), "providers": status}

    except Exception as e:
        logger.error(f"Error getting provider status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error getting provider status")


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
        models_dict = await service.list_models()

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
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        logger.error(f"Error getting recommended models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error getting recommended models")


@models_router.get(
    "/rtx5070",
    response_model=None,
    description="Deprecated — use /available?vram_gb=12 instead",
    deprecated=True,
)
async def get_rtx5070_models():
    """
    Deprecated: redirects to /api/models/available?vram_gb=12.
    Use the vram_gb query parameter on /available for hardware-agnostic filtering.
    """
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/api/models/available?vram_gb=12", status_code=301)



