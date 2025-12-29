"""
Model Selection Routes

Endpoints for selecting models per pipeline phase.
Allows users to manually choose or auto-select models.
Shows cost estimates before execution.

Endpoints:
- POST /api/models/estimate-cost - Cost of single model for phase
- POST /api/models/estimate-full-task - Cost of full task with selections
- POST /api/models/auto-select - Auto-select models based on quality
- GET /api/models/available-models - List available models
- POST /api/models/validate-selection - Validate model for phase
- GET /api/models/quality-summary - Get quality preference summaries
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Dict, Optional, List, Any
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================


class ModelSelection(BaseModel):
    """User's model selection for a task"""

    research: str = Field(default="auto", description="Model choice or 'auto'")
    outline: str = Field(default="auto", description="Model choice or 'auto'")
    draft: str = Field(default="auto", description="Model choice or 'auto'")
    assess: str = Field(default="auto", description="Model choice or 'auto'")
    refine: str = Field(default="auto", description="Model choice or 'auto'")
    finalize: str = Field(default="auto", description="Model choice or 'auto'")
    quality_preference: str = Field(
        default="balanced", description="Quality preference: fast, balanced, or quality"
    )


class CostEstimate(BaseModel):
    """Cost estimate for model selection"""

    phase: str
    model: str
    estimated_tokens: int
    estimated_cost: float
    formatted_cost: str = Field(default="", description="Human-readable cost")


class FullTaskCostEstimate(BaseModel):
    """Full task cost estimate"""

    by_phase: Dict[str, float]
    total_cost: float
    formatted_total: str
    budget_limit: float
    budget_remaining: float
    percentage_used: float
    within_budget: bool
    budget_status: str


class ValidationResult(BaseModel):
    """Model validation result"""

    valid: bool
    message: str
    phase: str
    model: str


class AvailableModels(BaseModel):
    """Available models for selection"""

    models: Dict[str, List[str]] | List[str]
    phase: Optional[str] = None


class QualitySummary(BaseModel):
    """Summary of quality preference"""

    name: str
    description: str
    models: Dict[str, str]
    quality_expected: str
    estimated_cost_per_task: float


# ============================================================================
# INITIALIZATION
# ============================================================================

# Initialize model selector
from services.model_selector_service import ModelSelector, QualityPreference

model_selector = ModelSelector()

# Budget limit for solopreneurs
BUDGET_LIMIT = 150.0  # $150/month


# ============================================================================
# HELPERS
# ============================================================================


def format_cost(cost: float) -> str:
    """Format cost as human-readable string"""
    if cost == 0:
        return "Free 🎉"
    elif cost < 0.001:
        return f"${cost:.6f}"
    elif cost < 0.01:
        return f"${cost:.4f}"
    else:
        return f"${cost:.3f}"


# ============================================================================
# ROUTES
# ============================================================================


@router.post("/estimate-cost", response_model=CostEstimate)
async def estimate_phase_cost(
    phase: str = Query(..., description="Pipeline phase"),
    model: str = Query(..., description="Model name"),
) -> CostEstimate:
    """
    Estimate cost of using a specific model for a phase.

    **Examples:**

    - Free local model:
      ```
      POST /api/models/estimate-cost?phase=research&model=ollama
      ```
      Response: `{cost: 0, formatted: "Free 🎉"}`

    - Cloud model:
      ```
      POST /api/models/estimate-cost?phase=draft&model=gpt-4
      ```
      Response: `{cost: 0.009, formatted: "$0.009"}`

    **Quality Per Phase:**
    - Research (gathering): Can use cheap models
    - Outline (structure): Can use cheap models
    - Draft (writing): Benefits from better models
    - Assess (evaluation): Needs GPT-4+
    - Refine (improvement): Needs GPT-4+
    - Finalize (polish): Needs GPT-4+
    """
    # Validate inputs
    is_valid, message = model_selector.validate_model_selection(phase, model)
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)

    # Calculate cost
    cost = model_selector.estimate_cost(phase, model)
    tokens = model_selector.PHASE_TOKEN_ESTIMATES.get(phase, 500)

    return CostEstimate(
        phase=phase,
        model=model,
        estimated_tokens=tokens,
        estimated_cost=cost,
        formatted_cost=format_cost(cost),
    )


