"""
Retry utilities for database and API operations.

Provides exponential backoff retry logic for handling transient failures
in database connections and external API calls.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 0.5,
        max_delay: float = 10.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        """
        Initialize retry configuration.

        Args:
            max_attempts: Maximum number of retry attempts (default: 3)
            initial_delay: Initial delay in seconds (default: 0.5s)
            max_delay: Maximum delay between retries (default: 10s)
            exponential_base: Base for exponential backoff (default: 2.0)
            jitter: Whether to add random jitter to delays (default: True)
        """
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay for given attempt number (0-indexed).

        Args:
            attempt: Attempt number (0 = first attempt)

        Returns:
            Delay in seconds
        """
        if attempt == 0:
            return 0  # No delay before first attempt

        # Exponential backoff: delay = initial_delay * (base ^ attempt)
        delay = self.initial_delay * (self.exponential_base ** (attempt - 1))
        delay = min(delay, self.max_delay)  # Cap at max_delay

        if self.jitter:
            # Add up to 25% random jitter to avoid thundering herd
            import random

            jitter_amount = delay * 0.25 * random.random()
            delay += jitter_amount

        return delay


# Default retry configuration for database operations
DB_RETRY_CONFIG = RetryConfig(
    max_attempts=3, initial_delay=0.1, max_delay=2.0, exponential_base=2.0, jitter=True
)

# Retry configuration for external API calls
API_RETRY_CONFIG = RetryConfig(
    max_attempts=3, initial_delay=1.0, max_delay=10.0, exponential_base=2.0, jitter=True
)


class RetryableException(Exception):
    """
    Exception that indicates an operation should be retried.

    Wraps the original exception with retry context.
    """

    def __init__(self, original_exception: Exception, attempt: int, config: RetryConfig):
        """
        Initialize retryable exception.

        Args:
            original_exception: The original exception that occurred
            attempt: Attempt number that failed (1-indexed)
            config: Retry configuration used
        """
        self.original_exception = original_exception
        self.attempt = attempt
        self.config = config
        next_delay = config.get_delay(attempt) if attempt < config.max_attempts else None

        message = (
            f"Operation failed on attempt {attempt}/{config.max_attempts}: "
            f"{type(original_exception).__name__}: {str(original_exception)}"
        )
        if next_delay is not None:
            message += f". Retrying in {next_delay:.2f}s..."

        super().__init__(message)


async def async_retry(
    operation: Callable[..., Awaitable[T]],
    *args,
    config: RetryConfig = DB_RETRY_CONFIG,
    on_retry: Callable[[Exception, int, float], Awaitable[None]] = None,
    retryable_exceptions: tuple = (Exception,),
    **kwargs,
) -> T:
    """
    Retry an async operation with exponential backoff.

    Args:
        operation: Async function to retry
        config: Retry configuration (default: DB_RETRY_CONFIG)
        on_retry: Optional callback called before each retry (receives exception, attempt, delay)
        retryable_exceptions: Tuple of exception types that trigger retry (default: all)
        *args: Positional arguments to pass to operation
        **kwargs: Keyword arguments to pass to operation

    Returns:
        Result of successful operation

    Raises:
        Last exception if all retry attempts fail

    Example:
        >>> async def fetch_user(user_id: int) -> dict:
        ...     # Database operation that might fail temporarily
        ...     return await db.get_user(user_id)
        ...
        >>> user = await async_retry(fetch_user, 123, config=DB_RETRY_CONFIG)
    """
    last_exception = None

    for attempt in range(config.max_attempts):
        try:
            # Attempt the operation
            result = await operation(*args, **kwargs)
            if attempt > 0:
                logger.debug(f"Operation succeeded on attempt {attempt + 1}/{config.max_attempts}")
            return result

        except retryable_exceptions as e:
            last_exception = e
            is_final_attempt = attempt == config.max_attempts - 1

            if is_final_attempt:
                # Last attempt failed - don't retry anymore
                logger.error(
                    f"Operation failed permanently after {config.max_attempts} attempts: "
                    f"{type(e).__name__}: {str(e)}"
                )
                raise

            # Calculate delay for next attempt
            delay = config.get_delay(attempt + 1)

            logger.warning(
                f"Operation failed on attempt {attempt + 1}/{config.max_attempts}: "
                f"{type(e).__name__}. Retrying in {delay:.2f}s..."
            )

            # Call optional callback
            if on_retry:
                await on_retry(e, attempt + 1, delay)

            # Sleep before retry
            await asyncio.sleep(delay)

        except Exception as e:
            # Non-retryable exception
            logger.error(f"Unexpected error (not retried): {type(e).__name__}: {str(e)}")
            raise

    # Should not reach here, but handle just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("Retry loop completed without result or exception")


