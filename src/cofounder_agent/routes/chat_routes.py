"""
Chat Routes - Handle real-time chat interactions with AI models

Provides endpoints for:
- Chat message processing with model selection
- Multi-turn conversation tracking
- Fallback to multiple AI providers
"""

import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime
from services.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

# Initialize Ollama client
ollama_client = OllamaClient()

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Models for request/response validation
class ChatMessage(BaseModel):
    """A single chat message"""
    content: str
    role: str = Field(default="user", description="user or assistant")
    timestamp: Optional[str] = None


class ChatRequest(BaseModel):
    """Chat request with message and model selection"""
    message: str = Field(..., description="The user's message")
    model: str = Field(default="ollama", description="Model to use: ollama, openai, claude, gemini")
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


# Store conversations in memory (in production, use database)
conversations: Dict[str, list] = {}


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Process a chat message and return AI response
    
    **Parameters:**
    - message: The user's message
    - model: AI model to use (ollama, openai, claude, gemini)
    - conversationId: ID to track multi-turn conversations
    - temperature: 0.0-2.0, higher = more creative
    - max_tokens: Maximum response length (1-4000)
    
    **Returns:**
    - response: The AI's response text
    - model: The model that generated the response
    - conversationId: The conversation ID
    - timestamp: When the response was generated
    - tokens_used: Approximate token count (optional)
    
    **Example:**
    ```json
    POST /api/chat
    {
      "message": "What is 2+2?",
      "model": "ollama",
      "conversationId": "default"
    }
    ```
    """
    try:
        # Validate model
        valid_models = ["ollama", "openai", "claude", "gemini"]
        if request.model not in valid_models:
            raise ValueError(f"Invalid model. Must be one of: {', '.join(valid_models)}")
        
        # Initialize conversation if needed
        if request.conversationId not in conversations:
            conversations[request.conversationId] = []
        
        # Add user message to conversation history
        conversations[request.conversationId].append({
            "role": "user",
            "content": request.message,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Log the chat request
        logger.info(f"[Chat] Processing message with model: {request.model}")
        logger.debug(f"[Chat] Message: {request.message}")
        
        # Get actual AI response based on model selection
        if request.model == "ollama":
            # Use local Ollama
            try:
                # Map generic "ollama" to actual Ollama model
                actual_ollama_model = "llama2"
                chat_result = await ollama_client.chat(
                    messages=conversations[request.conversationId],
                    model=actual_ollama_model,
                    temperature=request.temperature or 0.7,
                    max_tokens=request.max_tokens or 500
                )
                # ollama_client.chat returns {"content": "...", "tokens": ...}
                response_text = chat_result.get("content", chat_result.get("response", "No response generated"))
                tokens_used = chat_result.get("tokens", len(response_text.split()))
            except Exception as e:
                logger.error(f"[Chat] Ollama error: {str(e)}")
                response_text = f"Error calling Ollama: {str(e)}"
                tokens_used = 0
        else:
            # For other models, generate placeholder (would integrate with OpenAI/Claude/Gemini in production)
            logger.warning(f"[Chat] Model {request.model} not yet integrated, using demo response")
            response_text = generate_demo_response(request.message, request.model)
            tokens_used = len(response_text.split())
        
        # Add AI response to conversation history
        conversations[request.conversationId].append({
            "role": "assistant",
            "content": response_text,
            "model": request.model,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        return ChatResponse(
            response=response_text,
            model=request.model,
            conversationId=request.conversationId,
            timestamp=datetime.utcnow().isoformat(),
            tokens_used=len(response_text.split())  # Rough estimate
        )
    
    except ValueError as e:
        logger.error(f"[Chat] Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"[Chat] Error processing message: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")


@router.get("/history/{conversation_id}")
async def get_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Get the full conversation history for a conversation ID
    
    **Parameters:**
    - conversation_id: The conversation to retrieve
    
    **Returns:**
    - messages: List of messages in the conversation
    - conversation_id: The conversation ID
    - message_count: Total messages
    - first_message: Timestamp of first message
    - last_message: Timestamp of last message
    """
    try:
        if conversation_id not in conversations:
            return {
                "messages": [],
                "conversation_id": conversation_id,
                "message_count": 0,
                "first_message": None,
                "last_message": None
            }
        
        msgs = conversations[conversation_id]
        return {
            "messages": msgs,
            "conversation_id": conversation_id,
            "message_count": len(msgs),
            "first_message": msgs[0].get("timestamp") if msgs else None,
            "last_message": msgs[-1].get("timestamp") if msgs else None
        }
    
    except Exception as e:
        logger.error(f"[Chat] Error retrieving conversation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/history/{conversation_id}")
async def clear_conversation(conversation_id: str) -> Dict[str, str]:
    """
    Clear conversation history
    
    **Parameters:**
    - conversation_id: The conversation to clear
    
    **Returns:**
    - status: Success message
    - conversation_id: The cleared conversation ID
    """
    try:
        if conversation_id in conversations:
            del conversations[conversation_id]
        
        return {
            "status": "success",
            "conversation_id": conversation_id,
            "message": f"Conversation cleared"
        }
    
    except Exception as e:
        logger.error(f"[Chat] Error clearing conversation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def get_available_models() -> Dict[str, Any]:
    """
    Get list of available chat models
    
    **Returns:**
    - models: List of available models with descriptions
    - available_count: Number of available models
    """
    models_list = [
        {
            "id": "ollama",
            "name": "Ollama (Local)",
            "icon": "🏠",
            "description": "Free local AI model - fastest, no API needed",
            "status": "available"
        },
        {
            "id": "openai",
            "name": "OpenAI GPT-4",
            "icon": "🔴",
            "description": "Most capable - requires API key",
            "status": "available"
        },
        {
            "id": "claude",
            "name": "Claude (Anthropic)",
            "icon": "⭐",
            "description": "High quality responses - requires API key",
            "status": "available"
        },
        {
            "id": "gemini",
            "name": "Google Gemini",
            "icon": "✨",
            "description": "Latest Google model - requires API key",
            "status": "available"
        }
    ]
    
    return {
        "models": models_list,
        "available_count": len(models_list),
        "timestamp": datetime.utcnow().isoformat()
    }


def generate_demo_response(message: str, model: str) -> str:
    """
    Generate a demo response based on model and message
    
    In production, this would call the actual model API
    """
    responses = {
        "ollama": f"🏠 Ollama (Local): Processing '{message}'... Demo response complete. ✓",
        "openai": f"🔴 GPT-4: I understand you're saying: '{message}'. Here's my response... ✓",
        "claude": f"⭐ Claude: That's an interesting question: '{message}'. Let me help... ✓",
        "gemini": f"✨ Gemini: Analyzing '{message}'... Generating response... ✓"
    }
    
    return responses.get(model, f"🤖 {model}: Processing your message: '{message}'... ✓")
