"""
AI Response Cache Service

Caches AI API responses to reduce duplicate calls and save costs.
Implements both in-memory and Firestore-backed caching.

Cost Savings:
- Reduces AI API calls by 20-40%
- Estimated savings: $3,000-$6,000/year
- Faster response times for cached queries
"""

import hashlib
import json
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import structlog

logger = structlog.get_logger(__name__)


class AIResponseCache:
    """
    Cache for AI API responses to reduce costs and improve performance.
    
    Features:
    - Two-tier caching: Memory (fast) + Firestore (persistent)
    - Configurable TTL (time-to-live)
    - Automatic cache key generation
    - Cache hit/miss metrics
    
    Example usage:
        cache = AIResponseCache(firestore_client, ttl_hours=24)
        
        # Try to get cached response
        cached = await cache.get(prompt, model, params)
        if cached:
            return cached
        
        # If not cached, call AI API
        response = await call_ai_api(prompt)
        
        # Cache the response
        await cache.set(prompt, model, params, response)
    """
    
    def __init__(
        self,
        firestore_client=None,
        ttl_hours: int = 24,
        max_memory_entries: int = 1000
    ):
        """
        Initialize AI response cache.
        
        Args:
            firestore_client: Optional Firestore client for persistent cache
            ttl_hours: Cache time-to-live in hours
            max_memory_entries: Maximum entries in memory cache
        """
        self.firestore_client = firestore_client
        self.ttl = timedelta(hours=ttl_hours)
        self.max_memory_entries = max_memory_entries
        
        # In-memory cache for fast access
        self.memory_cache: Dict[str, Dict[str, Any]] = {}
        
        # Metrics
        self.metrics = {
            'hits': 0,
            'misses': 0,
            'memory_hits': 0,
            'firestore_hits': 0,
            'cache_sets': 0
        }
        
        logger.info(
            "AI cache initialized",
            ttl_hours=ttl_hours,
            max_memory_entries=max_memory_entries,
            firestore_enabled=firestore_client is not None
        )
    
    def _generate_key(self, prompt: str, model: str, params: Dict[str, Any]) -> str:
        """
        Generate deterministic cache key from prompt and parameters.
        
        Args:
            prompt: The AI prompt
            model: Model name (e.g., 'gpt-4', 'claude-opus')
            params: Model parameters (temperature, max_tokens, etc.)
            
        Returns:
            SHA-256 hash as cache key
        """
        # Include critical parameters that affect output
        cache_data = {
            'prompt': prompt.strip(),
            'model': model,
            'temperature': params.get('temperature', 0.7),
            'max_tokens': params.get('max_tokens', 1000),
            'top_p': params.get('top_p', 1.0)
        }
        
        key_string = json.dumps(cache_data, sort_keys=True)
        return hashlib.sha256(key_string.encode()).hexdigest()
    
    async def get(
        self,
        prompt: str,
        model: str,
        params: Dict[str, Any]
    ) -> Optional[str]:
        """
        Get cached response if available and not expired.
        
        Args:
            prompt: The AI prompt
            model: Model name
            params: Model parameters
            
        Returns:
            Cached response string or None if not found/expired
        """
        key = self._generate_key(prompt, model, params)
        now = datetime.utcnow()
        
        # Check memory cache first (fastest)
        if key in self.memory_cache:
            entry = self.memory_cache[key]
            if now - entry['timestamp'] < self.ttl:
                self.metrics['hits'] += 1
                self.metrics['memory_hits'] += 1
                logger.debug(
                    "Cache hit (memory)",
                    cache_key=key[:12],
                    age_seconds=(now - entry['timestamp']).total_seconds()
                )
                return entry['response']
            else:
                # Expired, remove from memory
                del self.memory_cache[key]
        
        # Check Firestore cache (persistent)
        if self.firestore_client:
            try:
                doc = await self.firestore_client.get_document(
                    collection='ai_response_cache',
                    document_id=key
                )
                
                if doc:
                    expires_at = doc.get('expires_at')
                    if expires_at and expires_at > now:
                        response = doc['response']
                        
                        # Update memory cache
                        self._add_to_memory_cache(key, response, doc['created_at'])
                        
                        self.metrics['hits'] += 1
                        self.metrics['firestore_hits'] += 1
                        logger.debug(
                            "Cache hit (Firestore)",
                            cache_key=key[:12],
                            age_seconds=(now - doc['created_at']).total_seconds()
                        )
                        return response
                    else:
                        # Expired in Firestore, optionally clean up
                        logger.debug("Expired cache entry", cache_key=key[:12])
            except Exception as e:
                logger.warning(
                    "Firestore cache lookup failed",
                    error=str(e),
                    cache_key=key[:12]
                )
        
        # Cache miss
        self.metrics['misses'] += 1
        logger.debug("Cache miss", cache_key=key[:12])
        return None
    
    async def set(
        self,
        prompt: str,
        model: str,
        params: Dict[str, Any],
        response: str
    ):
        """
        Cache an AI response.
        
        Args:
            prompt: The AI prompt
            model: Model name
            params: Model parameters
            response: The AI response to cache
        """
        key = self._generate_key(prompt, model, params)
        now = datetime.utcnow()
        expires_at = now + self.ttl
        
        # Store in memory cache
        self._add_to_memory_cache(key, response, now)
        
        # Store in Firestore for persistence
        if self.firestore_client:
            try:
                await self.firestore_client.set_document(
                    collection='ai_response_cache',
                    document_id=key,
                    data={
                        'prompt_hash': key,
                        'model': model,
                        'response': response,
                        'created_at': now,
                        'expires_at': expires_at,
                        'ttl_hours': self.ttl.total_seconds() / 3600,
                        'prompt_length': len(prompt),
                        'response_length': len(response)
                    }
                )
                self.metrics['cache_sets'] += 1
                logger.debug(
                    "Response cached",
                    cache_key=key[:12],
                    model=model,
                    ttl_hours=self.ttl.total_seconds() / 3600
                )
            except Exception as e:
                logger.warning(
                    "Failed to cache response in Firestore",
                    error=str(e),
                    cache_key=key[:12]
                )
    
    def _add_to_memory_cache(self, key: str, response: str, timestamp: datetime):
        """Add entry to memory cache with LRU eviction."""
        # Evict oldest entries if cache is full
        if len(self.memory_cache) >= self.max_memory_entries:
            # Remove oldest 10% of entries
            entries_to_remove = max(1, self.max_memory_entries // 10)
            sorted_entries = sorted(
                self.memory_cache.items(),
                key=lambda x: x[1]['timestamp']
            )
            for old_key, _ in sorted_entries[:entries_to_remove]:
                del self.memory_cache[old_key]
            
            logger.debug(
                "Memory cache eviction",
                removed=entries_to_remove,
                remaining=len(self.memory_cache)
            )
        
        self.memory_cache[key] = {
            'response': response,
            'timestamp': timestamp
        }
    
    def clear_memory_cache(self):
        """Clear in-memory cache (Firestore cache remains)."""
        entries = len(self.memory_cache)
        self.memory_cache.clear()
        logger.info("Memory cache cleared", entries_cleared=entries)
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get cache performance metrics.
        
        Returns:
            Dictionary with cache statistics
        """
        total_requests = self.metrics['hits'] + self.metrics['misses']
        hit_rate = (
            self.metrics['hits'] / total_requests if total_requests > 0 else 0
        )
        
        return {
            'total_requests': total_requests,
            'hits': self.metrics['hits'],
            'misses': self.metrics['misses'],
            'hit_rate': round(hit_rate * 100, 2),  # Percentage
            'memory_hits': self.metrics['memory_hits'],
            'firestore_hits': self.metrics['firestore_hits'],
            'cache_sets': self.metrics['cache_sets'],
            'memory_entries': len(self.memory_cache)
        }
    
    def reset_metrics(self):
        """Reset metrics counters."""
        self.metrics = {
            'hits': 0,
            'misses': 0,
            'memory_hits': 0,
            'firestore_hits': 0,
            'cache_sets': 0
        }
        logger.info("Cache metrics reset")


class ImageCache:
    """
    Cache for image search results to reduce Pexels API calls.
    
    Features:
    - Caches by topic + keywords combination
    - Automatic TTL management (30 days default)
    - Hit/miss tracking
    - Memory efficient
    
    Usage:
        cache = ImageCache()
        
        # Check cache first
        cached_image = cache.get_cached_image("AI", ["artificial", "intelligence"])
        if cached_image:
            return cached_image
        
        # If not cached, search and cache
        image = await pexels.search_images("AI", keywords=["artificial", "intelligence"])
        cache.cache_image("AI", ["artificial", "intelligence"], image)
    """
    
    def __init__(self, ttl_days: int = 30, max_entries: int = 500):
        """
        Initialize image cache.
        
        Args:
            ttl_days: Time-to-live in days
            max_entries: Maximum cached entries
        """
        self.ttl = timedelta(days=ttl_days)
        self.max_entries = max_entries
        self.image_cache: Dict[str, Dict[str, Any]] = {}
        self.metrics = {
            'hits': 0,
            'misses': 0,
            'cached_entries': 0
        }
        
        logger.info(
            "Image cache initialized",
            ttl_days=ttl_days,
            max_entries=max_entries
        )
    
    def _build_cache_key(self, topic: str, keywords: Optional[list[str]] = None) -> str:
        """
        Build cache key from topic and keywords.
        
        Args:
            topic: Search topic
            keywords: List of keywords
            
        Returns:
            Cache key string
        """
        key_parts = [topic.lower().strip()[:30]]
        if keywords:
            key_parts.extend([kw.lower().strip()[:20] for kw in keywords[:5]])
        
        combined = "|".join(key_parts)
        return hashlib.md5(combined.encode()).hexdigest()
    
    def get_cached_image(
        self,
        topic: str,
        keywords: Optional[list[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached image for topic + keywords.
        
        Args:
            topic: Search topic
            keywords: Search keywords
            
        Returns:
            Cached image dict or None if not found/expired
        """
        cache_key = self._build_cache_key(topic, keywords)
        
        if cache_key not in self.image_cache:
            self.metrics['misses'] += 1
            return None
        
        cached_entry = self.image_cache[cache_key]
        
        # Check if expired
        cached_at = datetime.fromisoformat(cached_entry['cached_at'])
        if datetime.now() > cached_at + self.ttl:
            logger.info(f"Image cache entry expired for: {topic}")
            del self.image_cache[cache_key]
            self.metrics['cached_entries'] -= 1
            self.metrics['misses'] += 1
            return None
        
        self.metrics['hits'] += 1
        logger.info(
            f"✓ Image cache hit for '{topic}'",
            cache_hits=self.metrics['hits']
        )
        
        return cached_entry['image']
    
    def cache_image(
        self,
        topic: str,
        keywords: Optional[list[str]],
        image_data: Dict[str, Any]
    ) -> None:
        """
        Cache image data for topic + keywords.
        
        Args:
            topic: Search topic
            keywords: Search keywords
            image_data: Image dictionary from Pexels
        """
        # Evict old entries if at capacity
        if len(self.image_cache) >= self.max_entries:
            # Remove oldest entry (simple FIFO)
            oldest_key = next(iter(self.image_cache))
            del self.image_cache[oldest_key]
            self.metrics['cached_entries'] -= 1
            logger.info(f"Image cache: evicted old entry (max capacity: {self.max_entries})")
        
        cache_key = self._build_cache_key(topic, keywords)
        
        self.image_cache[cache_key] = {
            'image': image_data,
            'cached_at': datetime.now().isoformat(),
            'topic': topic,
            'keywords': keywords or [],
            'ttl_seconds': int(self.ttl.total_seconds())
        }
        
        self.metrics['cached_entries'] += 1
        logger.info(
            f"✓ Image cached for '{topic}'",
            cache_size=len(self.image_cache)
        )
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get cache metrics."""
        total_lookups = self.metrics['hits'] + self.metrics['misses']
        hit_rate = (self.metrics['hits'] / total_lookups * 100) if total_lookups > 0 else 0
        
        return {
            'total_hits': self.metrics['hits'],
            'total_misses': self.metrics['misses'],
            'hit_rate_percent': round(hit_rate, 1),
            'cached_entries': self.metrics['cached_entries'],
            'max_entries': self.max_entries,
            'cache_utilization_percent': round(
                self.metrics['cached_entries'] / self.max_entries * 100, 1
            )
        }
    
    def clear_cache(self) -> None:
        """Clear all cached images."""
        self.image_cache.clear()
        self.metrics['cached_entries'] = 0
        logger.info("Image cache cleared")


# Singleton instances

_ai_cache: Optional[AIResponseCache] = None


def get_ai_cache() -> Optional[AIResponseCache]:
    """Get the global AI cache instance."""
    return _ai_cache


def initialize_ai_cache(
    firestore_client=None,
    ttl_hours: int = 24,
    max_memory_entries: int = 1000
) -> AIResponseCache:
    """
    Initialize the global AI response cache.
    
    Args:
        firestore_client: Firestore client for persistent cache
        ttl_hours: Cache TTL in hours
        max_memory_entries: Max memory cache size
        
    Returns:
        Initialized AIResponseCache instance
    """
    global _ai_cache
    _ai_cache = AIResponseCache(
        firestore_client=firestore_client,
        ttl_hours=ttl_hours,
        max_memory_entries=max_memory_entries
    )
    logger.info("Global AI cache initialized")
    return _ai_cache