async def with_connection_retry(
    pool_acquire_func: Callable[[], Awaitable[Any]],
    query_func: Callable[[Any], Awaitable[T]],
    operation_name: str = "database operation",
) -> T:
    """
    Execute a database operation with connection retry logic.

    Handles transient connection pool exhaustion and connection timeout errors.

    Args:
        pool_acquire_func: Async function that acquires a connection from pool
        query_func: Async function that executes query on connection
        operation_name: Name of operation for logging

    Returns:
        Result of successful query

    Example:
        >>> async def query_user(conn):
        ...     return await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
        ...
        >>> result = await with_connection_retry(
        ...     lambda: pool.acquire(),
        ...     query_user,
        ...     "fetch user"
        ... )
    """

    async def _execute() -> T:
        async with pool_acquire_func() as conn:
            return await query_func(conn)

    try:
        return await async_retry(
            _execute,
            config=DB_RETRY_CONFIG,
            retryable_exceptions=(asyncio.TimeoutError, ConnectionError, RuntimeError),
        )
    except asyncio.TimeoutError:
        logger.error(f"Connection timeout during {operation_name}")
        raise
    except ConnectionError as e:
        logger.error(f"Connection error during {operation_name}: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed {operation_name} after retries: {e}")
        raise


def should_retry_exception(exception: Exception) -> bool:
    """
    Determine if an exception is transient and should trigger retry.

    Args:
        exception: Exception to evaluate

    Returns:
        True if exception is transient, False if permanent
    """
    exception_str = str(exception).lower()
    exception_type = type(exception).__name__

    # Transient errors that should be retried
    transient_indicators = [
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "too many connections",
        "pool exhausted",
        "temporarily unable",
        "operational error",  # Many DB errors use this
        "sqlstate x0a000",  # Feature not supported (sometimes transient)
    ]

    # Check if exception message contains transient indicators
    for indicator in transient_indicators:
        if indicator in exception_str:
            return True

    # Check exception type
    if exception_type in ("TimeoutError", "ConnectionError", "asyncio.TimeoutError"):
        return True

    return False


class RetryStats:
    """Track retry statistics for monitoring."""

    def __init__(self):
        self.total_operations = 0
        self.successful_first_try = 0
        self.successful_after_retry = 0
        self.failed_permanently = 0
        self.total_retries = 0
        self.last_failure_reason = None
        self.last_failure_time = None

    def record_success(self, on_first_try: bool = True):
        """Record a successful operation."""
        self.total_operations += 1
        if on_first_try:
            self.successful_first_try += 1
        else:
            self.successful_after_retry += 1

    def record_failure(self, reason: str):
        """Record a permanent failure."""
        self.total_operations += 1
        self.failed_permanently += 1
        self.last_failure_reason = reason
        self.last_failure_time = datetime.now(timezone.utc)

    def record_retry(self):
        """Record a retry attempt."""
        self.total_retries += 1

    def get_stats(self) -> dict:
        """Get retry statistics."""
        success_rate = (
            (self.successful_first_try + self.successful_after_retry) / self.total_operations
            if self.total_operations > 0
            else 0.0
        )
        return {
            "total_operations": self.total_operations,
            "successful_first_try": self.successful_first_try,
            "successful_after_retry": self.successful_after_retry,
            "failed_permanently": self.failed_permanently,
            "total_retries": self.total_retries,
            "success_rate": round(success_rate, 4),
            "last_failure_reason": self.last_failure_reason,
            "last_failure_time": (
                self.last_failure_time.isoformat() if self.last_failure_time else None
            ),
        }


# Global retry stats tracker
retry_stats = RetryStats()