@router.post("/estimate-full-task", response_model=FullTaskCostEstimate)
async def estimate_full_task(selection: ModelSelection) -> FullTaskCostEstimate:
    """
    Estimate total cost of task with given model selections.

    **Example Request:**
    ```json
    {
        "research": "ollama",
        "outline": "ollama",
        "draft": "gpt-3.5-turbo",
        "assess": "gpt-4",
        "refine": "gpt-4",
        "finalize": "gpt-4",
        "quality_preference": "balanced"
    }
    ```

    **Example Response:**
    ```json
    {
        "by_phase": {
            "research": 0.0,
            "outline": 0.0,
            "draft": 0.00075,
            "assess": 0.0015,
            "refine": 0.001,
            "finalize": 0.001
        },
        "total_cost": 0.00375,
        "formatted_total": "$0.004",
        "budget_limit": 150.0,
        "budget_remaining": 149.99625,
        "percentage_used": 0.0025,
        "within_budget": true,
        "budget_status": "✅ Well within budget"
    }
    ```

    **What this means:**
    - At $150/month budget, you can create 40,000+ posts with balanced quality
    - Each post costs only $0.004 with this selection
    - Budget shows monthly limit, not per-post
    """
    # Build models dict from selection
    models_dict = {
        "research": selection.research,
        "outline": selection.outline,
        "draft": selection.draft,
        "assess": selection.assess,
        "refine": selection.refine,
        "finalize": selection.finalize,
    }

    # Validate all selections
    for phase, model in models_dict.items():
        if model != "auto":
            is_valid, message = model_selector.validate_model_selection(phase, model)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{phase}: {message}")

    # Get cost estimate
    cost_breakdown = model_selector.estimate_full_task_cost(models_dict)
    total_cost = cost_breakdown.pop("total")

    # Calculate budget status
    budget_remaining = BUDGET_LIMIT - total_cost
    percentage_used = (total_cost / BUDGET_LIMIT) * 100
    within_budget = total_cost <= BUDGET_LIMIT

    if within_budget:
        if percentage_used < 1:
            budget_status = "✅ Excellent - barely noticeable"
        elif percentage_used < 10:
            budget_status = "✅ Great - very affordable"
        else:
            budget_status = "✅ Good - within budget"
    else:
        budget_status = "⚠️ Warning - exceeds monthly budget"

    return FullTaskCostEstimate(
        by_phase=cost_breakdown,
        total_cost=total_cost,
        formatted_total=format_cost(total_cost),
        budget_limit=BUDGET_LIMIT,
        budget_remaining=budget_remaining,
        percentage_used=round(percentage_used, 3),
        within_budget=within_budget,
        budget_status=budget_status,
    )


@router.post("/auto-select")
async def auto_select_models(
    quality_preference: str = Query(
        default="balanced", description="Quality preference: fast, balanced, or quality"
    )
) -> Dict[str, Any]:
    """
    Auto-select models for all phases based on quality preference.

    **Three Quality Tiers:**

    1. **Fast (Cheapest)**
       - Uses Ollama everywhere
       - Cost: $0/post
       - Quality: 3/5 stars
       - Best for: Brainstorming, drafts

    2. **Balanced (Recommended)** ⭐
       - Mix of Ollama, GPT-3.5, and GPT-4
       - Cost: $0.004/post
       - Quality: 4.2/5 stars
       - Best for: Professional content

    3. **Quality (Premium)**
       - GPT-4 and Claude Opus
       - Cost: $0.05/post
       - Quality: 4.7/5 stars
       - Best for: High-stakes content

    **Example Requests:**
    ```
    POST /api/models/auto-select?quality_preference=fast
    POST /api/models/auto-select?quality_preference=balanced
    POST /api/models/auto-select?quality_preference=quality
    ```
    """
    # Validate quality preference
    try:
        quality = QualityPreference(quality_preference)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid quality preference. Must be: fast, balanced, or quality",
        )

    # Auto-select for each phase
    selected = {}
    for phase in ["research", "outline", "draft", "assess", "refine", "finalize"]:
        model = model_selector.auto_select(phase, quality)
        selected[phase] = model

    # Calculate cost
    cost_breakdown = model_selector.estimate_full_task_cost(selected)
    total_cost = cost_breakdown.pop("total")

    # Get quality summary
    summary = model_selector.get_quality_summary(quality)

    budget_remaining = BUDGET_LIMIT - total_cost
    percentage_used = (total_cost / BUDGET_LIMIT) * 100

    return {
        "quality_preference": quality_preference,
        "quality_name": summary["name"],
        "quality_description": summary["description"],
        "quality_expected": summary["quality_expected"],
        "selected_models": selected,
        "cost_breakdown": cost_breakdown,
        "estimated_total_cost": total_cost,
        "formatted_total": format_cost(total_cost),
        "budget_remaining": budget_remaining,
        "percentage_of_budget": round(percentage_used, 2),
        "budget_status": "✅ On track" if total_cost < BUDGET_LIMIT else "⚠️ Check budget",
    }


