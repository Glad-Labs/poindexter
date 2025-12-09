"""
Training data management API routes.

Endpoints for:
- Filtering and managing training data
- Tagging and organizing data
- Exporting datasets
- Fine-tuning job management
- Model registry and deployment
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestrator/training", tags=["training"])


# ============================================================================
# DEPENDENCY INJECTION (will be set up in main app)
# ============================================================================

training_data_service = None
fine_tuning_service = None


def set_services(tds, fts):
    """Set service instances (called from main app)"""
    global training_data_service, fine_tuning_service
    training_data_service = tds
    fine_tuning_service = fts


# ============================================================================
# TRAINING DATA ENDPOINTS
# ============================================================================

@router.get("/data")
async def list_training_data(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Get all training data with pagination"""
    try:
        data = await training_data_service.get_all_training_data(limit=limit, offset=offset)
        return {
            "success": True,
            "count": len(data),
            "data": [
                {
                    "id": d.id,
                    "execution_id": d.execution_id,
                    "user_request": d.user_request,
                    "intent": d.intent,
                    "quality_score": d.quality_score,
                    "success": d.success,
                    "tags": d.tags,
                    "created_at": d.created_at
                }
                for d in data
            ]
        }
    except Exception as e:
        logger.error(f"Error listing training data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/data/filter")
