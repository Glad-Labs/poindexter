"""
Unified Model Consolidation Service

Routes LLM requests through a unified interface with automatic fallback.
Policy: Ollama-only (local, zero-cost). HuggingFace kept as emergency fallback.
Paid APIs (Anthropic, OpenAI, Gemini) removed per no-paid-APIs policy.

Fallback Chain:
1. Ollama (local, zero-cost) ← Primary and only provider in normal operation
2. HuggingFace (free tier) ← Emergency fallback if Ollama is down

Usage:
    service = get_model_consolidation_service()
    response = await service.generate(
        prompt="Generate a blog post...",
        max_tokens=2000,
        temperature=0.7
    )
"""

import asyncio
import os
import httpx
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger

from .error_handler import ServiceError
from .provider_checker import ProviderChecker

logger = get_logger(__name__)


# ============================================================================
# ENUMS & TYPES
# ============================================================================


class ProviderType(str, Enum):
    """Available model providers (Ollama-only policy; HuggingFace as emergency fallback)"""

    OLLAMA = "ollama"  # Local, zero-cost — primary and only provider
    HUGGINGFACE = "huggingface"  # Free tier, rate limited — emergency fallback


@dataclass
class ProviderStatus:
    """Status of a model provider"""

    provider: ProviderType
    is_available: bool
    last_checked: datetime
    last_error: Optional[str] = None
    response_time_ms: float = 0.0

    @property
    def cache_expired(self) -> bool:
        """Check if cache should be refreshed (30s TTL for failures, 5 min for success)"""
        now = datetime.now(timezone.utc)
        ttl = timedelta(minutes=5) if self.is_available else timedelta(seconds=30)
        return now - self.last_checked > ttl


@dataclass
class ModelResponse:
    """Unified model response"""

    text: str
    provider: ProviderType
    model: str
    tokens_used: int
    cost: float
    response_time_ms: float


# ============================================================================
# PROVIDER ADAPTERS (Abstract + Implementations)
# ============================================================================


