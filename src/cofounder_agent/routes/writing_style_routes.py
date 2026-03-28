"""
Writing Style Routes

API endpoints for managing user writing samples for RAG-style matching.
Allows users to upload/manage writing samples that are used to guide LLM generation.

Endpoints:
- POST /api/writing-style/upload - Upload new writing sample
- GET /api/writing-style/samples - List user's writing samples
- GET /api/writing-style/active - Get active writing sample
- POST /api/writing-style/{sample_id}/activate - Activate sample (deactivates all others)
- PUT /api/writing-style/{sample_id} - Update sample
- DELETE /api/writing-style/{sample_id} - Delete sample
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from middleware.api_token_auth import OPERATOR_ID, verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
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
    total: int  # project-standard field name; total_count alias removed (issue #604)
    active_sample_id: Optional[str]
    offset: int = 0
    limit: int = 100


# ============================================================================
# Helpers
# ============================================================================


def _require_writing_style_service(db_service: DatabaseService):
    """Raise 503 if the writing_style sub-service was not initialised (no DB pool)."""
    if db_service.writing_style is None:
        raise HTTPException(
            status_code=503,
            detail="Writing style service unavailable — database not initialised",
        )
    return db_service.writing_style


def _get_user_id(_current_user=None) -> str:
    """Return the fixed operator ID for solo-operator mode."""
    return OPERATOR_ID


# ============================================================================
# Endpoints
# ============================================================================


@router.post("/upload", response_model=WritingSampleResponse)
async def upload_writing_sample(
    token: str = Depends(verify_api_token),
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
            # Validate file type
            allowed_types = {"text/plain", "text/markdown", "application/octet-stream"}
            if file.content_type not in allowed_types:
                logger.warning(f"File upload rejected: invalid content type {file.content_type}")
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid file type. Allowed: .txt, .md. Got: {file.content_type}",
                )

            # Validate file size
            if file.size and file.size > 1_000_000:  # 1MB limit
                logger.warning(f"File upload rejected: file too large ({file.size} bytes)")
                raise HTTPException(status_code=413, detail="File too large (max 1MB)")

            file_content = await file.read()

            # Validate file can be decoded as text
            try:
                sample_content = file_content.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning("File upload rejected: file is not valid UTF-8 text", exc_info=True)
                raise HTTPException(status_code=422, detail="File must be valid UTF-8 text") from exc

        if not sample_content or not sample_content.strip():
            raise HTTPException(
                status_code=400, detail="Sample content is required (provide content or file)"
            )

        # Create the writing sample
        user_id = _get_user_id()
        sample = await _require_writing_style_service(db_service).create_writing_sample(
            user_id=user_id,
            title=title,
            content=sample_content.strip(),
            description=description,
            set_as_active=set_as_active,
        )

        logger.info(f"✅ User {user_id} uploaded writing sample: {title}")
        return WritingSampleResponse(**sample)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error uploading writing sample: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to upload sample") from e


@router.get("/samples", response_model=WritingSamplesListResponse)
async def list_writing_samples(
    offset: int = Query(0, ge=0, description="Number of samples to skip"),
    limit: int = Query(20, ge=1, le=100, description="Maximum samples to return"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get writing samples for the current user with optional pagination.

    Returns:
        Paginated list of WritingSampleResponse objects
    """
    try:
        user_id = _get_user_id()
        all_samples = await _require_writing_style_service(db_service).get_user_writing_samples(
            user_id
        )

        total = len(all_samples)
        # Apply pagination
        paginated = all_samples[offset : offset + limit]

        # Find active sample if any
        active_sample_id = None
        for sample in all_samples:
            if sample.get("is_active"):
                active_sample_id = sample.get("id")
                break

        return WritingSamplesListResponse(
            samples=[WritingSampleResponse(**s) for s in paginated],
            total=total,
            active_sample_id=active_sample_id,
            offset=offset,
            limit=limit,
        )

    except Exception as e:
        logger.error(f"❌ Error listing writing samples: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list samples") from e


