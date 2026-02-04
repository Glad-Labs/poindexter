"""Ollama Integration Models

Consolidated schemas for Ollama model management and health checks.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


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
