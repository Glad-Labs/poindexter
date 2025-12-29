"""
Ollama Client Service

Provides zero-cost local AI model inference using Ollama.
Perfect for desktop/development environments with no API costs.

Supported Models:
- llama2 (7B, 13B, 70B)
- llama2:13b
- codellama (7B, 13B, 34B, 70B)
- mistral (7B)
- mixtral (8x7B)
- phi (2.7B)
- neural-chat (7B)
- starling-lm (7B)
- openchat (7B)
- vicuna (7B, 13B, 33B)

Install Ollama: https://ollama.ai/download
Pull models: ollama pull llama2
"""

from typing import Dict, Any, List, Optional, AsyncIterator
import httpx
import json
import structlog

logger = structlog.get_logger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

DEFAULT_MODEL = "llama2"  # Good balance of speed/quality
DEFAULT_BASE_URL = "http://localhost:11434"

# Model capabilities and recommended use cases
MODEL_PROFILES = {
    "llama2": {
        "size": "7B",
        "speed": "fast",
        "quality": "good",
        "use_cases": ["chat", "summarization", "Q&A"],
        "cost": 0.0,
    },
    "llama2:13b": {
        "size": "13B",
        "speed": "medium",
        "quality": "excellent",
        "use_cases": ["complex chat", "analysis", "reasoning"],
        "cost": 0.0,
    },
    "llama2:70b": {
        "size": "70B",
        "speed": "slow",
        "quality": "outstanding",
        "use_cases": ["complex reasoning", "critical analysis"],
        "cost": 0.0,
    },
    "mistral": {
        "size": "7B",
        "speed": "very fast",
        "quality": "excellent",
        "use_cases": ["chat", "creative writing", "general tasks"],
        "cost": 0.0,
    },
    "mixtral": {
        "size": "8x7B",
        "speed": "medium",
        "quality": "outstanding",
        "use_cases": ["complex reasoning", "code", "analysis"],
        "cost": 0.0,
    },
    "codellama": {
        "size": "7B-34B",
        "speed": "fast",
        "quality": "excellent",
        "use_cases": ["code generation", "code review", "debugging"],
        "cost": 0.0,
    },
    "phi": {
        "size": "2.7B",
        "speed": "blazing fast",
        "quality": "good",
        "use_cases": ["simple tasks", "quick responses"],
        "cost": 0.0,
    },
}


# ============================================================================
# EXCEPTIONS
# ============================================================================


class OllamaError(Exception):
    """Base exception for Ollama errors."""

    pass


class OllamaConnectionError(OllamaError):
    """Raised when cannot connect to Ollama server."""

    pass


class OllamaModelNotFoundError(OllamaError):
    """Raised when requested model is not available."""

    pass


