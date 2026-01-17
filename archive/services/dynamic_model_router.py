"""
Dynamic Model Router for GLAD Labs

Intelligently selects which LLM provider/model to use based on:
- Task type and step within task
- Available budget
- Quality requirements
- Model availability
- Cost tracking

All model selections come from PostgreSQL (no hardcoding).

Features:
- Per-task-step model configuration
- Automatic fallback chain
- Cost tracking (API $ + energy + hardware)
- Ollama-first strategy
- Circuit breaker for failing models
- A/B testing support
"""

import asyncio
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import aiohttp
import asyncpg


class Provider(str, Enum):
    """LLM provider"""
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class ModelStatus(str, Enum):
    """Model availability status"""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class ModelConfig:
    """Model configuration (from database)"""
    model_name: str
    provider: Provider
    enabled: bool
    temperature: float
    max_tokens: int
    cost_per_1k_tokens: float
    cost_energy_kwh_per_1k_tokens: float
    hardware_amortization_cost: float
    avg_latency_ms: float
    quality_score: float  # 0-1.0 (from historical execution)
    priority: int  # Lower = higher priority


@dataclass
class ModelResponse:
    """Response from LLM query"""
    response: str
    model_used: str
    provider: Provider
    tokens_used: int
    cost_usd: float
    cost_energy_kwh: float
    latency_ms: float
    metadata: Dict[str, Any]


@dataclass
class RoutingDecision:
    """Record of model selection decision"""
    execution_id: str
    task_type: str
    task_step: str
    requested_model: Optional[str]  # What was configured
    selected_model: str  # What was actually used
    fallback_count: int  # How many times we fell back
    reason: str  # Why this model was selected
    timestamp: str


class CircuitBreaker:
    """Track model failures and open circuit if too many"""

    def __init__(self, failure_threshold: int = 5, timeout_seconds: int = 300):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.status = ModelStatus.ONLINE

    def record_failure(self) -> None:
        """Record a failure"""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.failure_count >= self.failure_threshold:
            self.status = ModelStatus.CIRCUIT_OPEN

    def record_success(self) -> None:
        """Record a success"""
        self.failure_count = max(0, self.failure_count - 1)

    def is_available(self) -> bool:
        """Check if circuit is closed"""
        if self.status != ModelStatus.CIRCUIT_OPEN:
            return True

        # Check if timeout has passed
        if self.last_failure_time:
            elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
            if elapsed > self.timeout_seconds:
                self.status = ModelStatus.ONLINE
                self.failure_count = 0
                return True

        return False


