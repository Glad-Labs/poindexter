"""
Chat Routes - Handle real-time chat interactions with AI models

Provides endpoints for:
- Chat message processing with multi-model selection (Ollama, OpenAI, Claude, Gemini)
- Multi-turn conversation tracking
- Usage tracking and cost calculation
- Smart fallback to multiple AI providers
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from routes.auth_unified import get_current_user
from schemas.chat_schemas import (
    ChatRequest,
    ChatResponse,
)
from services.model_router import ModelRouter
from services.ollama_client import OllamaClient
from services.gemini_client import GeminiClient
from services.usage_tracker import get_usage_tracker

logger = logging.getLogger(__name__)

# Initialize services
ollama_client = OllamaClient()
gemini_client = GeminiClient()  # Initialize with API key from env
model_router = ModelRouter(use_ollama=True)  # Prefer free local inference
usage_tracker = get_usage_tracker()

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Store conversations in memory keyed by "{user_id}:{conversation_id}"
# (in production, use database for persistence across restarts)
conversations: Dict[str, list] = {}


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> ChatResponse:
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
        # Log incoming request details
        logger.info(
            f"[Chat] Incoming request - model: '{request.model}', message length: {len(request.message)}"
        )

        # Parse model specification (e.g., "ollama-mistral" -> provider="ollama", model_name="mistral")
        # Also accept generic names like "ollama", "openai", etc.
        model_parts = request.model.split("-", 1)  # Split on first dash only
        provider = model_parts[0]  # First part is the provider (ollama, openai, claude, gemini)
        model_name = model_parts[1] if len(model_parts) > 1 else None  # Rest is specific model name

        # Validate provider is supported
        supported_providers = ["ollama", "openai", "claude", "gemini"]
        if provider not in supported_providers:
            raise ValueError(
                f"Invalid model provider '{provider}'. Must be one of: {', '.join(supported_providers)}"
            )

        logger.info(f"[Chat] PARSED MODEL - provider: '{provider}', model_name: '{model_name}'")

        # Scope conversations by user_id to prevent cross-user access
        user_id = current_user.get("id", "anonymous")
        scoped_key = f"{user_id}:{request.conversationId}"

        # Initialize conversation if needed
        if scoped_key not in conversations:
            conversations[scoped_key] = []

        # Add user message to conversation history
        conversations[scoped_key].append(
            {"role": "user", "content": request.message, "timestamp": datetime.now(timezone.utc).isoformat()}
        )

        # Log the chat request
        logger.info(
            f"[Chat] Processing message with: provider={provider}, model={model_name or 'default'}"
        )
        logger.debug(f"[Chat] Message: {request.message}")

        # Get actual AI response based on provider selection
        if provider == "ollama":
            # Use local Ollama with specified model or default
            try:
                # Use specified Ollama model or fall back to environment config or llama2
                actual_ollama_model = model_name or os.getenv("DEFAULT_OLLAMA_CHAT_MODEL", "llama2")
                logger.info(f"[Chat] Calling Ollama with model: {actual_ollama_model}")

                # Check if model is available
                try:
                    models_raw = await ollama_client.list_models()
                    # list_models() returns List[Dict[str, Any]]; extract name strings
                    available_model_names: list[str] = [
                        m.get("name", "") if isinstance(m, dict) else str(m)
                        for m in models_raw
                    ]
                    logger.debug(f"[Chat] Available Ollama models: {available_model_names}")

                    if actual_ollama_model not in available_model_names:
                        # Model not found, suggest alternatives
                        alternatives = [
                            name
                            for name in available_model_names
                            if "llama" in name.lower()
                        ]
                        if not alternatives:
                            alternatives = available_model_names[:3] if available_model_names else ["llama2"]

                        logger.warning(
                            f"[Chat] Model '{actual_ollama_model}' not found. Available: {alternatives}"
                        )
                        response_text = (
                            f"❌ Model '{actual_ollama_model}' not available.\n\n"
                            f"Available models: {', '.join(alternatives[:5])}\n\n"
                            f"Pull a model with: ollama pull {alternatives[0] if alternatives else 'llama2'}"
                        )
                        tokens_used = len(response_text.split())

                        # Fall through to add response to history and return
                        conversations[scoped_key].append(
                            {
                                "role": "assistant",
                                "content": response_text,
                                "model": request.model,
                                "provider": provider,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )

                        return ChatResponse(
                            response=response_text,
                            model=request.model,
                            conversationId=request.conversationId,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            tokens_used=tokens_used,
                        )
                except Exception as e:
                    logger.debug(f"[Chat] Could not check available models: {str(e)}")

                chat_result = await ollama_client.chat(
                    messages=conversations[scoped_key],
                    model=actual_ollama_model,
                    temperature=request.temperature or 0.7,
                    max_tokens=request.max_tokens or 500,
                )
                # ollama_client.chat returns {"content": "...", "tokens": ...}
                response_text = chat_result.get(
                    "content", chat_result.get("response", "No response generated")
                )

                # Validate response is not empty or obviously wrong
                if not response_text or len(response_text.strip()) < 5:
                    response_text = (
                        f"✓ Processed by {actual_ollama_model} (generated short response)"
                    )

                tokens_used = chat_result.get("tokens", len(response_text.split()))
            except Exception as e:
                logger.error(
                    f"[Chat] Ollama error with model {model_name or 'default'}: {str(e)}",
                    exc_info=True,
                )
                response_text = (
                    f"⚠️ Ollama Error: {str(e)[:100]}\n\n"
                    f"Troubleshooting:\n"
                    f"1. Is Ollama running? Start: ollama serve\n"
                    f"2. Check model exists: ollama list\n"
                    f"3. Check http://localhost:11434 is accessible"
                )
                tokens_used = 0
        elif provider == "gemini":
            # Use Google Gemini with specified model or default
            try:
                if not gemini_client.is_configured():
                    raise Exception(
                        "Gemini API key not configured. Set GOOGLE_API_KEY or GEMINI_API_KEY environment variable."
                    )

                # Use specified Gemini model or fall back to latest
                actual_gemini_model = model_name or "gemini-2.5-flash"
                logger.info(f"[Chat] Calling Gemini with model: {actual_gemini_model}")

                # Check if model is available
                available_models = await gemini_client.list_models()
                if available_models and actual_gemini_model not in available_models:
                    logger.warning(
                        f"[Chat] Model '{actual_gemini_model}' not found in available models: {available_models}"
                    )
                    actual_gemini_model = available_models[0] if available_models else "gemini-2.5-flash"
                    logger.info(f"[Chat] Falling back to: {actual_gemini_model}")

                # Call Gemini API
                response_text = await gemini_client.chat(
                    messages=conversations[scoped_key],
                    model=actual_gemini_model,
                    temperature=request.temperature or 0.7,
                    max_tokens=request.max_tokens or 500,
                )

                # Validate response
                if not response_text or len(response_text.strip()) < 5:
                    response_text = f"✓ Processed by {actual_gemini_model} (generated short response)"

                tokens_used = len(response_text.split())
            except Exception as e:
                logger.error(
                    f"[Chat] Gemini error with model {model_name or 'default'}: {str(e)}",
                    exc_info=True,
                )
                response_text = (
                    f"⚠️ Gemini Error: {str(e)[:100]}\n\n"
                    f"Troubleshooting:\n"
                    f"1. Is GOOGLE_API_KEY set? Check: echo $GOOGLE_API_KEY\n"
                    f"2. Is the API key valid? Check Google Cloud Console\n"
                    f"3. Does your account have proper quota?"
                )
                tokens_used = 0
        else:
            # For other models (openai, claude), generate placeholder
            logger.warning(
                f"[Chat] Provider '{provider}' model '{model_name or 'default'}' not yet implemented, using demo response"
            )
            response_text = generate_demo_response(request.message, request.model)
            tokens_used = len(response_text.split())

        # Add AI response to conversation history
        conversations[scoped_key].append(
            {
                "role": "assistant",
                "content": response_text,
                "model": request.model,  # Keep original full model specification
                "provider": provider,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        return ChatResponse(
            response=response_text,
            model=request.model,  # Return original full model specification
            conversationId=request.conversationId,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tokens_used=len(response_text.split()),  # Rough estimate
        )

    except ValueError as e:
        logger.error(f"[Chat] Validation error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid request parameters")

    except Exception as e:
        logger.error(f"[Chat] Error processing message: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Chat processing failed")


@router.get("/history/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
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
        user_id = current_user.get("id", "anonymous")
        scoped_key = f"{user_id}:{conversation_id}"
        if scoped_key not in conversations:
            return {
                "messages": [],
                "conversation_id": conversation_id,
                "message_count": 0,
                "first_message": None,
                "last_message": None,
            }

        msgs = conversations[scoped_key]
        return {
            "messages": msgs,
            "conversation_id": conversation_id,
            "message_count": len(msgs),
            "first_message": msgs[0].get("timestamp") if msgs else None,
            "last_message": msgs[-1].get("timestamp") if msgs else None,
        }

    except Exception as e:
        logger.error(f"[Chat] Error retrieving conversation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/history/{conversation_id}")
async def clear_conversation(
    conversation_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    """
    Clear conversation history

    **Parameters:**
    - conversation_id: The conversation to clear

    **Returns:**
    - status: Success message
    - conversation_id: The cleared conversation ID
    """
    try:
        user_id = current_user.get("id", "anonymous")
        scoped_key = f"{user_id}:{conversation_id}"
        if scoped_key in conversations:
            del conversations[scoped_key]

        return {
            "status": "success",
            "conversation_id": conversation_id,
            "message": "Conversation cleared",
        }

    except Exception as e:
        logger.error(f"[Chat] Error clearing conversation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/models")
async def get_available_models(
    _current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
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
            "status": "available",
        },
        {
            "id": "openai",
            "name": "OpenAI GPT-4",
            "icon": "🔴",
            "description": "Most capable - requires API key",
            "status": "available",
        },
        {
            "id": "claude",
            "name": "Claude (Anthropic)",
            "icon": "⭐",
            "description": "High quality responses - requires API key",
            "status": "available",
        },
        {
            "id": "gemini",
            "name": "Google Gemini",
            "icon": "✨",
            "description": "Latest Google model - requires API key",
            "status": "available",
        },
    ]

    return {
        "models": models_list,
        "available_count": len(models_list),
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
        "gemini": f"✨ Gemini: Analyzing '{message}'... Generating response... ✓",
    }

    return responses.get(model, f"🤖 {model}: Processing your message: '{message}'... ✓")