class OllamaClient:
    """
    Client for Ollama local LLM inference.

    Zero-cost alternative to OpenAI/Claude for desktop environments.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: int = 120):
        """
        Initialize Ollama client.

        Args:
            base_url: Ollama server URL (default: http://localhost:11434)
            model: Default model to use (default: llama2)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or DEFAULT_BASE_URL
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

        logger.info("Ollama client initialized", base_url=self.base_url, model=self.model)

    async def close(self):
        """Close the HTTP client connection."""
        if self.client:
            await self.client.aclose()

    async def check_health(self) -> bool:
        """
        Check if Ollama server is running and accessible.

        Returns:
            True if server is healthy, False otherwise
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
                return response.status_code == 200
        except Exception as e:
            logger.warning("Ollama health check failed", error=str(e))
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        """
        List available Ollama models.

        Returns:
            List of model dictionaries with name, size, modified date
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/api/tags", timeout=10.0)
                response.raise_for_status()
                data = response.json()

                models = data.get("models", [])
                logger.info(f"Found {len(models)} Ollama models")
                return models

        except Exception as e:
            logger.error("Failed to list models", error=str(e))
            return []

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate completion from Ollama model.

        Args:
            prompt: User prompt
            model: Model name (default: self.model)
            system: System prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            stream: Whether to stream response

        Returns:
            Dictionary with response text, tokens, and timing
        """
        model = model or self.model

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
            },
        }

        if system:
            payload["system"] = system

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
                )
                response.raise_for_status()

                result = response.json()

                logger.info(
                    "Ollama generation complete",
                    model=model,
                    tokens=result.get("eval_count", 0),
                    duration=result.get("total_duration", 0) / 1e9,  # ns to seconds
                    cost=0.0,
                )

                return {
                    "text": result.get("response", ""),
                    "model": model,
                    "tokens": result.get("eval_count", 0),
                    "prompt_tokens": result.get("prompt_eval_count", 0),
                    "total_tokens": result.get("eval_count", 0)
                    + result.get("prompt_eval_count", 0),
                    "duration_seconds": result.get("total_duration", 0) / 1e9,
                    "cost": 0.0,  # Zero cost!
                    "done": result.get("done", False),
                }

        except httpx.HTTPError as e:
            logger.error("Ollama generation failed", error=str(e), model=model)
            raise

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Chat completion with message history.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Returns:
            Dictionary with response and metadata
        """
        model = model or self.model

        # Convert messages to prompt string format for Ollama
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                prompt += f"User: {content}\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n"
        prompt += "Assistant: "

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
                )
                response.raise_for_status()

                result = response.json()

                # Extract only the assistant response (remove the prompt we sent)
                full_response = result.get("response", "")

                # Extract just the assistant's response (after "Assistant: ")
                if "Assistant: " in full_response:
                    # Get everything after the last "Assistant: "
                    parts = full_response.split("Assistant: ")
                    assistant_response = parts[-1].strip()
                else:
                    assistant_response = full_response.strip()

                logger.info(
                    "Ollama chat complete",
                    model=model,
                    tokens=result.get("eval_count", 0),
                    cost=0.0,
                )

                return {
                    "role": "assistant",
                    "content": assistant_response,
                    "model": model,
                    "tokens": result.get("eval_count", 0),
                    "prompt_tokens": result.get("prompt_eval_count", 0),
                    "total_tokens": result.get("eval_count", 0)
                    + result.get("prompt_eval_count", 0),
                    "duration_seconds": result.get("total_duration", 0) / 1e9,
                    "cost": 0.0,
                    "done": result.get("done", False),
                }

        except httpx.HTTPError as e:
            logger.error("Ollama chat failed", error=str(e), model=model)
            raise

    async def pull_model(self, model: str) -> bool:
        """
        Pull a model from Ollama library.

        Args:
            model: Model name to pull (e.g., "llama2", "mistral")

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Pulling Ollama model: {model}")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/pull",
                    json={"name": model},
                    timeout=3600.0,  # Model downloads can take a while
                )
                response.raise_for_status()

                logger.info(f"Successfully pulled model: {model}")
                return True

        except Exception as e:
            logger.error(f"Failed to pull model {model}", error=str(e))
            return False

    def get_model_profile(self, model: str) -> Optional[Dict[str, Any]]:
        """
        Get model profile information.

        Args:
            model: Model name

        Returns:
            Model profile dict or None if not found
        """
        # Extract base model name (handle :tag notation)
        base_model = model.split(":")[0]
        return MODEL_PROFILES.get(base_model)

    def recommend_model(self, task_type: str) -> str:
        """
        Recommend best Ollama model for task type.

        Args:
            task_type: Task type (e.g., "chat", "code", "analysis")

        Returns:
            Recommended model name
        """
        task_lower = task_type.lower()

        # Code-related tasks
        if any(kw in task_lower for kw in ["code", "program", "debug", "implement"]):
            return "codellama"

        # Fast/simple tasks
        if any(kw in task_lower for kw in ["classify", "extract", "simple"]):
            return "phi"

        # Complex reasoning
        if any(kw in task_lower for kw in ["complex", "reason", "analyze", "critical"]):
            return "mixtral"

        # Default to mistral (best general purpose)
        return "mistral"

    async def generate_with_retry(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Generate completion with exponential backoff retry logic.

        Retries failed requests with exponential backoff to handle:
        - Temporary network issues
        - Model loading delays
        - Ollama process restarts

        Args:
            prompt: User prompt
            model: Model name (default: self.model)
            system: System prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay in seconds (doubles each retry)

        Returns:
            Dictionary with response text, tokens, and timing
        """
        import asyncio
        import time

        model = model or self.model
        last_error = None

        for attempt in range(max_retries):
            try:
                logger.info(
                    f"Ollama generation attempt {attempt + 1}/{max_retries}",
                    model=model,
                    attempt=attempt + 1,
                )

                result = await self.generate(
                    prompt=prompt,
                    model=model,
                    system=system,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                )

                if result and result.get("text"):
                    logger.info(
                        "✓ Ollama generation succeeded",
                        model=model,
                        attempt=attempt + 1,
                        tokens=result.get("tokens", 0),
                    )
                    return result

            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Ollama connection failed (attempt {attempt + 1}), "
                        f"retrying in {delay}s...",
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Ollama connection failed after all retries",
                        attempts=max_retries,
                        error=str(e),
                    )

            except httpx.ReadTimeout as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Ollama request timeout (attempt {attempt + 1}), "
                        f"retrying in {delay}s...",
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Ollama timeout after all retries", attempts=max_retries, error=str(e)
                    )

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Ollama generation failed (attempt {attempt + 1}), "
                        f"retrying in {delay}s...",
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Ollama generation failed after all retries",
                        attempts=max_retries,
                        error=str(e),
                    )

        # All retries exhausted
        logger.error(
            "All Ollama generation attempts exhausted",
            max_retries=max_retries,
            last_error=str(last_error),
        )
        raise last_error if last_error else OllamaError("Generation failed after all retries")

    async def stream_generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Stream generation from Ollama model.

        Args:
            prompt: User prompt
            model: Model name
            system: System prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Yields:
            Text chunks as they are generated
        """
        model = model or self.model

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
            },
        }

        if system:
            payload["system"] = system

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                if "response" in data:
                                    yield data["response"]
                            except json.JSONDecodeError:
                                continue

        except httpx.HTTPError as e:
            logger.error("Ollama streaming failed", error=str(e), model=model)
            raise


# Initialize function for easy integration
async def initialize_ollama_client(
    base_url: str | None = None, model: str | None = None
) -> OllamaClient:
    """
    Initialize Ollama client.

    Args:
        base_url: Ollama server URL
        model: Default model to use

    Returns:
        Initialized OllamaClient
    """
    return OllamaClient(base_url=base_url, model=model)
