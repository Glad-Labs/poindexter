"""AI Model Management Models

Consolidated schemas for model availability and provider status.
"""

from pydantic import BaseModel, Field
from typing import List, Optional


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
