"""
Unified Model Consolidation Service

Consolidates 5 independent model providers into a single unified interface with
intelligent fallback chain:

Fallback Chain (in order):
1. Ollama (local, zero-cost) ← Primary
2. HuggingFace (free tier available)
3. Google Gemini (paid tier)
4. Anthropic Claude (paid tier)
5. OpenAI GPT (expensive, last resort)

Features:
- Unified interface across all providers
- Automatic fallback on provider failure
- Availability caching (5 minute TTL)
- Metrics tracking per provider
- Easy provider addition
- Graceful degradation

Usage:
    service = get_model_consolidation_service()
    response = await service.generate(
        prompt="Generate a blog post...",
        max_tokens=2000,
        temperature=0.7
    )
"""

from typing import Dict, Any, Optional, List, Tuple
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import structlog
import os
import asyncio

logger = structlog.get_logger(__name__)


# ============================================================================
# ENUMS & TYPES
# ============================================================================


class ProviderType(str, Enum):
    """Available model providers"""

    OLLAMA = "ollama"  # Local, zero-cost
    HUGGINGFACE = "huggingface"  # Free tier, rate limited
    GOOGLE = "google"  # Gemini API
    ANTHROPIC = "anthropic"  # Claude API
    OPENAI = "openai"  # GPT API (expensive)


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
        """Check if cache should be refreshed (5 min TTL)"""
        return datetime.utcnow() - self.last_checked > timedelta(minutes=5)


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
        pass

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
        pass

    @abstractmethod
    def list_models(self) -> List[str]:
        """List available models"""
        pass