@router.get("/active", response_model=Optional[WritingSampleResponse])
async def get_active_writing_sample(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get the currently active writing sample for the user.

    Returns:
        Active WritingSampleResponse or null if no active sample
    """
    try:
        user_id = _get_user_id()
        sample = await _require_writing_style_service(db_service).get_active_writing_sample(user_id)

        if not sample:
            return None

        return WritingSampleResponse(**sample)

    except Exception as e:
        logger.error(f"❌ Error getting active writing sample: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get active sample") from e


@router.post("/{sample_id}/activate", response_model=WritingSampleResponse)
async def activate_writing_sample(
    sample_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Activate a writing sample as the user's current active style.

    Activating a sample deactivates all other samples for the user — this is
    a state-machine transition, not a field update, so POST is the correct method.

    Args:
        sample_id: ID of sample to activate

    Returns:
        Updated WritingSampleResponse
    """
    try:
        user_id = _get_user_id()
        # Verify sample belongs to user
        sample = await _require_writing_style_service(db_service).get_writing_sample(sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail="Writing sample not found")

        if sample.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")

        # Set as active
        updated = await _require_writing_style_service(db_service).set_active_writing_sample(
            user_id, sample_id
        )

        logger.info(f"✅ User {user_id} set writing sample {sample_id} as active")
        return WritingSampleResponse(**updated)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error setting active writing sample: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set active sample") from e


@router.put("/{sample_id}", response_model=WritingSampleResponse)
async def update_writing_sample(
    sample_id: str,
    request: WritingSampleRequest,
    token: str = Depends(verify_api_token),
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
        user_id = _get_user_id()
        # Verify sample belongs to user
        sample = await _require_writing_style_service(db_service).get_writing_sample(sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail="Writing sample not found")

        if sample.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")

        # Update the sample
        updated = await _require_writing_style_service(db_service).update_writing_sample(
            sample_id=sample_id,
            user_id=user_id,
            title=request.title,
            description=request.description,
            content=request.content,
        )

        logger.info(f"✅ User {user_id} updated writing sample {sample_id}")
        return WritingSampleResponse(**updated)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error updating writing sample: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update sample") from e


@router.delete("/{sample_id}", status_code=204)
async def delete_writing_sample(
    sample_id: str,
    token: str = Depends(verify_api_token),
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
        user_id = _get_user_id()
        # Verify sample belongs to user
        sample = await _require_writing_style_service(db_service).get_writing_sample(sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail="Writing sample not found")

        if sample.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Unauthorized")

        # Delete the sample
        success = await _require_writing_style_service(db_service).delete_writing_sample(
            sample_id, user_id
        )

        if not success:
            raise HTTPException(status_code=404, detail="Writing sample not found")

        logger.info(f"✅ User {user_id} deleted writing sample {sample_id}")
        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error deleting writing sample: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete sample") from e


# ============================================================================
# Phase 3.4: RAG Retrieval Endpoints
# ============================================================================


def _calculate_topic_similarity(content: str, query: str) -> float:
    """Calculate topic similarity using Jaccard index (keyword overlap)"""
    import re

    # Extract keywords
    query_words = set(w.lower() for w in re.findall(r"\b\w+\b", query) if len(w) > 2)
    content_words = set(w.lower() for w in re.findall(r"\b\w+\b", content) if len(w) > 2)

    if not query_words or not content_words:
        return 0.0

    # Jaccard similarity
    intersection = len(query_words & content_words)
    union = len(query_words | content_words)
    return intersection / union if union > 0 else 0.0


@router.get("/relevant")
async def get_relevant_samples(
    query_topic: str,
    preferred_style: Optional[str] = None,
    preferred_tone: Optional[str] = None,
    limit: int = 3,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict:
    """
    Retrieve writing samples relevant to a topic using RAG.

    Uses Jaccard similarity to find topically related samples,
    with optional style and tone filtering.

    **Parameters:**
    - `query_topic`: Topic/keywords to find relevant samples
    - `preferred_style`: Optional style filter (technical, narrative, etc.)
    - `preferred_tone`: Optional tone filter (formal, casual, etc.)
    - `limit`: Number of samples to return (1-10, default 3)

    **Response:**
    ```json
    {
      "query_topic": "AI in healthcare",
      "found_samples": 2,
      "samples": [
        {
          "id": "uuid",
          "title": "Healthcare AI Article",
          "style": "technical",
          "tone": "formal",
          "word_count": 1500,
          "relevance_score": 0.75
        }
      ],
      "message": "2 samples found"
    }
    ```
    """
    try:
        user_id = OPERATOR_ID

        # Get all user samples
        samples = await _require_writing_style_service(db_service).get_user_writing_samples(user_id)

        if not samples:
            return {
                "query_topic": query_topic,
                "found_samples": 0,
                "samples": [],
                "message": "No writing samples available",
            }

        # Score each sample by relevance
        scored = []
        for sample in samples:
            # Calculate topic similarity
            similarity = _calculate_topic_similarity(sample.get("content", ""), query_topic)

            # Reduce score if style doesn't match
            sample_style = (
                sample.get("metadata", {}).get("style") if sample.get("metadata") else None
            )
            if preferred_style and sample_style != preferred_style:
                similarity *= 0.7  # 30% penalty for style mismatch

            # Reduce score if tone doesn't match
            sample_tone = sample.get("metadata", {}).get("tone") if sample.get("metadata") else None
            if preferred_tone and sample_tone != preferred_tone:
                similarity *= 0.7  # 30% penalty for tone mismatch

            scored.append(
                {
                    "id": sample.get("id"),
                    "title": sample.get("title"),
                    "style": sample_style,
                    "tone": sample_tone,
                    "word_count": sample.get("word_count", 0),
                    "relevance_score": similarity,
                }
            )

        # Sort by relevance and limit
        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        top_samples = scored[:limit]

        logger.info(f"✅ Retrieved {len(top_samples)} relevant samples for user {user_id}")

        return {
            "query_topic": query_topic,
            "found_samples": len(top_samples),
            "samples": [
                {**s, "relevance_score": round(s["relevance_score"], 2)} for s in top_samples
            ],
            "message": f"{len(top_samples)} sample(s) found matching your topic",
        }

    except Exception as e:
        logger.error(f"❌ Error retrieving samples: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve samples") from e


@router.get("/by-style/{style}")
async def retrieve_by_style(
    style: str,
    limit: int = 5,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict:
    """
    Retrieve writing samples filtered by specific style.

    **Supported Styles:**
    - technical: Technical/instructional writing
    - narrative: Storytelling/narrative
    - listicle: List-based articles
    - educational: Educational/explanatory
    - thought-leadership: Opinion/leadership pieces

    **Response:**
    ```json
    {
      "style": "technical",
      "found_samples": 2,
      "samples": [
        {
          "id": "uuid",
          "title": "Technical Article",
          "tone": "authoritative",
          "word_count": 2000
        }
      ]
    }
    ```
    """
    try:
        user_id = OPERATOR_ID

        # Get all user samples
        samples = await _require_writing_style_service(db_service).get_user_writing_samples(user_id)

        # Filter by style
        matching = []
        for sample in samples:
            sample_style = (
                sample.get("metadata", {}).get("style") if sample.get("metadata") else None
            )
            if sample_style and sample_style.lower() == style.lower():
                matching.append(
                    {
                        "id": sample.get("id"),
                        "title": sample.get("title"),
                        "tone": (
                            sample.get("metadata", {}).get("tone")
                            if sample.get("metadata")
                            else None
                        ),
                        "word_count": sample.get("word_count", 0),
                    }
                )

        # Limit results
        matching = matching[:limit]

        logger.info(f"✅ Retrieved {len(matching)} samples with style '{style}' for user {user_id}")

        return {"style": style, "found_samples": len(matching), "samples": matching}

    except Exception as e:
        logger.error(f"❌ Error retrieving samples by style: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve samples") from e


@router.get("/by-tone/{tone}")
async def retrieve_by_tone(
    tone: str,
    limit: int = 5,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict:
    """
    Retrieve writing samples filtered by specific tone.

    **Supported Tones:**
    - formal: Formal/professional
    - casual: Casual/conversational
    - authoritative: Expert/authoritative
    - conversational: Friendly/conversational

    **Response:**
    ```json
    {
      "tone": "authoritative",
      "found_samples": 3,
      "samples": [
        {
          "id": "uuid",
          "title": "Expert Article",
          "style": "technical",
          "word_count": 1500
        }
      ]
    }
    ```
    """
    try:
        user_id = OPERATOR_ID

        # Get all user samples
        samples = await _require_writing_style_service(db_service).get_user_writing_samples(user_id)

        # Filter by tone
        matching = []
        for sample in samples:
            sample_tone = sample.get("metadata", {}).get("tone") if sample.get("metadata") else None
            if sample_tone and sample_tone.lower() == tone.lower():
                matching.append(
                    {
                        "id": sample.get("id"),
                        "title": sample.get("title"),
                        "style": (
                            sample.get("metadata", {}).get("style")
                            if sample.get("metadata")
                            else None
                        ),
                        "word_count": sample.get("word_count", 0),
                    }
                )

        # Limit results
        matching = matching[:limit]

        logger.info(f"✅ Retrieved {len(matching)} samples with tone '{tone}' for user {user_id}")

        return {"tone": tone, "found_samples": len(matching), "samples": matching}

    except Exception as e:
        logger.error(f"❌ Error retrieving samples by tone: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve samples") from e
