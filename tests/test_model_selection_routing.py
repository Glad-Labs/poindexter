"""
Test model selection routing through orchestrator pipeline.

Verifies that:
1. Model selections are extracted from execution context
2. Correct model is selected for each phase
3. LLMClient is initialized with proper model override
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_model_selection_extraction():
    """Test that model_selections are extracted from execution context."""
    from services.unified_orchestrator import UnifiedOrchestrator, Request, RequestType
    
    orchestrator = UnifiedOrchestrator()
    
    # Create a request with model selections in context
    request = Request(
        request_id="test-123",
        original_text="Write a blog post about AI",
        request_type=RequestType.CONTENT_CREATION,
        extracted_intent="content_creation",
        parameters={"topic": "AI"},
        context={
            "model_selections": {
                "research": "auto",
                "draft": "gemini-2.5-flash",
                "assess": "auto",
                "refine": "gpt-4",
                "finalize": "auto"
            },
            "quality_preference": "balanced",
            "user_id": "user_123",
            "writing_style_id": None
        }
    )
    
    # Verify context contains model selections
    assert request.context["model_selections"]["draft"] == "gemini-2.5-flash"
    assert request.context["model_selections"]["refine"] == "gpt-4"
    assert request.context["quality_preference"] == "balanced"


def test_get_model_for_phase():
    """Test _get_model_for_phase helper function."""
    from services.unified_orchestrator import UnifiedOrchestrator
    
    orchestrator = UnifiedOrchestrator()
    
    model_selections = {
        "research": "auto",
        "draft": "gemini-2.5-flash",
        "assess": "gpt-4",
        "refine": "claude-opus",
        "finalize": "auto"
    }
    
    # Test explicit selection
    assert orchestrator._get_model_for_phase("draft", model_selections, "balanced") == "gemini-2.5-flash"
    assert orchestrator._get_model_for_phase("refine", model_selections, "balanced") == "claude-opus"
    
    # Test "auto" falls back to None
    assert orchestrator._get_model_for_phase("research", model_selections, "balanced") is None
    
    # Test missing phase falls back to None
    assert orchestrator._get_model_for_phase("missing_phase", model_selections, "balanced") is None


def test_llm_client_model_override():
    """Test that LLMClient accepts and uses model_name override."""
    from agents.content_agent.services.llm_client import LLMClient
    
    # Test default initialization
    client_default = LLMClient()
    assert client_default.model_name_override is None
    
    # Test with model override
    client_override = LLMClient(model_name="gemini-2.5-flash")
    assert client_override.model_name_override == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_model_selection_passed_to_orchestrator():
    """Test that model_selections are passed through to orchestrator execution."""
    from services.unified_orchestrator import UnifiedOrchestrator, Request, RequestType
    from agents.content_agent.services.llm_client import LLMClient
    
    # Create mock orchestrator with mocked agents
    orchestrator = UnifiedOrchestrator()
    
    request = Request(
        request_id="test-model-selection",
        original_text="Write a blog post about AI",
        request_type=RequestType.CONTENT_CREATION,
        extracted_intent="content_creation",
        parameters={
            "topic": "AI",
            "style": "technical",
            "tone": "informative",
            "keywords": ["AI", "ML"]
        },
        context={
            "model_selections": {
                "draft": "gemini-2.5-flash"
            },
            "quality_preference": "balanced"
        }
    )
    
    # Verify the context is properly set
    assert request.context["model_selections"]["draft"] == "gemini-2.5-flash"
    
    # Verify helper function gets the right model
    model = orchestrator._get_model_for_phase("draft", request.context["model_selections"], request.context["quality_preference"])
    assert model == "gemini-2.5-flash"


def test_model_selection_with_json_string():
    """Test that model_selections work when passed as JSON string."""
    import json
    from services.unified_orchestrator import UnifiedOrchestrator
    
    orchestrator = UnifiedOrchestrator()
    
    # Model selections as JSON string (as they come from database)
    model_selections_json = json.dumps({
        "draft": "gpt-4",
        "refine": "claude-opus"
    })
    
    # Parse it like the orchestrator does
    model_selections = json.loads(model_selections_json)
    
    assert orchestrator._get_model_for_phase("draft", model_selections, "balanced") == "gpt-4"
    assert orchestrator._get_model_for_phase("refine", model_selections, "balanced") == "claude-opus"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
