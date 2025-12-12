"""Chat Message and Request/Response Models

Consolidated schemas for chat interactions.
"""

from pydantic import BaseModel, Field
from typing import Optional


class ChatMessage(BaseModel):
    """A single chat message"""
    content: str
    role: str = Field(default="user", description="user or assistant")
    timestamp: Optional[str] = None


class ChatRequest(BaseModel):
    """Chat request with message and model selection"""
    message: str = Field(..., description="The user's message")
    model: str = Field(default="ollama", description="Model to use: ollama (or ollama-modelname), openai, claude, gemini, etc.")
    conversationId: str = Field(default="default", description="Conversation ID for multi-turn context")
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=500, ge=1, le=4000)


class ChatResponse(BaseModel):
    """Chat response with model info"""
    response: str
    model: str
    conversationId: str
    timestamp: str
    tokens_used: Optional[int] = None
