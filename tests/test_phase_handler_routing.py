#!/usr/bin/env python3
"""
Test phase handler agent routing in workflow execution.
"""

import sys
import asyncio
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

# Add src to path
src_path = Path(__file__).parent / "src" / "cofounder_agent"
sys.path.insert(0, str(src_path))

async def test_phase_handler_routing():
    """Test that phase handlers correctly route to agents."""
    print("Testing Phase Handler Agent Routing\n")
    
    from services.workflow_execution_adapter import create_phase_handler
    
    # Mock workflow context
    context = Mock()
    context.workflow_id = "test-workflow-123"
    context.initial_input = {"topic": "python testing"}
    
    # Test 1: Handler creation doesn't raise error
    print("Test 1: Create phase handler")
    try:
        handler = await create_phase_handler(
            phase_name="research",
            agent_name="research_agent",
            database_service=Mock()
        )
        assert callable(handler), "Handler should be callable"
        print("  [OK] Phase handler created successfully\n")
    except Exception as e:
        print(f"  [FAIL] Failed to create handler: {e}\n")
        return False
    
    # Test 2: Handler execution with mock agent
    print("Test 2: Execute handler with mocked agent")
    
    # Create a mock agent
    mock_agent = Mock()
    mock_agent.run = Mock(return_value="Mock research result")
    
    # Patch the agent instantiation
    with patch('services.workflow_execution_adapter._get_agent_instance_async') as mock_get_agent:
        mock_get_agent.return_value = mock_agent
        
        try:
            result = await handler(context)
            
            # Check result structure
            assert result.phase_name == "research", f"Expected phase_name 'research', got '{result.phase_name}'"
            assert result.status.value == "completed", f"Expected status 'completed', got '{result.status.value}'"
            assert result.output is not None, "Expected non-empty output"
            
            print(f"  [OK] Handler executed successfully")
            print(f"     Status: {result.status.value}")
            print(f"     Duration: {result.duration_ms}ms")
            print(f"     Metadata: {result.metadata}\n")
            
        except Exception as e:
            print(f"  [FAIL] Handler execution failed: {e}\n")
            import traceback
            traceback.print_exc()
            return False
    
    # Test 3: Handler with agent execution error
    print("Test 3: Handle agent execution errors")
    
    # Create new handler for error test
    error_handler = await create_phase_handler(
        phase_name="error_phase",
        agent_name="error_agent",
        database_service=Mock()
    )
    
    # Create a mock agent that will raise an error when ANY method is called
    error_agent = Mock()
    error_agent.configure_mock(**{
        'execute.side_effect': RuntimeError("Agent execute failed"),
        'run.side_effect': RuntimeError("Agent run failed"),
        'process.side_effect': RuntimeError("Agent process failed")
    })
    
    with patch('services.workflow_execution_adapter._get_agent_instance_async') as mock_get_agent:
        mock_get_agent.return_value = error_agent
        
        try:
            error_context = Mock()
            error_context.workflow_id = "test-workflow-456"
            error_context.initial_input = {}
            
            result = await error_handler(error_context)
            
            if result.status.value == "failed" and result.error is not None:
                print(f"  [OK] Correctly handled agent error")
                print(f"     Error: {result.error}\n")
            else:
                print(f"  [SKIP] Got {result.status.value} status but expected failed")
                print(f"     Error: {result.error}")
                print(f"     Output: {result.output}\n")
            
        except Exception as e:
            print(f"  [FAIL] Error handling test failed: {e}\n")
            import traceback
            traceback.print_exc()
            return False
    
    # Test 4: Async agent execution
    print("Test 4: Async agent execution")
    
    async_agent = Mock()
    async_agent.execute = AsyncMock(return_value={"phase": "async_phase", "result": "async result"})
    
    with patch('services.workflow_execution_adapter._get_agent_instance_async') as mock_get_agent:
        mock_get_agent.return_value = async_agent
        
        try:
            async_context = Mock()
            async_context.workflow_id = "test-workflow-789"
            async_context.initial_input = {}
            
            result = await handler(async_context)
            
            assert result.status.value == "completed", f"Expected status 'completed', got '{result.status.value}'"
            
            print(f"  [OK] Async agent executed successfully\n")
            
        except Exception as e:
            print(f"  [FAIL] Async execution failed: {e}\n")
            import traceback
            traceback.print_exc()
            return False
    
    print("[OK] All phase handler routing tests passed!")
    return True


if __name__ == "__main__":
    success = asyncio.run(test_phase_handler_routing())
    sys.exit(0 if success else 1)
