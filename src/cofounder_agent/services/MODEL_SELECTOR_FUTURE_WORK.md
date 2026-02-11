# ModelSelectorService - Future Work

**Status:** ⏸️ **NOT CURRENTLY USED** - Preserved for future architectural evolution

## Overview

ModelSelectorService is a phase-aware model selection service designed for potential future enhancements to model routing strategy. Currently, the production system uses **ModelRouter** and **ModelConsolidationService** for LLM provider selection.

## Current Architecture (Active)

**Model Routing:** Dual-system approach

**Primary Router:** [ModelConsolidationService](./model_consolidation_service.py) - Multi-provider with fallback chain

**Secondary Router:** [ModelRouter](./model_router.py) - Provider-specific routing

**Entry Point:** [model_routes.py](../routes/model_routes.py)

```text
Content Generation Request
  → UnifiedOrchestrator
    → ModelConsolidationService.get_model_consolidation_service()
      → OllamaClient (local, zero-cost)
      → GeminiClient (fallback)
      → HuggingFaceClient (fallback)
      → ChatGPTClient (fallback)
      → ClaudeClient (fallback)
```

## This Service (Proposed)

**Approach:** Phase-aware model selection

**Location:** [model_selector_service.py](./model_selector_service.py) (313 lines)

**Main Class:** `ModelSelector`

**Key Methods:**

- `auto_select(phase: str)` - Select model appropriate for pipeline phase
- `validate_model_selection()` - Verify model choice is valid
- `estimate_cost()` - Calculate cost before execution

## Why This Exists But Isn't Used

The ModelSelectorService was created to enable **phase-aware model routing**:

- **Research Phase:** Fast/cheap model (Ollama, Gemini)
- **Creative Phase:** Higher quality model (Claude, GPT-4)
- **QA Phase:** Precise evaluation model
- **Refinement Phase:** Iterative improvement model
- **Publishing Phase:** Final formatting model

However, the current approach works well:

- **ModelConsolidationService** provides intelligent fallback across all providers
- **UnifiedOrchestrator** uses same model for all phases
- This simplicity is **production-stable and effective**

## When to Use This

**Consider reviving ModelSelector when:**

- Need to optimize costs by using cheaper models for research/QA phases
- Want different model quality levels per phase (cost vs quality tradeoff)
- Need to route to specialized models per task type
- User preferences require specific models per phase

## What Would Need to Happen

To integrate ModelSelectorService:

1. **Enhance UnifiedOrchestrator** to accept phase parameter

```python
# Instead of:
model = consolidation_service.generate(prompt)

# Would be:
model = model_selector.auto_select(phase="research")
result = model.generate(prompt)
```

1. **Update each agent** to use phase-specific models

```python
class ResearchAgent:
  async def run(self, topic):
    model = model_selector.auto_select("research")  # Cheap/fast
    data = await model.generate(search_prompt)

class CreativeAgent:
  async def run(self, topic):
    model = model_selector.auto_select("creative")  # Quality/expensive
    content = await model.generate(draft_prompt)
```

1. **Add cost estimation** to balance quality and expense

```python
cost = model_selector.estimate_cost(
    phase="creative",
    tokens_estimate=2000
)
# Log cost for analytics
```

1. **Test cost vs quality tradeoff** to verify benefits

- Prove cost savings don't hurt quality
- Measure latency improvements
- Validate user experience remains good

1. **Update configuration** to allow phase-specific model preferences

```env
# Environment config
MODEL_RESEARCH_PHASE=ollama:free
MODEL_CREATIVE_PHASE=claude-3-sonnet
MODEL_QA_PHASE=gpt-4-turbo
```

## Verification

Confirmed not used (as of Feb 10, 2026):

- No imports of ModelSelector in active routes or services
- No calls to auto_select() anywhere in codebase
- No cost estimation calls in production

## References

- **Current Architecture:** [docs/02-ARCHITECTURE_AND_DESIGN.md](../../../docs/02-ARCHITECTURE_AND_DESIGN.md)
- **Active Model Routing:** [model_consolidation_service.py](./model_consolidation_service.py)
- **Provider Router:** [model_router.py](./model_router.py)
- **Model Routes:** [routes/model_routes.py](../routes/model_routes.py)
- **Unified Orchestrator:** [unified_orchestrator.py](./unified_orchestrator.py)

---

**Decision Date:** February 10, 2026

**Decision:** Preserve for future work, not currently active

**Deprecation Status:** Non-critical, no impact on current pipeline

**Last Review:** Codebase cleanup phase 4