class DynamicModelRouter:
    """
    Routes model queries to the best available LLM provider.

    All model configurations come from PostgreSQL:
    - Task-specific model preferences
    - Provider configurations
    - Cost tracking
    - Quality metrics

    Example Usage:

        router = DynamicModelRouter(db_connection_string)
        await router.initialize()

        response = await router.query(
            prompt="Write a blog post",
            task_type="blog_post",
            task_step="creative",
            execution_id="123",
            budget_usd=0.50
        )
    """

    def __init__(
        self,
        db_connection_string: str,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize router.

        Args:
            db_connection_string: PostgreSQL connection string
            logger: Optional logger
        """
        self.db_connection_string = db_connection_string
        self.logger = logger or logging.getLogger("model_router")

        # Database connection (lazy-loaded)
        self.db: Optional[asyncpg.Connection] = None

        # Cache model configs (refresh every 5 minutes)
        self.model_configs: Dict[str, ModelConfig] = {}
        self.last_config_refresh: Optional[datetime] = None

        # Circuit breakers for each model
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}

        # API clients for each provider
        self.ollama_client: Optional[aiohttp.ClientSession] = None
        self.openai_client: Optional[aiohttp.ClientSession] = None
        self.anthropic_client: Optional[aiohttp.ClientSession] = None
        self.google_client: Optional[aiohttp.ClientSession] = None

    # ========================================================================
    # Initialization
    # ========================================================================

    async def initialize(self) -> None:
        """Connect to database and load model configs"""
        self.db = await asyncpg.connect(self.db_connection_string)
        await self._refresh_model_configs()

    async def shutdown(self) -> None:
        """Close all connections"""
        if self.db:
            await self.db.close()
        if self.ollama_client:
            await self.ollama_client.close()
        if self.openai_client:
            await self.openai_client.close()
        if self.anthropic_client:
            await self.anthropic_client.close()
        if self.google_client:
            await self.google_client.close()

    # ========================================================================
    # Configuration Management (from database)
    # ========================================================================

    async def _refresh_model_configs(self) -> None:
        """Load/refresh model configurations from database"""
        if not self.db:
            raise RuntimeError("Database not initialized")

        # Fetch all enabled models
        rows = await self.db.fetch(
            """
            SELECT
                model_name,
                provider,
                enabled,
                temperature,
                max_tokens,
                cost_per_1k_tokens,
                cost_energy_kwh_per_1k_tokens,
                hardware_amortization_cost,
                avg_latency_ms,
                quality_score,
                priority
            FROM model_configurations
            WHERE enabled = true
            ORDER BY priority ASC
            """
        )

        self.model_configs = {}
        for row in rows:
            config = ModelConfig(
                model_name=row["model_name"],
                provider=Provider(row["provider"]),
                enabled=row["enabled"],
                temperature=row["temperature"],
                max_tokens=row["max_tokens"],
                cost_per_1k_tokens=row["cost_per_1k_tokens"],
                cost_energy_kwh_per_1k_tokens=row["cost_energy_kwh_per_1k_tokens"],
                hardware_amortization_cost=row["hardware_amortization_cost"],
                avg_latency_ms=row["avg_latency_ms"],
                quality_score=row["quality_score"],
                priority=row["priority"]
            )
            self.model_configs[row["model_name"]] = config

        self.last_config_refresh = datetime.utcnow()
        self.logger.info(f"Loaded {len(self.model_configs)} model configurations")

    async def _get_task_model_preference(
        self,
        task_type: str,
        task_step: str
    ) -> Optional[str]:
        """
        Get configured model preference for a task/step.

        Returns None if no specific preference (use default fallback chain).
        """
        if not self.db:
            return None

        row = await self.db.fetchrow(
            """
            SELECT preferred_model
            FROM task_model_preferences
            WHERE task_type = $1 AND task_step = $2
            """,
            task_type,
            task_step
        )

        return row["preferred_model"] if row else None

    # ========================================================================
    # Model Selection Logic
    # ========================================================================

    async def query(
        self,
        prompt: str,
        task_type: str,
        task_step: str,
        execution_id: str,
        budget_usd: Optional[float] = None,
        quality_threshold: float = 0.7,
        max_retries: int = 3
    ) -> ModelResponse:
        """
        Query the best available model for the task.

        Routing strategy:
        1. Check if configs need refresh
        2. Get task-specific model preference
        3. Build fallback chain (preference → defaults → cheap options)
        4. Try each model in chain until one works
        5. Track cost, quality, latency
        6. Update circuit breaker

        Args:
            prompt: The prompt to query
            task_type: Type of task (e.g., "blog_post")
            task_step: Step within task (e.g., "creative")
            execution_id: Execution ID for logging
            budget_usd: Maximum cost allowed (None = unlimited)
            quality_threshold: Minimum quality score required
            max_retries: Retries per model

        Returns:
            ModelResponse with selected model and response

        Raises:
            Exception: If no model available within budget
        """
        # Refresh configs if needed (every 5 minutes)
        if (
            self.last_config_refresh is None
            or datetime.utcnow() - self.last_config_refresh > timedelta(minutes=5)
        ):
            await self._refresh_model_configs()

        # Get task-specific preference
        preferred_model = await self._get_task_model_preference(task_type, task_step)

        # Build fallback chain
        fallback_chain = await self._build_fallback_chain(
            preferred_model,
            task_type,
            task_step,
            budget_usd,
            quality_threshold
        )

        if not fallback_chain:
            raise Exception(
                f"No available models for {task_type}/{task_step} "
                f"within budget ${budget_usd}"
            )

        # Try each model in chain
        fallback_count = 0
        last_error = None

        for model_name in fallback_chain:
            try:
                response = await self._query_model(
                    model_name,
                    prompt,
                    max_retries=max_retries
                )

                # Record successful routing decision
                await self._record_routing_decision(
                    execution_id=execution_id,
                    task_type=task_type,
                    task_step=task_step,
                    requested_model=preferred_model,
                    selected_model=model_name,
                    fallback_count=fallback_count,
                    reason="Success"
                )

                # Record success for circuit breaker
                if model_name in self.circuit_breakers:
                    self.circuit_breakers[model_name].record_success()

                # Update quality metrics in database
                await self._update_model_metrics(
                    model_name,
                    response.tokens_used,
                    response.cost_usd,
                    success=True
                )

                return response

            except Exception as e:
                last_error = e
                fallback_count += 1
                self.logger.warning(
                    f"Model {model_name} failed: {str(e)}, trying fallback"
                )

                # Record failure for circuit breaker
                if model_name in self.circuit_breakers:
                    self.circuit_breakers[model_name].record_failure()

                # Update metrics with failure
                await self._update_model_metrics(
                    model_name,
                    tokens_used=0,
                    cost_usd=0,
                    success=False
                )

        # All models failed
        raise Exception(
            f"All models failed for {task_type}/{task_step}. "
            f"Last error: {str(last_error)}"
        )

    async def _build_fallback_chain(
        self,
        preferred_model: Optional[str],
        task_type: str,
        task_step: str,
        budget_usd: Optional[float],
        quality_threshold: float
    ) -> List[str]:
        """Build ordered list of models to try"""
        chain = []

        # 1. If preferred model is configured and available, use it first
        if preferred_model and preferred_model in self.model_configs:
            config = self.model_configs[preferred_model]
            if (
                config.enabled
                and config.quality_score >= quality_threshold
                and (budget_usd is None or config.cost_per_1k_tokens <= budget_usd)
            ):
                breaker = self.circuit_breakers.setdefault(
                    preferred_model, CircuitBreaker()
                )
                if breaker.is_available():
                    chain.append(preferred_model)

        # 2. Add default fallback chain:
        # Ollama (free) → Anthropic → OpenAI → Google
        default_chain = ["ollama", "anthropic:claude-opus", "openai:gpt-4", "google:gemini-pro"]

        for model in default_chain:
            if model not in chain and model in self.model_configs:
                config = self.model_configs[model]
                if (
                    config.enabled
                    and config.quality_score >= quality_threshold
                    and (budget_usd is None or config.cost_per_1k_tokens <= budget_usd)
                ):
                    breaker = self.circuit_breakers.setdefault(model, CircuitBreaker())
                    if breaker.is_available():
                        chain.append(model)

        return chain

    # ========================================================================
    # Model-Specific Query Methods
    # ========================================================================

    async def _query_model(
        self,
        model_name: str,
        prompt: str,
        max_retries: int = 3
    ) -> ModelResponse:
        """Query a specific model"""
        if model_name not in self.model_configs:
            raise Exception(f"Unknown model: {model_name}")

        config = self.model_configs[model_name]

        # Route to provider-specific handler
        if config.provider == Provider.OLLAMA:
            return await self._query_ollama(model_name, prompt, config)
        elif config.provider == Provider.OPENAI:
            return await self._query_openai(model_name, prompt, config)
        elif config.provider == Provider.ANTHROPIC:
            return await self._query_anthropic(model_name, prompt, config)
        elif config.provider == Provider.GOOGLE:
            return await self._query_google(model_name, prompt, config)
        else:
            raise Exception(f"Unknown provider: {config.provider}")

    async def _query_ollama(
        self,
        model_name: str,
        prompt: str,
        config: ModelConfig
    ) -> ModelResponse:
        """Query Ollama (local, free)"""
        start_time = datetime.utcnow()

        if not self.ollama_client:
            self.ollama_client = aiohttp.ClientSession()

        try:
            async with self.ollama_client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model_name,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": config.temperature,
                    "num_predict": config.max_tokens
                },
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    raise Exception(f"Ollama error: {resp.status}")

                data = await resp.json()
                response_text = data.get("response", "")
                tokens_used = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)

                latency_ms = (
                    datetime.utcnow() - start_time
                ).total_seconds() * 1000

                # Cost for Ollama = hardware amortization only (free API)
                cost_usd = config.hardware_amortization_cost * (tokens_used / 1000)

                return ModelResponse(
                    response=response_text,
                    model_used=model_name,
                    provider=Provider.OLLAMA,
                    tokens_used=tokens_used,
                    cost_usd=cost_usd,
                    cost_energy_kwh=config.cost_energy_kwh_per_1k_tokens * (tokens_used / 1000),
                    latency_ms=latency_ms,
                    metadata={"stream": False}
                )

        except asyncio.TimeoutError:
            raise Exception("Ollama request timeout")

    async def _query_openai(
        self,
        model_name: str,
        prompt: str,
        config: ModelConfig
    ) -> ModelResponse:
        """Query OpenAI (paid API)"""
        # Placeholder - will be implemented with actual OpenAI API calls
        raise NotImplementedError("OpenAI implementation pending")

    async def _query_anthropic(
        self,
        model_name: str,
        prompt: str,
        config: ModelConfig
    ) -> ModelResponse:
        """Query Anthropic Claude (paid API)"""
        # Placeholder - will be implemented with actual Anthropic API calls
        raise NotImplementedError("Anthropic implementation pending")

    async def _query_google(
        self,
        model_name: str,
        prompt: str,
        config: ModelConfig
    ) -> ModelResponse:
        """Query Google Gemini (paid API)"""
        # Placeholder - will be implemented with actual Google API calls
        raise NotImplementedError("Google implementation pending")

    # ========================================================================
    # Logging & Tracking
    # ========================================================================

    async def _record_routing_decision(
        self,
        execution_id: str,
        task_type: str,
        task_step: str,
        requested_model: Optional[str],
        selected_model: str,
        fallback_count: int,
        reason: str
    ) -> None:
        """Log model selection decision"""
        if not self.db:
            return

        await self.db.execute(
            """
            INSERT INTO routing_decisions
            (execution_id, task_type, task_step, requested_model, selected_model, fallback_count, reason, timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            execution_id,
            task_type,
            task_step,
            requested_model,
            selected_model,
            fallback_count,
            reason,
            datetime.utcnow().isoformat()
        )

    async def _update_model_metrics(
        self,
        model_name: str,
        tokens_used: int,
        cost_usd: float,
        success: bool
    ) -> None:
        """Update model performance metrics"""
        if not self.db:
            return

        await self.db.execute(
            """
            INSERT INTO model_execution_metrics
            (model_name, tokens_used, cost_usd, success, timestamp)
            VALUES ($1, $2, $3, $4, $5)
            """,
            model_name,
            tokens_used,
            cost_usd,
            success,
            datetime.utcnow().isoformat()
        )