class ProviderAdapter(ABC):
    """Abstract base class for model provider adapters"""

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if provider is operational"""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs,
    ) -> ModelResponse:
        """Generate text using this provider"""

    @abstractmethod
    def list_models(self) -> List[str]:
        """List available models"""


class OllamaAdapter(ProviderAdapter):
    """Adapter for Ollama local model provider"""

    def __init__(self):
        from .ollama_client import OllamaClient

        from services.site_config import site_config
        self.host = site_config.get("ollama_base_url") or site_config.get("ollama_host", "http://host.docker.internal:11434")
        self.client = OllamaClient(base_url=self.host)
        self.provider_type = ProviderType.OLLAMA

    async def is_available(self) -> bool:
        """Check if Ollama service is running"""
        try:
            # Try shared client first, fall back to fresh client if closed
            try:
                response = await self.client.client.get(
                    f"{self.host}/api/tags", timeout=3.0
                )
            except Exception:
                # Shared client may be closed — create a fresh one.
                # Explicit client-level timeout so a hung connect can't block
                # health checks even if the per-call timeout is missed.
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(3.0, connect=2.0)
                ) as client:
                    response = await client.get(
                        f"{self.host}/api/tags", timeout=3.0
                    )
            return response.status_code == 200
        except asyncio.TimeoutError:
            logger.debug("Ollama health check timed out (3s)", host=self.host)
            return False
        except Exception as e:
            logger.debug("Ollama unavailable", error=str(e), host=self.host)
            return False

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs,
    ) -> ModelResponse:
        """Generate text using Ollama"""
        if not model:
            try:
                from services.site_config import site_config
                model = site_config.get("default_ollama_model", "auto")
            except Exception:
                model = "auto"
        start_time = datetime.now(timezone.utc)

        try:
            response = await self.client.generate(
                prompt=prompt,
                model=model,
                stream=False,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            elapsed_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            tokens_used = response.get("prompt_eval_count", 0) + response.get("eval_count", 0)

            logger.info(
                "LLM call completed",
                provider="ollama",
                model=model,
                response_time_ms=elapsed_ms,
                tokens_used=tokens_used,
            )

            return ModelResponse(
                text=response.get("response", ""),
                provider=self.provider_type,
                model=model,
                tokens_used=tokens_used,
                cost=0.0,  # Ollama — electricity cost tracked separately via GPU metrics
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("Ollama generation failed", error=str(e), model=model, exc_info=True)
            raise

    async def list_models(self) -> List[str]:
        """List available Ollama models from live instance."""
        try:
            # Delegate to OllamaClient.list_models() which uses the shared
            # httpx.AsyncClient instead of creating one per call (#1326).
            models = await self.client.list_models()
            if models:
                return [m["name"] for m in models]
        except Exception as e:
            logger.warning("[OllamaAdapter] Failed to list models from %s: %s", self.host, e)
        # Fallback: models known to be installed
        return [
            "qwen3.5:35b",
            "qwen3:8b",
            "gemma3:27b",
        ]


class HuggingFaceAdapter(ProviderAdapter):
    """Adapter for HuggingFace Inference API"""

    def __init__(self):
        from .huggingface_client import HuggingFaceClient

        api_token = ProviderChecker.get_huggingface_api_key()
        self.client = HuggingFaceClient(api_token=api_token)
        self.provider_type = ProviderType.HUGGINGFACE

    async def is_available(self) -> bool:
        """Check if HuggingFace API is available"""
        return ProviderChecker.is_huggingface_available()

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs,
    ) -> ModelResponse:
        """Generate text using HuggingFace"""
        model = model or "mistralai/Mistral-7B-Instruct-v0.1"
        start_time = datetime.now(timezone.utc)

        try:
            response = await self.client.generate(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=kwargs.get("top_p", 0.9),
            )

            elapsed_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            tokens_used = len(prompt.split()) + len(response.split())  # Rough estimate

            logger.info(
                "LLM call completed",
                provider="huggingface",
                model=model,
                response_time_ms=elapsed_ms,
                tokens_used=tokens_used,
            )

            return ModelResponse(
                text=response,
                provider=self.provider_type,
                model=model,
                tokens_used=tokens_used,
                cost=0.0 if not api_token else 0.0001,  # Free tier or minimal cost
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning(
                "HuggingFace generation failed", error=str(e), model=model, exc_info=True
            )
            raise

    def list_models(self) -> List[str]:
        """List available HuggingFace models"""
        return [
            "mistralai/Mistral-7B-Instruct-v0.1",
            "meta-llama/Llama-2-7b-chat",
            "tiiuae/falcon-7b-instruct",
        ]



# NOTE: GoogleAdapter, AnthropicAdapter, OpenAIAdapter removed — Ollama-only policy.
# See git history (session 55+) for paid API adapter code if ever needed again.


# ============================================================================
# MAIN CONSOLIDATION SERVICE
# ============================================================================


class ModelConsolidationService:
    """
    Unified model service with fallback chain.

    Fallback Order (Ollama-only policy):
    1. Ollama (local, zero-cost) ← Primary and only provider
    2. HuggingFace (free tier) ← Emergency fallback
    """

    FALLBACK_CHAIN = [
        ProviderType.OLLAMA,
        ProviderType.HUGGINGFACE,
    ]

    def __init__(self):
        """Initialize all model providers"""
        self.adapters: Dict[ProviderType, ProviderAdapter] = {}
        self.provider_status: Dict[ProviderType, ProviderStatus] = {}
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_cost": 0.0,
            "by_provider": {},
        }

        # Initialize all adapters
        self._initialize_adapters()
        logger.info("Model consolidation service initialized")

    def _initialize_adapters(self):
        """Initialize provider adapters (Ollama + HuggingFace only)"""
        adapters_to_init = [
            (ProviderType.OLLAMA, OllamaAdapter),
            (ProviderType.HUGGINGFACE, HuggingFaceAdapter),
        ]

        for provider_type, adapter_class in adapters_to_init:
            try:
                adapter = adapter_class()
                self.adapters[provider_type] = adapter
                self.provider_status[provider_type] = ProviderStatus(
                    provider=provider_type,
                    is_available=False,
                    last_checked=datetime.now(timezone.utc),
                )
                logger.debug("Adapter initialized", provider=provider_type.value)
            except Exception as e:
                logger.warning(
                    "Failed to initialize adapter",
                    provider=provider_type.value,
                    error=str(e),
                    exc_info=True,
                )

    async def _check_provider_availability(self, provider_type: ProviderType) -> bool:
        """Check and cache provider availability"""
        status = self.provider_status.get(provider_type)

        # Return cached value if still valid
        if status and not status.cache_expired:
            return status.is_available

        # Check availability
        try:
            adapter = self.adapters.get(provider_type)
            if not adapter:
                return False

            is_available = await adapter.is_available()

            # Update status
            self.provider_status[provider_type] = ProviderStatus(
                provider=provider_type,
                is_available=is_available,
                last_checked=datetime.now(timezone.utc),
                last_error=None if is_available else "Not available",
            )

            return is_available
        except Exception as e:
            logger.warning(
                "Provider check failed", provider=provider_type.value, error=str(e), exc_info=True
            )

            self.provider_status[provider_type] = ProviderStatus(
                provider=provider_type,
                is_available=False,
                last_checked=datetime.now(timezone.utc),
                last_error=str(e),
            )

            return False

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        preferred_provider: Optional[ProviderType] = None,
        **kwargs,
    ) -> ModelResponse:
        """
        Generate text using fallback chain.

        Args:
            prompt: Input text prompt
            model: Specific model to use (optional)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0-1.0)
            preferred_provider: Try this provider first
            **kwargs: Additional arguments for providers

        Returns:
            ModelResponse with generated text and metadata

        Raises:
            Exception: If all providers fail
        """
        self.metrics["total_requests"] += 1

        # Build chain (preferred provider first)
        chain = []
        if preferred_provider:
            chain.append(preferred_provider)

        for provider in self.FALLBACK_CHAIN:
            if provider not in chain:
                chain.append(provider)

        # Try each provider in order
        last_error = None
        logger.info(
            "Starting provider fallback chain (%d providers to try)",
            len(chain),
            chain=[p.value for p in chain],
        )

        for provider_type in chain:
            try:
                # Check availability
                logger.debug("Checking %s availability...", provider_type.value)
                is_available = await self._check_provider_availability(provider_type)
                if not is_available:
                    logger.info(
                        "%s not available, skipping",
                        provider_type.value,
                        provider=provider_type.value,
                    )
                    continue

                # Try to generate
                adapter = self.adapters.get(provider_type)
                if not adapter:
                    logger.warning(
                        "No adapter for %s, skipping",
                        provider_type.value,
                        provider=provider_type.value,
                    )
                    continue

                logger.info(
                    "Attempting generation with %s...",
                    provider_type.value,
                    provider=provider_type.value,
                )

                response = await adapter.generate(
                    prompt=prompt,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )

                # Track metrics
                self.metrics["successful_requests"] += 1
                self.metrics["total_cost"] += response.cost

                if provider_type.value not in self.metrics["by_provider"]:
                    self.metrics["by_provider"][provider_type.value] = {
                        "requests": 0,
                        "cost": 0.0,
                    }

                self.metrics["by_provider"][provider_type.value]["requests"] += 1
                self.metrics["by_provider"][provider_type.value]["cost"] += response.cost

                logger.info(
                    "%s generation successful",
                    provider_type.value,
                    provider=provider_type.value,
                    response_time_ms=response.response_time_ms,
                    cost=response.cost,
                )

                return response

            except Exception as e:
                last_error = e
                logger.warning(
                    "%s generation failed",
                    provider_type.value,
                    provider=provider_type.value,
                    error=str(e),
                    exc_info=True,
                )
                continue

        # All providers failed
        self.metrics["failed_requests"] += 1
        error_msg = f"All model providers failed. Last error: {str(last_error)}"
        logger.error("All providers exhausted", error=error_msg, exc_info=last_error)
        raise ServiceError(error_msg)

    def get_status(self) -> Dict[str, Any]:
        """Get status of all providers"""
        return {
            "providers": {
                provider.value: {
                    "available": status.is_available,
                    "last_checked": status.last_checked.isoformat(),
                    "response_time_ms": status.response_time_ms,
                    "last_error": status.last_error,
                }
                for provider, status in self.provider_status.items()
            },
            "metrics": self.metrics,
        }

    async def list_models(self, provider: Optional[ProviderType] = None) -> Dict[str, List[str]]:
        """List available models"""
        if provider:
            adapter = self.adapters.get(provider)
            if adapter:
                models = (
                    await adapter.list_models()
                    if hasattr(adapter.list_models, "__func__")
                    and asyncio.iscoroutinefunction(adapter.list_models)
                    else adapter.list_models()
                )
                return {provider.value: models}
            return {provider.value: []}

        result = {}
        for prov, adapter in self.adapters.items():
            if asyncio.iscoroutinefunction(adapter.list_models):
                result[prov.value] = await adapter.list_models()
            else:
                result[prov.value] = adapter.list_models()
        return result


# ============================================================================
# GLOBAL SINGLETON
# ============================================================================

_model_consolidation_service: Optional[ModelConsolidationService] = None


def initialize_model_consolidation_service():
    """Initialize the global model consolidation service"""
    global _model_consolidation_service
    _model_consolidation_service = ModelConsolidationService()
    logger.info("Global model consolidation service initialized")


def get_model_consolidation_service() -> ModelConsolidationService:
    """Get the global model consolidation service (lazy-initialized)"""
    global _model_consolidation_service

    if _model_consolidation_service is None:
        initialize_model_consolidation_service()

    return _model_consolidation_service
