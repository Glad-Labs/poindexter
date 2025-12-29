"""
Service Dependency Injection Setup

Provides Depends() functions for FastAPI routes to access unified services.

Usage in routes:
```python
from fastapi import Depends
from utils.service_dependencies import (
    get_unified_orchestrator,
    get_quality_service,
    get_database_service
)

@router.post("/content/generate")
async def generate_content(
    request: Request,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
    quality_service: UnifiedQualityService = Depends(get_quality_service),
):
    # Services are available as parameters
    result = await orchestrator.process_request(...)
    return result
```
"""

import logging
from fastapi import Request, HTTPException
from services.unified_orchestrator import UnifiedOrchestrator
from services.quality_service import UnifiedQualityService
from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


def get_unified_orchestrator(request: Request) -> UnifiedOrchestrator:
    """Get UnifiedOrchestrator from app state"""
    orchestrator = getattr(request.app.state, "unified_orchestrator", None)
    if not orchestrator:
        logger.error("UnifiedOrchestrator not initialized in app state")
        raise HTTPException(status_code=500, detail="Service not initialized: UnifiedOrchestrator")
    return orchestrator


def get_quality_service(request: Request) -> UnifiedQualityService:
    """Get UnifiedQualityService from app state"""
    service = getattr(request.app.state, "quality_service", None)
    if not service:
        logger.error("UnifiedQualityService not initialized in app state")
        raise HTTPException(
            status_code=500, detail="Service not initialized: UnifiedQualityService"
        )
    return service


def get_database_service(request: Request) -> DatabaseService:
    """Get DatabaseService from app state"""
    service = getattr(request.app.state, "db_service", None) or getattr(
        request.app.state, "database", None
    )
    if not service:
        logger.error("DatabaseService not initialized in app state")
        raise HTTPException(status_code=500, detail="Service not initialized: DatabaseService")
    return service