class OllamaAdapter(ProviderAdapter):
    """Adapter for Ollama local model provider"""

    def __init__(self):
        from .ollama_client import OllamaClient

        self.host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.client = OllamaClient(base_url=self.host)
        self.provider_type = ProviderType.OLLAMA

    async def is_available(self) -> bool:
        """Check if Ollama service is running"""
        try:
            # Try to list models
            models = await self.client.list_models()
            is_available = len(models) > 0
            if is_available:
                logger.debug("Ollama available", model_count=len(models))
            return is_available
        except Exception as e:
            logger.debug("Ollama unavailable", error=str(e))
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
        model = model or "mistral:latest"
        start_time = datetime.utcnow()

        try:
            response = await self.client.generate(
                prompt=prompt,
                model=model,
                stream=False,
                options={
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            )

            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            return ModelResponse(
                text=response.get("response", ""),
                provider=self.provider_type,
                model=model,
                tokens_used=response.get("prompt_eval_count", 0) + response.get("eval_count", 0),
                cost=0.0,  # Ollama is free!
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("Ollama generation failed", error=str(e), model=model)
            raise

    def list_models(self) -> List[str]:
        """List available Ollama models"""
        return [
            "mistral:latest",
            "llama2:latest",
            "neural-chat:latest",
            "qwen2.5:14b",
            "mixtral:latest",
            "deepseek-r1:14b",
            "llama3:70b-instruct",
        ]


class HuggingFaceAdapter(ProviderAdapter):
    """Adapter for HuggingFace Inference API"""

    def __init__(self):
        from .huggingface_client import HuggingFaceClient

        self.token = os.getenv("HUGGINGFACE_API_TOKEN")
        self.client = HuggingFaceClient(api_token=self.token)
        self.provider_type = ProviderType.HUGGINGFACE

    async def is_available(self) -> bool:
        """Check if HuggingFace API is available"""
        try:
            is_available = await self.client.is_available()
            if is_available:
                logger.debug("HuggingFace available")
            return is_available
        except Exception as e:
            logger.debug("HuggingFace unavailable", error=str(e))
            return False

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
        start_time = datetime.utcnow()

        try:
            response = await self.client.generate(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=kwargs.get("top_p", 0.9),
            )

            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            return ModelResponse(
                text=response,
                provider=self.provider_type,
                model=model,
                tokens_used=len(prompt.split()) + len(response.split()),  # Rough estimate
                cost=0.0 if not self.token else 0.0001,  # Free tier or minimal cost
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("HuggingFace generation failed", error=str(e), model=model)
            raise

    def list_models(self) -> List[str]:
        """List available HuggingFace models"""
        return [
            "mistralai/Mistral-7B-Instruct-v0.1",
            "meta-llama/Llama-2-7b-chat",
            "tiiuae/falcon-7b-instruct",
        ]


class GoogleAdapter(ProviderAdapter):
    """Adapter for Google Gemini API"""

    def __init__(self):
        from .gemini_client import GeminiClient

        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.client = GeminiClient(api_key=self.api_key)
        self.provider_type = ProviderType.GOOGLE

    async def is_available(self) -> bool:
        """Check if Google Gemini API is available"""
        if not self.api_key:
            return False

        try:
            models = await self.client.list_models()
            is_available = len(models) > 0
            if is_available:
                logger.debug("Google Gemini available", model_count=len(models))
            return is_available
        except Exception as e:
            logger.debug("Google Gemini unavailable", error=str(e))
            return False

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs,
    ) -> ModelResponse:
        """Generate text using Google Gemini"""
        model = model or "gemini-pro"
        start_time = datetime.utcnow()

        try:
            response = await self.client.generate(
                prompt=prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            return ModelResponse(
                text=response,
                provider=self.provider_type,
                model=model,
                tokens_used=len(prompt.split()) + len(response.split()),  # Rough estimate
                cost=0.0001,  # Gemini is relatively cheap
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("Google Gemini generation failed", error=str(e), model=model)
            raise

    def list_models(self) -> List[str]:
        """List available Google models"""
        return [
            "gemini-pro",
            "gemini-pro-vision",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ]


class AnthropicAdapter(ProviderAdapter):
    """Adapter for Anthropic Claude API"""

    def __init__(self):
        try:
            from anthropic import Anthropic

            self.api_key = os.getenv("ANTHROPIC_API_KEY")
            if self.api_key:
                self.client = Anthropic(api_key=self.api_key)
            else:
                self.client = None
        except ImportError:
            logger.warning("Anthropic SDK not installed. Install with: pip install anthropic")
            self.client = None

        self.provider_type = ProviderType.ANTHROPIC

    async def is_available(self) -> bool:
        """Check if Anthropic Claude API is available"""
        return self.client is not None

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs,
    ) -> ModelResponse:
        """Generate text using Anthropic Claude"""
        if not self.client:
            raise Exception(
                "Anthropic client not configured. Set ANTHROPIC_API_KEY environment variable."
            )

        model = model or "claude-3-sonnet-20240229"
        start_time = datetime.utcnow()

        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )

            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            text = response.content[0].text if response.content else ""

            return ModelResponse(
                text=text,
                provider=self.provider_type,
                model=model,
                tokens_used=response.usage.input_tokens + response.usage.output_tokens,
                cost=0.0003,  # Approximate cost per token
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("Anthropic generation failed", error=str(e), model=model)
            raise

    def list_models(self) -> List[str]:
        """List available Anthropic models"""
        return [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]


class OpenAIAdapter(ProviderAdapter):
    """Adapter for OpenAI GPT API"""

    def __init__(self):
        try:
            from openai import OpenAI

            self.api_key = os.getenv("OPENAI_API_KEY")
            if self.api_key:
                self.client = OpenAI(api_key=self.api_key)
            else:
                self.client = None
        except ImportError:
            logger.warning("OpenAI SDK not installed. Install with: pip install openai")
            self.client = None

        self.provider_type = ProviderType.OPENAI

    async def is_available(self) -> bool:
        """Check if OpenAI API is available"""
        return self.client is not None

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs,
    ) -> ModelResponse:
        """Generate text using OpenAI GPT"""
        if not self.client:
            raise Exception(
                "OpenAI client not configured. Set OPENAI_API_KEY environment variable."
            )

        model = model or "gpt-4-turbo"
        start_time = datetime.utcnow()

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )

            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            text = response.choices[0].message.content if response.choices else ""

            return ModelResponse(
                text=text,
                provider=self.provider_type,
                model=model,
                tokens_used=response.usage.prompt_tokens + response.usage.completion_tokens,
                cost=0.0006,  # Approximate cost per token (GPT-4 is expensive!)
                response_time_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("OpenAI generation failed", error=str(e), model=model)
            raise

    def list_models(self) -> List[str]:
        """List available OpenAI models"""
        return [
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
        ]


# ============================================================================
# MAIN CONSOLIDATION SERVICE
# ============================================================================


class ModelConsolidationService:
    """
    Unified model service with intelligent fallback chain.

    Fallback Order:
    1. Ollama (local, zero-cost) ← Primary
    2. HuggingFace (free tier)
    3. Google Gemini
    4. Anthropic Claude
    5. OpenAI GPT (expensive, last resort)
    """

    # Fallback chain (order matters!)
    FALLBACK_CHAIN = [
        ProviderType.OLLAMA,
        ProviderType.HUGGINGFACE,
        ProviderType.GOOGLE,
        ProviderType.ANTHROPIC,
        ProviderType.OPENAI,
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
        """Initialize all provider adapters"""
        adapters_to_init = [
            (ProviderType.OLLAMA, OllamaAdapter),
            (ProviderType.HUGGINGFACE, HuggingFaceAdapter),
            (ProviderType.GOOGLE, GoogleAdapter),
            (ProviderType.ANTHROPIC, AnthropicAdapter),
            (ProviderType.OPENAI, OpenAIAdapter),
        ]

        for provider_type, adapter_class in adapters_to_init:
            try:
                adapter = adapter_class()
                self.adapters[provider_type] = adapter
                self.provider_status[provider_type] = ProviderStatus(
                    provider=provider_type,
                    is_available=False,
                    last_checked=datetime.utcnow(),
                )
                logger.debug("Adapter initialized", provider=provider_type.value)
            except Exception as e:
                logger.warning(
                    "Failed to initialize adapter", provider=provider_type.value, error=str(e)
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
                last_checked=datetime.utcnow(),
                last_error=None if is_available else "Not available",
            )

            return is_available
        except Exception as e:
            logger.warning("Provider check failed", provider=provider_type.value, error=str(e))

            self.provider_status[provider_type] = ProviderStatus(
                provider=provider_type,
                is_available=False,
                last_checked=datetime.utcnow(),
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
        for provider_type in chain:
            try:
                # Check availability
                is_available = await self._check_provider_availability(provider_type)
                if not is_available:
                    logger.debug("Provider not available, skipping", provider=provider_type.value)
                    continue

                # Try to generate
                adapter = self.adapters.get(provider_type)
                if not adapter:
                    continue

                logger.info("Attempting generation with provider", provider=provider_type.value)

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
                    "Generation successful",
                    provider=provider_type.value,
                    response_time_ms=response.response_time_ms,
                    cost=response.cost,
                )

                return response

            except Exception as e:
                last_error = e
                logger.warning(
                    "Provider generation failed", provider=provider_type.value, error=str(e)
                )
                continue

        # All providers failed
        self.metrics["failed_requests"] += 1
        error_msg = f"All model providers failed. Last error: {str(last_error)}"
        logger.error("All providers exhausted", error=error_msg)
        raise Exception(error_msg)

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

    def list_models(self, provider: Optional[ProviderType] = None) -> Dict[str, List[str]]:
        """List available models"""
        if provider:
            adapter = self.adapters.get(provider)
            return {provider.value: adapter.list_models() if adapter else []}

        return {
            provider.value: adapter.list_models() for provider, adapter in self.adapters.items()
        }


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