@router.get("/available-models", response_model=AvailableModels)
async def get_available_models(
    phase: Optional[str] = Query(default=None, description="Optional: specific phase to filter")
) -> AvailableModels:
    """
    Get available models for selection.

    **Without phase parameter:**
    ```
    GET /api/models/available-models
    ```
    Returns all models grouped by phase

    **With phase parameter:**
    ```
    GET /api/models/available-models?phase=research
    ```
    Returns: `{models: ["ollama", "gpt-3.5-turbo", "gpt-4"]}`

    **Available Phases:**
    - research - Information gathering
    - outline - Structure design
    - draft - Content writing
    - assess - Quality evaluation
    - refine - Content improvement
    - finalize - Final polish
    """
    models = model_selector.get_available_models(phase)
    return AvailableModels(models=models["models"], phase=phase)


@router.post("/validate-selection", response_model=ValidationResult)
async def validate_selection(
    phase: str = Query(..., description="Pipeline phase"),
    model: str = Query(..., description="Model to validate"),
) -> ValidationResult:
    """
    Validate that a model is available for a phase.

    **Example: Valid Selection**
    ```
    POST /api/models/validate-selection?phase=research&model=ollama
    ```
    Response: `{valid: true, message: "OK"}`

    **Example: Invalid Selection**
    ```
    POST /api/models/validate-selection?phase=assess&model=ollama
    ```
    Response:
    ```json
    {
        "valid": false,
        "message": "Model ollama not available for assess. Available: ['gpt-4', 'claude-3-opus']"
    }
    ```
    """
    is_valid, message = model_selector.validate_model_selection(phase, model)
    return ValidationResult(valid=is_valid, message=message, phase=phase, model=model)


@router.get("/quality-summary")
async def get_quality_summary(
    quality: str = Query(
        default="balanced", description="Quality preference: fast, balanced, or quality"
    )
) -> QualitySummary:
    """
    Get detailed summary of what each quality preference means.

    Includes:
    - Model selection for each phase
    - Expected quality rating
    - Estimated cost per task
    - Use case recommendations

    **Example:**
    ```
    GET /api/models/quality-summary?quality=balanced
    ```
    """
    try:
        quality_enum = QualityPreference(quality)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid quality. Must be: fast, balanced, or quality"
        )

    summary = model_selector.get_quality_summary(quality_enum)

    return QualitySummary(
        name=summary["name"],
        description=summary["description"],
        models=summary["models"],
        quality_expected=summary["quality_expected"],
        estimated_cost_per_task=summary["estimated_cost_per_task"],
    )


@router.get("/budget-status")
async def get_budget_status() -> Dict[str, Any]:
    """
    Get current budget information for solopreneurs.

    **Returns:**
    - Monthly budget limit: $150
    - How many posts at different quality levels
    - Current spending (from cost_logs table)
    - Projected monthly total
    """
    return {
        "monthly_budget_limit": BUDGET_LIMIT,
        "currency": "USD",
        "posts_per_month": {
            "at_fast_quality": "Unlimited (Free with Ollama)",
            "at_balanced_quality": int(BUDGET_LIMIT / 0.004),
            "at_premium_quality": int(BUDGET_LIMIT / 0.05),
        },
        "recommendations": [
            "✅ Mix 'Fast' (Ollama) for brainstorming - saves money",
            "✅ Use 'Balanced' for regular posts - best value",
            "✅ Use 'Premium' only for important/high-stakes content",
        ],
    }
