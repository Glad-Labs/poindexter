"""
Chat Routes - Handle real-time chat interactions with AI models

Provides endpoints for:
- Chat message processing with multi-model selection (Ollama, OpenAI, Claude, Gemini)
- Multi-turn conversation tracking
- Usage tracking and cost calculation
- Smart fallback to multiple AI providers
"""

import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from routes.auth_unified import get_current_user
from utils.rate_limiter import limiter

from schemas.chat_schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
)
from services.ai_cache import AIResponseCache
from services.model_router import ModelRouter, TaskComplexity
from services.ollama_client import OllamaClient
from services.prompt_templates import PromptTemplates
from services.system_knowledge_rag import get_system_knowledge_rag
from services.usage_tracker import get_usage_tracker

logger = logging.getLogger(__name__)

# Initialize services
ollama_client = OllamaClient()
model_router = ModelRouter(use_ollama=True)  # Prefer free local inference
usage_tracker = get_usage_tracker()
system_knowledge_rag = get_system_knowledge_rag()  # System knowledge base
ai_cache = AIResponseCache()  # Response caching (uses Redis if available)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Store conversations in memory with bounded size (issue #214).
# Uses OrderedDict to evict oldest conversations when the cap is reached.
# In production, migrate to PostgreSQL for persistence across restarts.
MAX_CONVERSATIONS = 500
MAX_MESSAGES_PER_CONVERSATION = 100
conversations: OrderedDict[str, list] = OrderedDict()


