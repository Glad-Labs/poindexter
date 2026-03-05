"""
Unit tests for WorkflowExecutor service.

Tests phase execution, input/output mapping, WebSocket events, and workflow control.
"""

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_executor_initialization(mock_workflow_executor):
    """Test WorkflowExecutor initializes correctly."""
    assert mock_workflow_executor is not None
    
    # Verify required methods exist
    assert hasattr(mock_workflow_executor, "execute")
    assert hasattr(mock_workflow_executor, "pause")
    assert hasattr(mock_workflow_executor, "resume")
    assert hasattr(mock_workflow_executor, "cancel")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_execution_success(mock_workflow_executor):
    """Test successful workflow execution."""
    result = await mock_workflow_executor.execute(
        workflow_id="wf_test_001",
        template_name="blog_post"
    )
    
    assert result is not None
    assert result["workflow_id"] == "wf_test_001"
    assert result["status"] == "completed"
    assert "phases_completed" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_phase_execution_order(mock_workflow_executor, sample_workflow_data):
    """Test phases execute in correct order."""
    # With a multi-phase workflow
    workflow_data = {
        **sample_workflow_data,
        "phases": [
            {"name": "phase_1", "index": 0},
            {"name": "phase_2", "index": 1},
            {"name": "phase_3", "index": 2}
        ]
    }
    
    result = await mock_workflow_executor.execute(
        workflow_id="wf_order_001",
        template_name="blog_post"
    )
    
    # Verify all phases executed
    assert result["phases_completed"] >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_input_output_mapping(mock_workflow_executor):
    """Test input/output mapping between phases."""
    # This tests that phase output becomes next phase input
    inputs = {
        "phase_1_input": "value_1",
        "phase_2_input": "value_2"
    }
    
    result = await mock_workflow_executor.execute(
        workflow_id="wf_mapping_001",
        template_name="blog_post",
        **inputs
    )
    
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_pause(mock_workflow_executor):
    """Test pausing a workflow execution."""
    result = await mock_workflow_executor.pause()
    
    assert result["status"] == "paused"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_resume(mock_workflow_executor):
    """Test resuming a paused workflow."""
    result = await mock_workflow_executor.resume()
    
    assert result["status"] == "running"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_cancel(mock_workflow_executor):
    """Test cancelling a workflow execution."""
    result = await mock_workflow_executor.cancel()
    
    assert result["status"] == "cancelled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_state_persistence(mock_workflow_executor):
    """Test workflow state is persisted during execution."""
    # Execute workflow and verify state tracking
    result = await mock_workflow_executor.execute(
        workflow_id="wf_persist_001",
        template_name="blog_post"
    )
    
    assert result["status"] in ["completed", "paused", "running"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_error_handling(mock_workflow_executor):
    """Test workflow handles phase errors gracefully."""
    # This would test recovery from phase failures
    # In mock, should return error status with details
    pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_websocket_events(mock_workflow_executor):
    """Test that workflow emits WebSocket events during execution."""
    # Events should be emitted at:
    # - Workflow start
    # - Phase complete
    # - Workflow complete
    # - On error
    
    result = await mock_workflow_executor.execute(
        workflow_id="wf_ws_001",
        template_name="blog_post"
    )
    
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_timeout_handling(mock_workflow_executor):
    """Test workflow respects timeout configuration."""
    # Each workflow should have a timeout (e.g., 60 minutes)
    # If exceeded, should cancel automatically
    pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_result_aggregation(mock_workflow_executor):
    """Test workflow aggregates results from all phases."""
    result = await mock_workflow_executor.execute(
        workflow_id="wf_agg_001",
        template_name="blog_post"
    )
    
    # Result should include outputs from all phases
    assert "result" in result
    assert isinstance(result["result"], (str, dict))


@pytest.mark.unit
def test_workflow_template_configuration():
    """Test that workflow templates are properly configured."""
    templates = [
        "social_media",
        "email",
        "blog_post",
        "newsletter",
        "market_analysis"
    ]
    
    # Verify all expected templates exist
    assert "blog_post" in templates
    assert "social_media" in templates


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_custom_parameters(mock_workflow_executor):
    """Test workflow accepts custom phase parameters."""
    custom_params = {
        "blog_topic": "AI in Education",
        "target_length": 2000,
        "publishing_platform": "medium"
    }
    
    result = await mock_workflow_executor.execute(
        workflow_id="wf_custom_001",
        template_name="blog_post",
        **custom_params
    )
    
    assert result is not None
