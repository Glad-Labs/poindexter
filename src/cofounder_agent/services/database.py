"""
Database Service Base Class for Glad Labs AI Co-Founder

This module provides a base class for database services.
"""

from typing import Any, Dict, Optional
from abc import ABC, abstractmethod

# Import configuration
from config import get_config

# Get configuration
config = get_config()


class DatabaseService(ABC):
    """Base class for database services."""
    
    def __init__(self):
        self.config = config
    
    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on the database."""
        pass
    
    @abstractmethod
    async def get_metrics(self) -> Dict[str, Any]:
        """Get database metrics."""
        pass
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the database service."""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the database service."""
        pass