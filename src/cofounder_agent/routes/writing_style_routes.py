"""
Writing Style Routes

API endpoints for managing user writing samples for RAG-style matching.
Allows users to upload/manage writing samples that are used to guide LLM generation.

Endpoints:
- POST /api/writing-style/upload - Upload new writing sample
- GET /api/writing-style/samples - List user's writing samples
- GET /api/writing-style/active - Get active writing sample
- PUT /api/writing-style/{sample_id}/set-active - Set as active sample
- PUT /api/writing-style/{sample_id} - Update sample
- DELETE /api/writing-style/{sample_id} - Delete sample
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from services.database_service import DatabaseService
from routes.auth_unified import get_current_user
from utils.route_utils import get_database_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writing-style", tags=["writing-style"])


# ============================================================================
# Pydantic Schemas
# ============================================================================

class WritingSampleRequest(BaseModel):
    """Request to create/update writing sample"""
    title: str
    description: Optional[str] = None
    content: str
    set_as_active: bool = False


class WritingSampleResponse(BaseModel):
    """Response containing writing sample data"""
    id: str
    user_id: str
    title: str
    description: str
    content: str
    is_active: bool
    word_count: int
    char_count: int
    metadata: Dict[str, Any]
    created_at: Optional[str]
    updated_at: Optional[str]


class WritingSamplesListResponse(BaseModel):
    """Response containing list of samples"""
    samples: List[WritingSampleResponse]
    total_count: int
    active_sample_id: Optional[str]


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/upload", response_model=WritingSampleResponse)
async def upload_writing_sample(
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    set_as_active: bool = Form(False),
):
    """
    Upload a new writing sample.
    
    Can either provide:
    - Raw text via 'content' form field
    - File upload via 'file' parameter (.txt, .md files)
    
    Args:
        title: Sample title/name
        description: Optional description
        content: Writing sample text (if not uploading file)
        file: File upload (if not providing raw text)
        set_as_active: Whether to set this as active sample for the user
        
    Returns:
        Created WritingSampleResponse
    """
    try:
        # Validate that we have content either from form or file
        sample_content = content
        if file:
            if file.size and file.size > 1_000_000:  # 1MB limit
                raise HTTPException(status_code=400, detail="File too large (max 1MB)")
            
            file_content = await file.read()
            sample_content = file_content.decode("utf-8")
        
        if not sample_content or not sample_content.strip():
            raise HTTPException(status_code=400, detail="Sample content is required (provide content or file)")
        
        # Create the writing sample
        user_id = current_user.get("id") if isinstance(current_user, dict) else current_user
        sample = await db_service.writing_style.create_writing_sample(
            user_id=user_id,
            title=title,
            content=sample_content.strip(),
            description=description,
            set_as_active=set_as_active
        )
        
        logger.info(f"✅ User {user_id} uploaded writing sample: {title}")
        return WritingSampleResponse(**sample)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error uploading writing sample: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload sample: {str(e)}")


@router.get("/samples", response_model=WritingSamplesListResponse)
async def list_writing_samples(
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get all writing samples for the current user.
    
    Returns:
        List of WritingSampleResponse objects
    """
    try:
        user_id = current_user.get("id") if isinstance(current_user, dict) else current_user
        samples = await db_service.writing_style.get_user_writing_samples(user_id)
        
        # Find active sample if any
        active_sample_id = None
        for sample in samples:
            if sample.get("is_active"):
                active_sample_id = sample.get("id")
                break
        
        return WritingSamplesListResponse(
            samples=[WritingSampleResponse(**s) for s in samples],
            total_count=len(samples),
            active_sample_id=active_sample_id
        )
        
    except Exception as e:
        logger.error(f"❌ Error listing writing samples: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list samples: {str(e)}")


@router.get("/active", response_model=Optional[WritingSampleResponse])
async def get_active_writing_sample(
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get the currently active writing sample for the user.
    
    Returns:
        Active WritingSampleResponse or null if no active sample
    """
    try:
        user_id = current_user.get("id") if isinstance(current_user, dict) else current_user
        sample = await db_service.writing_style.get_active_writing_sample(user_id)
        
        if not sample:
            return None
        
        return WritingSampleResponse(**sample)
        
    except Exception as e:
        logger.error(f"❌ Error getting active writing sample: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get active sample: {str(e)}")


@router.put("/{sample_id}/set-active", response_model=WritingSampleResponse)
async def set_active_writing_sample(
    sample_id: str,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Set a writing sample as the active one for the user.
    
    Args:
        sample_id: ID of sample to activate
        
    Returns:
        Updated WritingSampleResponse
    """
    try:
        user_id = current_user.get("id") if isinstance(current_user, dict) else current_user
        # Verify sample belongs to user
        sample = await db_service.writing_style.get_writing_sample(sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail="Writing sample not found")
        
        if sample.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        # Set as active
        updated = await db_service.writing_style.set_active_writing_sample(user_id, sample_id)
        
        logger.info(f"✅ User {user_id} set writing sample {sample_id} as active")
        return WritingSampleResponse(**updated)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error setting active writing sample: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to set active sample: {str(e)}")


@router.put("/{sample_id}", response_model=WritingSampleResponse)
async def update_writing_sample(
    sample_id: str,
    request: WritingSampleRequest,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Update a writing sample.
    
    Args:
        sample_id: Sample ID to update
        request: Updated sample data
        
    Returns:
        Updated WritingSampleResponse
    """
    try:
        user_id = current_user.get("id") if isinstance(current_user, dict) else current_user
        # Verify sample belongs to user
        sample = await db_service.writing_style.get_writing_sample(sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail="Writing sample not found")
        
        if sample.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        # Update the sample
        updated = await db_service.writing_style.update_writing_sample(
            sample_id=sample_id,
            user_id=user_id,
            title=request.title,
            description=request.description,
            content=request.content
        )
        
        logger.info(f"✅ User {user_id} updated writing sample {sample_id}")
        return WritingSampleResponse(**updated)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error updating writing sample: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update sample: {str(e)}")


@router.delete("/{sample_id}")
async def delete_writing_sample(
    sample_id: str,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Delete a writing sample.
    
    Args:
        sample_id: Sample ID to delete
        
    Returns:
        Success message
    """
    try:
        user_id = current_user.get("id") if isinstance(current_user, dict) else current_user
        # Verify sample belongs to user
        sample = await db_service.writing_style.get_writing_sample(sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail="Writing sample not found")
        
        if sample.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        # Delete the sample
        success = await db_service.writing_style.delete_writing_sample(sample_id, user_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Writing sample not found")
        
        logger.info(f"✅ User {user_id} deleted writing sample {sample_id}")
        return {"status": "deleted", "sample_id": sample_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error deleting writing sample: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete sample: {str(e)}")