async def filter_training_data(
    quality_min: float = Query(0.0, ge=0.0, le=1.0),
    quality_max: float = Query(1.0, ge=0.0, le=1.0),
    intent_filter: Optional[str] = Query(None),
    success_only: bool = Query(False),
    exclude_tags: Optional[str] = Query(None),
    include_tags: Optional[str] = Query(None),
    date_after: Optional[str] = Query(None),
    date_before: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Filter training data by multiple criteria.
    
    Query params:
    - quality_min/max: Quality score range (0.0-1.0)
    - intent_filter: Single intent or comma-separated intents
    - exclude_tags: Comma-separated tags to exclude
    - include_tags: Comma-separated tags to include (must have all)
    - date_after/before: ISO date strings
    """
    try:
        intent_list = intent_filter.split(",") if intent_filter else None
        exclude_list = exclude_tags.split(",") if exclude_tags else None
        include_list = include_tags.split(",") if include_tags else None

        data = await training_data_service.filter_training_data(
            quality_min=quality_min,
            quality_max=quality_max,
            intent_filter=intent_list,
            success_only=success_only,
            exclude_tags=exclude_list,
            include_tags=include_list,
            date_after=date_after,
            date_before=date_before,
            limit=limit
        )

        stats = await training_data_service.get_statistics({
            "quality_min": quality_min,
            "quality_max": quality_max,
            "exclude_tags": exclude_list,
            "include_tags": include_list
        })

        return {
            "success": True,
            "filtered_count": len(data),
            "total_count": stats.total_examples,
            "avg_quality": stats.avg_quality_score,
            "success_rate": stats.success_rate,
            "data": [
                {
                    "id": d.id,
                    "execution_id": d.execution_id,
                    "user_request": d.user_request[:100],
                    "quality_score": d.quality_score,
                    "success": d.success,
                    "tags": d.tags,
                    "created_at": d.created_at
                }
                for d in data
            ]
        }
    except Exception as e:
        logger.error(f"Error filtering training data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/data/tag")
async def tag_training_data(body: Dict[str, Any]):
    """
    Add tags to training data.
    
    Body:
    {
        "execution_ids": ["exec-123", "exec-456"],
        "tags": ["production", "approved"]
    }
    """
    try:
        execution_ids = body.get("execution_ids", [])
        tags = body.get("tags", [])

        if not execution_ids or not tags:
            raise HTTPException(status_code=400, detail="execution_ids and tags required")

        count = await training_data_service.add_tags(execution_ids, tags)

        return {
            "success": True,
            "tagged_count": count,
            "message": f"Tagged {count} examples with {tags}"
        }
    except Exception as e:
        logger.error(f"Error tagging training data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/data/tag-by-date")
async def tag_by_date_range(body: Dict[str, Any]):
    """
    Tag all data within a date range.
    
    Body:
    {
        "date_after": "2025-12-01T00:00:00Z",
        "date_before": "2025-12-31T23:59:59Z",
        "tags": ["development"]
    }
    """
    try:
        date_after = body.get("date_after")
        date_before = body.get("date_before")
        tags = body.get("tags", [])

        if not date_after or not date_before or not tags:
            raise HTTPException(status_code=400, detail="date_after, date_before, tags required")

        count = await training_data_service.tag_by_date_range(date_after, date_before, tags)

        return {
            "success": True,
            "tagged_count": count,
            "message": f"Tagged {count} examples in date range"
        }
    except Exception as e:
        logger.error(f"Error tagging by date: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/data/tag-by-quality")
async def tag_by_quality(body: Dict[str, Any]):
    """
    Tag low-quality data.
    
    Body:
    {
        "quality_max": 0.7,
        "tags": ["low_quality"]
    }
    """
    try:
        quality_max = body.get("quality_max")
        tags = body.get("tags", [])

        if quality_max is None or not tags:
            raise HTTPException(status_code=400, detail="quality_max and tags required")

        count = await training_data_service.tag_by_quality(quality_max, tags)

        return {
            "success": True,
            "tagged_count": count,
            "message": f"Tagged {count} examples with quality < {quality_max}"
        }
    except Exception as e:
        logger.error(f"Error tagging by quality: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# STATISTICS ENDPOINTS
# ============================================================================

@router.get("/stats")
async def get_statistics(exclude_tags: Optional[str] = Query(None)):
    """Get statistics about training data"""
    try:
        exclude_list = exclude_tags.split(",") if exclude_tags else None

        stats = await training_data_service.get_statistics({
            "exclude_tags": exclude_list
        })

        return {
            "success": True,
            "total_examples": stats.total_examples,
            "filtered_count": stats.filtered_count,
            "avg_quality_score": stats.avg_quality_score,
            "success_rate": stats.success_rate,
            "by_tag": stats.by_tag,
            "by_intent": stats.by_intent,
            "quality_distribution": stats.quality_score_distribution,
            "date_range": stats.date_range
        }
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# DATASET ENDPOINTS
# ============================================================================

@router.post("/datasets")
async def create_dataset(body: Dict[str, Any]):
    """
    Create a versioned dataset for fine-tuning.
    
    Body:
    {
        "name": "production",
        "description": "Production data only",
        "filters": {
            "quality_min": 0.85,
            "exclude_tags": ["development", "test"]
        }
    }
    """
    try:
        name = body.get("name")
        description = body.get("description", "")
        filters = body.get("filters", {})

        if not name:
            raise HTTPException(status_code=400, detail="name required")

        dataset = await training_data_service.create_dataset(name, description, filters)

        return {
            "success": True,
            "dataset": dataset
        }
    except Exception as e:
        logger.error(f"Error creating dataset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasets")
async def list_datasets():
    """List all versioned datasets"""
    try:
        datasets = await training_data_service.list_datasets()

        return {
            "success": True,
            "count": len(datasets),
            "datasets": datasets
        }
    except Exception as e:
        logger.error(f"Error listing datasets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: int):
    """Get specific dataset by ID"""
    try:
        dataset = await training_data_service.get_dataset(dataset_id)

        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")

        return {
            "success": True,
            "dataset": dataset
        }
    except Exception as e:
        logger.error(f"Error getting dataset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/datasets/export")
async def export_dataset(body: Dict[str, Any]):
    """
    Export dataset as JSONL for fine-tuning.
    
    Body:
    {
        "filters": {
            "quality_min": 0.85,
            "exclude_tags": ["development"]
        }
    }
    """
    try:
        filters = body.get("filters", {})

        export_result = await training_data_service.export_as_jsonl(filters=filters)

        return {
            "success": True,
            "export": {
                "file_path": export_result["file_path"],
                "file_size": export_result["file_size"],
                "example_count": export_result["example_count"],
                "avg_quality": export_result["avg_quality"]
            }
        }
    except Exception as e:
        logger.error(f"Error exporting dataset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# FINE-TUNING ENDPOINTS
# ============================================================================

@router.post("/fine-tune")
async def start_fine_tuning(body: Dict[str, Any]):
    """
    Start a fine-tuning job.
    
    Body:
    {
        "target": "ollama|gemini|claude|gpt4",
        "dataset_path": "/path/to/training_data.jsonl",
        "base_model": "mistral"  # For Ollama
    }
    """
    try:
        target = body.get("target", "ollama").lower()
        dataset_path = body.get("dataset_path")
        base_model = body.get("base_model", "mistral")

        if not dataset_path:
            raise HTTPException(status_code=400, detail="dataset_path required")

        if target == "ollama":
            result = await fine_tuning_service.fine_tune_ollama(
                dataset_path=dataset_path,
                base_model=base_model
            )
        elif target == "gemini":
            result = await fine_tuning_service.fine_tune_gemini(dataset_path)
        elif target == "claude":
            result = await fine_tuning_service.fine_tune_claude(dataset_path)
        elif target == "gpt4":
            result = await fine_tuning_service.fine_tune_gpt4(dataset_path)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown target: {target}")

        return {
            "success": result.get("status") != "failed",
            "job": result
        }
    except Exception as e:
        logger.error(f"Error starting fine-tuning: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs")
async def list_fine_tuning_jobs():
    """List all fine-tuning jobs"""
    try:
        jobs = await fine_tuning_service.list_jobs()

        return {
            "success": True,
            "count": len(jobs),
            "jobs": jobs
        }
    except Exception as e:
        logger.error(f"Error listing jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get status of a fine-tuning job"""
    try:
        status = await fine_tuning_service.get_job_status(job_id)

        return {
            "success": True,
            "job": status
        }
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a fine-tuning job"""
    try:
        result = await fine_tuning_service.cancel_job(job_id)

        return {
            "success": result["success"],
            "message": result.get("message", result.get("error", "Job cancelled"))
        }
    except Exception as e:
        logger.error(f"Error cancelling job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# MODEL REGISTRY ENDPOINTS
# ============================================================================

@router.post("/jobs/{job_id}/deploy")
async def deploy_model(job_id: str, body: Dict[str, Any]):
    """
    Deploy a completed fine-tuning job as a model.
    
    Body:
    {
        "model_name": "orchestrator-v1",
        "set_active": true
    }
    """
    try:
        model_name = body.get("model_name")
        set_active = body.get("set_active", False)

        if not model_name:
            raise HTTPException(status_code=400, detail="model_name required")

        result = await fine_tuning_service.deploy_model(
            model_name=model_name,
            job_id=job_id,
            set_active=set_active
        )

        return {
            "success": result["success"],
            "deployment": result
        }
    except Exception as e:
        logger.error(f"Error deploying model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# INITIALIZATION
# ============================================================================

__all__ = ["router", "set_services"]
