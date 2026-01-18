"""
Circuit breaker pattern for handling API failures gracefully.

Prevents cascading failures by stopping requests to failing services
and allowing them time to recover.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Callable, Awaitable, TypeVar
from enum import Enum

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Service down, refusing requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker for managing external API calls.

    Prevents cascading failures by stopping requests when service is down
    and gradually allowing traffic back when service recovers.
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        success_threshold: int = 2,
    ):
        """
        Initialize circuit breaker.

        Args:
            service_name: Name of the service being protected
            failure_threshold: Failures before opening circuit (default: 5)
            recovery_timeout: Seconds before trying to recover (default: 60)
            success_threshold: Successes in half-open before closing (default: 2)
        """
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.opened_time: Optional[datetime] = None

    def record_success(self) -> None:
        """Record a successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            logger.debug(
                f"[{self.service_name}] Half-open success {self.success_count}/{self.success_threshold}"
            )

            if self.success_count >= self.success_threshold:
                self._close_circuit()
        else:
            self.failure_count = 0  # Reset failure count on success

    def record_failure(self) -> None:
        """Record a failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.state == CircuitState.CLOSED:
            logger.warning(
                f"[{self.service_name}] Failure {self.failure_count}/{self.failure_threshold}"
            )

            if self.failure_count >= self.failure_threshold:
                self._open_circuit()

        elif self.state == CircuitState.HALF_OPEN:
            # Any failure in half-open state goes back to open
            logger.warning(f"[{self.service_name}] Failed during recovery - reopening circuit")
            self._open_circuit()

    def is_available(self) -> bool:
        """
        Check if service is available for requests.

        Returns:
            True if service is CLOSED or HALF_OPEN, False if OPEN
        """
        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if self.opened_time:
                elapsed = (datetime.now(timezone.utc) - self.opened_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    logger.info(f"[{self.service_name}] Recovery timeout passed - trying again")
                    self._half_open_circuit()
                    return True
            return False

        return True  # CLOSED or HALF_OPEN

    def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status."""
        return {
            "service": self.service_name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time.isoformat()
            if self.last_failure_time
            else None,
            "opened_time": self.opened_time.isoformat() if self.opened_time else None,
        }

    def _open_circuit(self) -> None:
        """Open circuit - stop accepting requests."""
        self.state = CircuitState.OPEN
        self.opened_time = datetime.now(timezone.utc)
        self.success_count = 0
        logger.error(
            f"🔴 [{self.service_name}] Circuit OPEN - service unavailable "
            f"(will retry in {self.recovery_timeout}s)"
        )

    def _half_open_circuit(self) -> None:
        """Half-open circuit - allow test requests."""
        self.state = CircuitState.HALF_OPEN
        self.failure_count = 0
        self.success_count = 0
        logger.warning(f"🟡 [{self.service_name}] Circuit HALF_OPEN - testing recovery")

    def _close_circuit(self) -> None:
        """Close circuit - resume normal operation."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.opened_time = None
        logger.info(f"🟢 [{self.service_name}] Circuit CLOSED - service recovered")


# Global circuit breakers for common services
circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(service_name: str) -> CircuitBreaker:
    """
    Get or create circuit breaker for service.

    Args:
        service_name: Name of the service

    Returns:
        CircuitBreaker instance
    """
    if service_name not in circuit_breakers:
        circuit_breakers[service_name] = CircuitBreaker(service_name)
    return circuit_breakers[service_name]


async def with_circuit_breaker(
    operation: Callable[..., Awaitable[T]],
    service_name: str,
    fallback_value: Optional[T] = None,
    *args,
    **kwargs,
) -> Optional[T]:
    """
    Execute operation with circuit breaker protection.

    Args:
        operation: Async function to execute
        service_name: Name of service being called
        fallback_value: Value to return if circuit is open
        *args: Arguments to pass to operation
        **kwargs: Keyword arguments to pass to operation

    Returns:
        Result of operation or fallback_value if circuit is open

    Example:
        >>> async def fetch_user_data(user_id: int) -> dict:
        ...     return await external_api.get_user(user_id)
        ...
        >>> user = await with_circuit_breaker(
        ...     fetch_user_data, "user_service", fallback_value={},
        ...     user_id=123
        ... )
    """
    breaker = get_circuit_breaker(service_name)

    if not breaker.is_available():
        logger.warning(f"🔴 [{service_name}] Circuit is OPEN - using fallback")
        return fallback_value

    try:
        result = await operation(*args, **kwargs)
        breaker.record_success()
        return result

    except asyncio.TimeoutError:
        breaker.record_failure()
        logger.error(f"❌ [{service_name}] Timeout")
        return fallback_value

    except ConnectionError as e:
        breaker.record_failure()
        logger.error(f"❌ [{service_name}] Connection error: {e}")
        return fallback_value

    except Exception as e:
        breaker.record_failure()
        logger.error(f"❌ [{service_name}] Error: {type(e).__name__}: {e}")
        return fallback_value


class CachedResponse:
    """Cache for API responses during outages."""

    def __init__(self, max_age: int = 3600):
        """
        Initialize response cache.

        Args:
            max_age: Maximum age of cached response in seconds (default: 1 hour)
        """
        self.cache: Dict[str, tuple] = {}  # key -> (value, timestamp)
        self.max_age = max_age

    def get(self, key: str) -> Optional[Any]:
        """
        Get cached value if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found or expired
        """
        if key not in self.cache:
            return None

        value, timestamp = self.cache[key]
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()

        if age > self.max_age:
            logger.debug(f"Cache expired for {key} (age: {age:.0f}s)")
            del self.cache[key]
            return None

        logger.debug(f"Cache hit for {key} (age: {age:.1f}s)")
        return value

    def set(self, key: str, value: Any) -> None:
        """
        Cache a value.

        Args:
            key: Cache key
            value: Value to cache
        """
        self.cache[key] = (value, datetime.now(timezone.utc))
        logger.debug(f"Cached {key}")

    def clear(self) -> None:
        """Clear all cached values."""
        self.cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_size = sum(
            len(str(value))
            for value, _ in self.cache.values()
        )
        return {
            "cached_items": len(self.cache),
            "total_size_bytes": total_size,
            "max_age_seconds": self.max_age,
        }


# Global response cache
response_cache = CachedResponse(max_age=3600)


async def get_with_fallback(
    operation: Callable[..., Awaitable[T]],
    service_name: str,
    cache_key: Optional[str] = None,
    *args,
    **kwargs,
) -> Optional[T]:
    """
    Execute operation with circuit breaker and caching fallback.

    Args:
        operation: Async function to execute
        service_name: Name of service
        cache_key: Key for caching response (if None, no caching)
        *args: Arguments to pass to operation
        **kwargs: Keyword arguments to pass to operation

    Returns:
        Fresh result, cached result, or None

    Example:
        >>> async def fetch_trending_topics() -> list:
        ...     return await api.get_trends()
        ...
        >>> trends = await get_with_fallback(
        ...     fetch_trending_topics, "trends_api",
        ...     cache_key="trending_topics"
        ... )
    """
    breaker = get_circuit_breaker(service_name)

    # Try to get fresh result
    if breaker.is_available():
        try:
            result = await operation(*args, **kwargs)
            breaker.record_success()

            # Cache successful response
            if cache_key:
                response_cache.set(cache_key, result)
                logger.info(f"✅ [{service_name}] Success (cached)")

            return result

        except Exception as e:
            breaker.record_failure()
            logger.warning(f"⚠️  [{service_name}] Failed: {type(e).__name__}")

    # Circuit is open or operation failed - try cache
    if cache_key:
        cached = response_cache.get(cache_key)
        if cached is not None:
            logger.info(f"🟡 [{service_name}] Using cached response")
            return cached

    logger.warning(f"❌ [{service_name}] No response available (circuit open, no cache)")
    return None


def get_all_circuit_breaker_status() -> Dict[str, Dict[str, Any]]:
    """Get status of all circuit breakers."""
    return {name: breaker.get_status() for name, breaker in circuit_breakers.items()}