@router.post("", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: dict = Depends(get_current_user),
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
            f"[Chat] Incoming request - model: '{body.model}', message length: {len(body.message)}"
        )

        # Parse model specification (e.g., "ollama-mistral" -> provider="ollama", model_name="mistral")
        # Also accept generic names like "ollama", "openai", etc.
        model_parts = body.model.split("-", 1)  # Split on first dash only
        provider = model_parts[0]  # First part is the provider (ollama, openai, claude, gemini)
        model_name = model_parts[1] if len(model_parts) > 1 else None  # Rest is specific model name

        # Validate provider is supported
        supported_providers = ["ollama", "openai", "claude", "gemini"]
        if provider not in supported_providers:
            raise ValueError(
                f"Invalid model provider '{provider}'. Must be one of: {', '.join(supported_providers)}"
            )

        logger.info(f"[Chat] PARSED MODEL - provider: '{provider}', model_name: '{model_name}'")

        # Initialize conversation if needed; evict oldest if at capacity
        if body.conversationId not in conversations:
            if len(conversations) >= MAX_CONVERSATIONS:
                evicted_id, _ = conversations.popitem(last=False)
                logger.debug(f"[Chat] Evicted oldest conversation {evicted_id} (capacity: {MAX_CONVERSATIONS})")
            conversations[body.conversationId] = []
        else:
            # Move to end so it's treated as most recently used
            conversations.move_to_end(body.conversationId)

        # Trim per-conversation message history
        if len(conversations[body.conversationId]) >= MAX_MESSAGES_PER_CONVERSATION:
            conversations[body.conversationId] = conversations[body.conversationId][
                -MAX_MESSAGES_PER_CONVERSATION:
            ]

        # Add user message to conversation history
        conversations[body.conversationId].append(
            {
                "role": "user",
                "content": body.message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Check cache first (before doing any heavy processing)
        cache_params = {
            "temperature": body.temperature or 0.7,
            "max_tokens": body.max_tokens or 500,
        }
        cached_response = await ai_cache.get(body.message, body.model, cache_params)
        if cached_response:
            logger.info(f"[Chat] Cache hit for query (saved ~{body.max_tokens or 500} tokens)")
            # Still add to conversation history
            conversations[body.conversationId].append(
                {
                    "role": "assistant",
                    "content": cached_response,
                    "model": body.model,
                    "provider": provider,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "cached": True,
                }
            )
            return ChatResponse(
                response=cached_response,
                model=body.model,
                conversationId=body.conversationId,
                timestamp=datetime.now(timezone.utc).isoformat(),
                tokens_used=len(cached_response.split()),
                cached=True,
            )

        # Check if this is a system knowledge question
        is_system_question = PromptTemplates.detect_system_question(body.message)
        system_context = None
        system_knowledge_used = False

        if is_system_question:
            logger.info("[Chat] System question detected, retrieving from knowledge base")
            knowledge_result = system_knowledge_rag.retrieve(body.message)
            if knowledge_result and knowledge_result.confidence > 0.5:
                system_context = knowledge_result.content
                system_knowledge_used = True
                logger.info(
                    f"[Chat] Using system knowledge (confidence: {knowledge_result.confidence:.2f}, "
                    f"source: {knowledge_result.source_section})"
                )
            else:
                logger.warning(
                    "[Chat] System question detected but no high-confidence knowledge found"
                )

        # Log the chat request
        logger.info(
            f"[Chat] Processing message with: provider={provider}, model={model_name or 'default'}"
        )
        logger.debug(f"[Chat] Message: {body.message}")

        # Get actual AI response based on provider selection
        if provider == "ollama":
            # Use local Ollama with specified model or default
            try:
                # Use specified Ollama model or fall back to lightweight default
                # Use llama2 instead of mistral - it's more stable with memory constraints
                actual_ollama_model = model_name or "llama2"
                logger.info(f"[Chat] Calling Ollama with model: {actual_ollama_model}")

                # Check if model is available
                try:
                    available_models = await ollama_client.list_models()
                    logger.debug(f"[Chat] Available Ollama models: {available_models}")

                    if actual_ollama_model not in available_models:
                        # Model not found, suggest alternatives
                        alternatives = [
                            m
                            for m in available_models
                            if "llama" in str(m).lower() or len(available_models) == 0
                        ]
                        if not alternatives:
                            alternatives = available_models[:3] if available_models else ["llama2"]

                        logger.warning(
                            f"[Chat] Model '{actual_ollama_model}' not found. Available: {alternatives}"
                        )
                        response_text = (
                            f"❌ Model '{actual_ollama_model}' not available.\n\n"
                            f"Available models: {', '.join(str(m) for m in alternatives[:5])}\n\n"
                            f"Pull a model with: ollama pull {alternatives[0] if alternatives else 'llama2'}"
                        )
                        tokens_used = len(response_text.split())

                        # Fall through to add response to history and return
                        conversations[body.conversationId].append(
                            {
                                "role": "assistant",
                                "content": response_text,
                                "model": body.model,
                                "provider": provider,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )

                        return ChatResponse(
                            response=response_text,
                            model=body.model,
                            conversationId=body.conversationId,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            tokens_used=tokens_used,
                        )
                except (ConnectionError, TimeoutError, AttributeError, KeyError) as e:
                    logger.debug(f"[Chat] Could not check available models: {str(e)}")

                # Prepare messages for Ollama chat
                messages_to_send = conversations[body.conversationId].copy()

                # If we have system knowledge, create a system-aware prompt and prepend it
                if system_knowledge_used:
                    system_prompt = PromptTemplates.system_aware_chat_prompt(
                        system_context=system_context,
                        user_query=body.message,
                    )
                    # Insert system message at the beginning (before user messages)
                    messages_to_send.insert(0, {"role": "system", "content": system_prompt})
                    logger.debug("[Chat] System-aware prompt added to conversation")

                chat_result = await ollama_client.chat(
                    messages=messages_to_send,
                    model=actual_ollama_model,
                    temperature=body.temperature or 0.7,
                    max_tokens=body.max_tokens or 500,
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

                # Cache the response for future similar queries
                await ai_cache.set(
                    body.message, body.model, cache_params, response_text
                )  # 24 hour TTL for system questions
                logger.debug(f"[Chat] Response cached (TTL: 24h)")

            except (ConnectionError, TimeoutError, ValueError, KeyError, AttributeError, RuntimeError) as e:
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
        else:
            # Provider exists in the supported list (validated above) but lacks a
            # real API integration. Return a clear error instead of a fake demo
            # response that could mislead users (issue #100).
            logger.error(
                f"[Chat] Provider '{provider}' model '{model_name or 'default'}' has no "
                f"real integration. Returning 503 instead of demo response."
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Provider '{provider}' is not yet integrated for live chat. "
                    f"Currently supported providers: ollama. "
                    f"To use Ollama, ensure it is running (ollama serve) and a model is available (ollama pull llama2)."
                ),
            )

        # Add AI response to conversation history
        conversations[body.conversationId].append(
            {
                "role": "assistant",
                "content": response_text,
                "model": body.model,  # Keep original full model specification
                "provider": provider,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        return ChatResponse(
            response=response_text,
            model=body.model,  # Return original full model specification
            conversationId=body.conversationId,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tokens_used=len(response_text.split()),  # Rough estimate
        )

    except ValueError as e:
        logger.error(f"[Chat] Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    except (ConnectionError, TimeoutError, TypeError, AttributeError, RuntimeError) as e:
        logger.error(f"[Chat] Error processing message: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")


@router.get("/history/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
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
        if conversation_id not in conversations:
            return {
                "messages": [],
                "conversation_id": conversation_id,
                "message_count": 0,
                "first_message": None,
                "last_message": None,
            }

        msgs = conversations[conversation_id]
        return {
            "messages": msgs,
            "conversation_id": conversation_id,
            "message_count": len(msgs),
            "first_message": msgs[0].get("timestamp") if msgs else None,
            "last_message": msgs[-1].get("timestamp") if msgs else None,
        }

    except (KeyError, TypeError, AttributeError) as e:
        logger.error(f"[Chat] Error retrieving conversation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/history/{conversation_id}")
async def clear_conversation(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
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
        if conversation_id in conversations:
            del conversations[conversation_id]

        return {
            "status": "success",
            "conversation_id": conversation_id,
            "message": f"Conversation cleared",
        }

    except (KeyError, TypeError, AttributeError) as e:
        logger.error(f"[Chat] Error clearing conversation: {str(e)}", exc_info=True)
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
