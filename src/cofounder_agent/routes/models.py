"""
Model Management Routes for Cofounder Agent

Provides endpoints for:
- Listing available AI models
- Getting provider status
- Model recommendations
"""

import os
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from services.llm_provider_manager import get_llm_manager

logger = logging.getLogger(__name__)

# Router for all model-related endpoints
models_router = APIRouter(prefix="/api/v1/models", tags=["models"])


class ModelInfo(BaseModel):
    """Model information for frontend"""
    name: str
    displayName: str
    provider: str
    isFree: bool
    size: str
    estimatedVramGb: float
    description: str
    icon: str
    requiresInternet: bool


class ModelsListResponse(BaseModel):
    """Response with list of available models"""
    models: List[ModelInfo]
    total: int
    timestamp: str


class ProviderStatus(BaseModel):
    """Status of an LLM provider"""
    available: bool
    url: Optional[str] = None
    hasToken: bool = False
    hasKey: bool = False
    models: int = 0


class ProvidersStatusResponse(BaseModel):
    """Response with all provider statuses"""
    ollama: ProviderStatus
    huggingface: ProviderStatus
    gemini: ProviderStatus
    timestamp: str


@models_router.get(
    "/available",
    response_model=ModelsListResponse,
    description="Get list of available AI models"
)
async def get_available_models():
    """
    Get all currently available models based on provider configuration.
    
    Returns models sorted by:
    1. Local models first (Ollama)
    2. Free models before paid
    3. By model size
    """
    try:
        manager = get_llm_manager()
        available_models = manager.get_available_models()
        
        models_list = []
        for model_name, config in available_models.items():
            models_list.append(ModelInfo(
                name=model_name,
                displayName=config.model_display_name,
                provider=config.provider.value,
                isFree=config.is_free,
                size=config.size.value,
                estimatedVramGb=config.estimated_vram_gb,
                description=config.description,
                icon="🖥️" if config.provider.value == "ollama" else ("🌐" if config.provider.value == "huggingface" else "☁️"),
                requiresInternet=config.requires_internet,
            ))
        
        # Sort models (recommended order)
        recommended = manager.get_recommended_models()
        recommended_names = [m.model_name for m in recommended]
        
        def sort_key(model):
            try:
                return recommended_names.index(model.name)
            except ValueError:
                return len(recommended_names)
        
        models_list.sort(key=sort_key)
        
        from datetime import datetime
        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )
    
    except Exception as e:
        logger.error(f"Error getting available models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting available models: {str(e)}")


@models_router.get(
    "/status",
    response_model=ProvidersStatusResponse,
    description="Get status of all LLM providers"
)
async def get_provider_status():
    """
    Get availability status of all LLM providers.
    
    Shows which providers are available and configured.
    Useful for debugging and monitoring model availability.
    """
    try:
        manager = get_llm_manager()
        status = manager.get_provider_status()
        
        from datetime import datetime
        return ProvidersStatusResponse(
            ollama=ProviderStatus(
                available=status["ollama"]["available"],
                url=status["ollama"].get("url"),
                models=status["ollama"]["models"],
            ),
            huggingface=ProviderStatus(
                available=status["huggingface"]["available"],
                hasToken=status["huggingface"]["has_token"],
                models=status["huggingface"]["models"],
            ),
            gemini=ProviderStatus(
                available=status["gemini"]["available"],
                hasKey=status["gemini"]["has_key"],
                models=status["gemini"]["models"],
            ),
            timestamp=datetime.now().isoformat(),
        )
    
    except Exception as e:
        logger.error(f"Error getting provider status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting provider status: {str(e)}")


@models_router.get(
    "/recommended",
    response_model=ModelsListResponse,
    description="Get recommended models for current environment"
)
async def get_recommended_models():
    """
    Get models recommended for the current environment.
    
    Recommendations based on:
    1. Available resources (RTX 5070 = 12GB VRAM)
    2. Cost efficiency (prefers free/local)
    3. Quality (balances speed vs quality)
    """
    try:
        manager = get_llm_manager()
        recommended = manager.get_recommended_models()
        
        models_list = []
        for config in recommended:
            models_list.append(ModelInfo(
                name=config.model_name,
                displayName=config.model_display_name,
                provider=config.provider.value,
                isFree=config.is_free,
                size=config.size.value,
                estimatedVramGb=config.estimated_vram_gb,
                description=config.description,
                icon="🖥️" if config.provider.value == "ollama" else ("🌐" if config.provider.value == "huggingface" else "☁️"),
                requiresInternet=config.requires_internet,
            ))
        
        from datetime import datetime
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
    description="Get models optimized for RTX 5070 (12GB VRAM)"
)
async def get_rtx5070_models():
    """
    Get models that fit within RTX 5070's 12GB VRAM.
    
    RTX 5070 is a great mid-range GPU that can run many 7B-13B models
    efficiently for local AI inference.
    """
    try:
        manager = get_llm_manager()
        available = manager.get_available_models()
        
        # Filter models for RTX 5070 (12GB VRAM)
        rtx_models = []
        for config in available.values():
            if config.provider.value == "ollama" and config.estimated_vram_gb <= 12:
                rtx_models.append(config)
            elif config.provider.value != "ollama":
                # Cloud models don't use local VRAM
                rtx_models.append(config)
        
        models_list = []
        for config in rtx_models:
            models_list.append(ModelInfo(
                name=config.model_name,
                displayName=config.model_display_name,
                provider=config.provider.value,
                isFree=config.is_free,
                size=config.size.value,
                estimatedVramGb=config.estimated_vram_gb,
                description=config.description,
                icon="🖥️" if config.provider.value == "ollama" else ("🌐" if config.provider.value == "huggingface" else "☁️"),
                requiresInternet=config.requires_internet,
            ))
        
        from datetime import datetime
        return ModelsListResponse(
            models=models_list,
            total=len(models_list),
            timestamp=datetime.now().isoformat(),
        )
    
    except Exception as e:
        logger.error(f"Error getting RTX5070 models: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting RTX5070 models: {str(e)}")
