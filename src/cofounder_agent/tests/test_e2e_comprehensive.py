"""
End-to-End Tests for AI Co-Founder System
Complete system validation and user workflow testing
"""

import pytest

# Skip comprehensive E2E tests - system is failing all models, needs working LLM setup
pytest.skip(allow_module_level=True, reason="E2E tests require working LLM (Ollama/OpenAI), skip until configured")

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
import sys
import os

# Add project paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from conftest import (
    test_data_manager, performance_monitor, test_utils, 
    run_with_timeout, pytest_marks
)

class E2ETestScenario:
    """End-to-end test scenario runner"""
    
    def __init__(self, name: str):
        self.name = name
        self.steps = []
        self.results = []
        self.start_time = None
        self.end_time = None
    
    def add_step(self, step_name: str, step_function, expected_result=None):
        """Add a test step"""
        self.steps.append({
            "name": step_name,
            "function": step_function,
            "expected": expected_result
        })
    
    async def run(self) -> Dict[str, Any]:
        """Run the complete scenario"""
        self.start_time = datetime.now()
        success_count = 0
        
        for i, step in enumerate(self.steps):
            step_start = datetime.now()
            
            try:
                if asyncio.iscoroutinefunction(step["function"]):
                    result = await step["function"]()
                else:
                    result = step["function"]()
                
                step_success = True
                if step["expected"] and not self._validate_result(result, step["expected"]):
                    step_success = False
                
                if step_success:
                    success_count += 1
                
            except Exception as e:
                result = {"error": str(e)}
                step_success = False
            
            step_end = datetime.now()
            step_duration = (step_end - step_start).total_seconds()
            
            self.results.append({
                "step": i + 1,
                "name": step["name"],
                "success": step_success,
                "result": result,
                "duration": step_duration,
                "timestamp": step_start.isoformat()
            })
        
        self.end_time = datetime.now()
        total_duration = (self.end_time - self.start_time).total_seconds()
        
        return {
            "scenario": self.name,
            "total_steps": len(self.steps),
            "successful_steps": success_count,
            "success_rate": success_count / len(self.steps) if self.steps else 0,
            "total_duration": total_duration,
            "steps": self.results
        }
    
    def _validate_result(self, result, expected):
        """Validate step result against expected outcome"""
        if isinstance(expected, dict):
            if not isinstance(result, dict):
                return False
            
            for key, value in expected.items():
                if key not in result or result[key] != value:
                    return False
        
        return True

@pytest.fixture
def e2e_scenario():
    """E2E scenario fixture"""
    def create_scenario(name: str):
        return E2ETestScenario(name)
    
    return create_scenario

@pytest_marks["e2e"]
class TestCompleteUserWorkflows:
    """Test complete user workflows end-to-end"""
    
    async def test_business_owner_daily_routine(self, e2e_scenario, test_data_manager):
        """Test typical business owner daily routine"""
        
        scenario = e2e_scenario("Business Owner Daily Routine")
        
        # Step 1: Morning check-in - Get business overview
        async def morning_checkin():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("E2E Test Company")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.search_memories = AsyncMock(return_value=[])
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    # Request business overview
                    response = await cofounder.chat(
                        "Good morning! Give me a quick overview of how the business is performing.",
                        {"user_id": "test_business_owner", "session_type": "daily_checkin"}
                    )
                    
                    return {"success": True, "response_received": True, "response": response}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Morning Business Check-in", morning_checkin, {"success": True})
        
        # Step 2: Review and create tasks
        async def review_and_create_tasks():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("E2E Test Company")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    # Create tasks for the day
                    tasks_to_create = [
                        "Write a blog post about our new product features",
                        "Analyze competitor pricing strategies",
                        "Review and respond to customer feedback"
                    ]
                    
                    results = []
                    for task_desc in tasks_to_create:
                        task_request = {
                            "title": task_desc[:50] + "..." if len(task_desc) > 50 else task_desc,
                            "description": task_desc,
                            "priority": "medium",
                            "category": "daily_operations"
                        }
                        
                        result = await cofounder.create_task_from_request(task_request)
                        results.append(result)
                    
                    return {
                        "success": True,
                        "tasks_created": len(results),
                        "results": results
                    }
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Create Daily Tasks", review_and_create_tasks, {"success": True})
        
        # Step 3: Delegate tasks to AI agents
        async def delegate_tasks():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("E2E Test Company")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    # Delegate tasks to appropriate agents
                    delegations = [
                        {
                            "description": "Create comprehensive blog post about product features",
                            "requirements": ["blog_writing", "content_optimization"],
                            "priority": "high"
                        },
                        {
                            "description": "Conduct competitive analysis of pricing strategies",
                            "requirements": ["market_analysis", "competitor_analysis"],
                            "priority": "medium"
                        }
                    ]
                    
                    results = []
                    for delegation in delegations:
                        result = await cofounder.delegate_task_to_agent(
                            delegation["description"],
                            delegation["requirements"],
                            delegation["priority"]
                        )
                        results.append(result)
                    
                    return {
                        "success": True,
                        "delegations_created": len(results),
                        "results": results
                    }
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Delegate Tasks to AI Agents", delegate_tasks, {"success": True})
        
        # Step 4: Strategic planning session
        async def strategic_planning():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("E2E Test Company")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    # Create strategic planning session
                    planning_request = {
                        "timeframe": "Next Quarter",
                        "focus_areas": ["market_expansion", "product_development", "customer_retention"],
                        "current_metrics": test_data_manager.get_sample_business_data()
                    }
                    
                    result = await cofounder.create_strategic_plan(planning_request)
                    
                    return {
                        "success": True,
                        "strategic_plan_created": True,
                        "result": result
                    }
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Strategic Planning Session", strategic_planning, {"success": True})
        
        # Step 5: Evening review - Get comprehensive status
        async def evening_review():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("E2E Test Company")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.search_memories = AsyncMock(return_value=[])
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    # Get comprehensive status
                    status = await cofounder.get_comprehensive_status()
                    
                    return {
                        "success": True,
                        "status_retrieved": True,
                        "status": status
                    }
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Evening Business Review", evening_review, {"success": True})
        
        # Run the complete scenario
        result = await scenario.run()
        
        # Validate scenario results
        assert result["success_rate"] >= 0.8, f"Daily routine scenario failed: {result['success_rate']:.1%} success rate"
        assert result["total_duration"] < 30.0, f"Daily routine took too long: {result['total_duration']:.1f}s"
        
        # Log scenario results
        print(f"\n=== {result['scenario']} Results ===")
        print(f"Success Rate: {result['success_rate']:.1%}")
        print(f"Total Duration: {result['total_duration']:.2f}s")
        
        for step in result["steps"]:
            status = "✅" if step["success"] else "❌"
            print(f"{status} Step {step['step']}: {step['name']} ({step['duration']:.2f}s)")
    
    async def test_content_creator_workflow(self, e2e_scenario, performance_monitor):
        """Test content creator workflow"""
        
        scenario = e2e_scenario("Content Creator Workflow")
        
        # Step 1: Content strategy consultation
        async def content_strategy():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("Content Creator Test")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.search_memories = AsyncMock(return_value=[])
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    response = await cofounder.chat(
                        "I need help developing a content strategy for our AI automation business. What topics should we focus on?",
                        {"user_role": "content_creator", "domain": "ai_automation"}
                    )
                    
                    return {"success": True, "strategy_received": True}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Content Strategy Consultation", content_strategy, {"success": True})
        
        # Step 2: Create content workflow
        async def create_content_workflow():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("Content Creator Test")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    # Create content production workflow
                    workflow_result = await cofounder.create_strategic_workflow(
                        "Monthly Content Production",
                        [
                            "Research trending AI topics",
                            "Create 4 blog posts about AI automation",
                            "Develop social media content calendar",
                            "Optimize content for SEO performance"
                        ]
                    )
                    
                    return {"success": True, "workflow_created": True}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Create Content Workflow", create_content_workflow, {"success": True})
        
        # Step 3: Content optimization analysis
        async def content_optimization():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("Content Creator Test")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.store_memory = AsyncMock()
                    
                    # Request content optimization
                    optimization_data = {
                        "content_type": "blog_posts",
                        "target_audience": "business_owners",
                        "goals": ["increase_engagement", "improve_seo", "generate_leads"],
                        "current_performance": {
                            "avg_engagement": 0.035,
                            "seo_score": 72,
                            "conversion_rate": 0.024
                        }
                    }
                    
                    result = await cofounder.optimize_content_strategy(optimization_data)
                    
                    return {"success": True, "optimization_completed": True}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Content Optimization Analysis", content_optimization, {"success": True})
        
        # Run scenario with performance monitoring
        async def run_content_workflow():
            return await scenario.run()
        
        result, duration, success = await performance_monitor.measure_async_operation(
            "content_creator_workflow", run_content_workflow
        )
        
        assert success, "Content creator workflow failed"
        assert result["success_rate"] >= 0.7, f"Content workflow success rate too low: {result['success_rate']:.1%}"
        assert duration < 20.0, f"Content workflow too slow: {duration:.1f}s"
    
    async def test_voice_interaction_workflow(self, e2e_scenario):
        """Test voice interaction workflow"""
        
        scenario = e2e_scenario("Voice Interaction Workflow")
        
        # Step 1: Initialize voice interface
        async def init_voice_interface():
            try:
                from voice_interface import VoiceInterfaceSystem
                
                voice_system = VoiceInterfaceSystem()
                return {"success": True, "voice_system_ready": True}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Initialize Voice Interface", init_voice_interface, {"success": True})
        
        # Step 2: Process voice commands
        async def process_voice_commands():
            try:
                from voice_interface import VoiceInterfaceSystem
                from unittest.mock import patch
                
                voice_system = VoiceInterfaceSystem()
                
                # Simulate voice commands
                voice_commands = [
                    "Show me business metrics",
                    "Create a new blog post about machine learning",
                    "What is our revenue this month"
                ]
                
                results = []
                for command_text in voice_commands:
                    # Mock speech-to-text
                    with patch.object(voice_system, '_simulate_speech_to_text',
                                    return_value={
                                        "success": True,
                                        "text": command_text,
                                        "confidence": 0.95
                                    }):
                        
                        result = await voice_system.process_voice_input(
                            b"mock_audio_data", 
                            "voice_test_session"
                        )
                        results.append(result)
                
                return {
                    "success": True,
                    "commands_processed": len(results),
                    "results": results
                }
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Process Voice Commands", process_voice_commands, {"success": True})
        
        # Step 3: Voice analytics
        async def voice_analytics():
            try:
                from voice_interface import VoiceInterfaceSystem
                
                voice_system = VoiceInterfaceSystem()
                
                # Get analytics (should work even with no commands in fresh instance)
                analytics = await voice_system.get_voice_analytics()
                
                return {"success": True, "analytics_retrieved": True}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Voice Analytics", voice_analytics, {"success": True})
        
        # Run scenario
        result = await scenario.run()
        
        assert result["success_rate"] >= 0.8, f"Voice workflow failed: {result['success_rate']:.1%} success rate"

@pytest_marks["e2e"]
@pytest_marks["performance"]
class TestSystemPerformance:
    """Test system-wide performance characteristics"""
    
    async def test_system_load_handling(self, performance_monitor):
        """Test system performance under load"""
        
        async def create_load_test():
            """Create sustained load on the system"""
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from multi_agent_orchestrator import MultiAgentOrchestrator, TaskPriority
                from unittest.mock import Mock, AsyncMock, patch
                
                # Create multiple system instances
                systems = []
                
                for i in range(3):  # Create 3 concurrent systems
                    with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                        cofounder = IntelligentCoFounder(f"Load Test {i}")
                        cofounder.memory_system = Mock()
                        cofounder.memory_system.search_memories = AsyncMock(return_value=[])
                        cofounder.memory_system.store_memory = AsyncMock()
                        systems.append(cofounder)
                
                # Create tasks in all systems
                tasks = []
                for system in systems:
                    for j in range(5):  # 5 tasks per system
                        task = system.create_task_from_request({
                            "title": f"Load Test Task {j}",
                            "description": f"Task {j} for load testing",
                            "priority": "medium",
                            "category": "load_test"
                        })
                        tasks.append(task)
                
                # Wait for all tasks
                await asyncio.gather(*tasks, return_exceptions=True)
                
                return {"success": True, "systems_created": len(systems), "tasks_created": len(tasks)}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        result, duration, success = await performance_monitor.measure_async_operation(
            "system_load_test", create_load_test
        )
        
        assert success, "Load test failed"
        assert duration < 15.0, f"System load handling too slow: {duration:.1f}s"
    
    async def test_memory_efficiency(self, performance_monitor):
        """Test memory usage efficiency"""
        
        async def memory_test():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from memory_system import AIMemorySystem, MemoryType, ImportanceLevel
                from unittest.mock import patch
                
                # Create memory system
                memory_system = AIMemorySystem()
                
                # Store many memories to test efficiency
                for i in range(100):
                    await memory_system.store_memory(
                        content=f"Test memory {i}",
                        memory_type=MemoryType.BUSINESS_FACT,
                        importance=ImportanceLevel.MEDIUM,
                        tags=[f"test_{i}", "memory_test"],
                        metadata={"test_id": i}
                    )
                
                # Search memories to test retrieval efficiency
                search_results = await memory_system.search_memories(
                    query="test memory",
                    limit=20
                )
                
                return {"success": True, "memories_stored": 100, "search_results": len(search_results)}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        result, duration, success = await performance_monitor.measure_async_operation(
            "memory_efficiency_test", memory_test
        )
        
        assert success, "Memory efficiency test failed"
        assert duration < 5.0, f"Memory operations too slow: {duration:.1f}s"

@pytest_marks["e2e"]
class TestSystemResilience:
    """Test system resilience and error recovery"""
    
    async def test_graceful_degradation(self, e2e_scenario):
        """Test system graceful degradation when components fail"""
        
        scenario = e2e_scenario("Graceful Degradation Test")
        
        # Step 1: System with missing components
        async def test_missing_components():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, patch
                
                # Create system with deliberately failing components
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("Resilience Test")
                    
                    # Mock failing components
                    cofounder.business_intelligence = Mock()
                    cofounder.business_intelligence.analyze_business_performance = Mock(
                        side_effect=Exception("BI system offline")
                    )
                    
                    # System should still handle basic requests
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.search_memories = Mock(return_value=[])
                    cofounder.memory_system.store_memory = Mock()
                    
                    # This should work despite BI failure
                    response = await cofounder.chat(
                        "Hello, are you working?",
                        {"test": "resilience"}
                    )
                    
                    return {"success": True, "degraded_operation": True}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Test Missing Components", test_missing_components, {"success": True})
        
        # Step 2: Recovery from temporary failures
        async def test_recovery():
            try:
                from multi_agent_orchestrator import MultiAgentOrchestrator, TaskPriority
                
                orchestrator = MultiAgentOrchestrator()
                orchestrator.orchestration_active = False
                
                # Create task that might fail
                task_id = await orchestrator.create_task(
                    "Recovery Test Task",
                    "Task to test recovery",
                    ["blog_writing"],
                    TaskPriority.MEDIUM
                )
                
                # System should handle task creation even if assignment fails
                return {"success": True, "recovery_tested": True, "task_created": task_id is not None}
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        scenario.add_step("Test Recovery", test_recovery, {"success": True})
        
        # Run resilience tests
        result = await scenario.run()
        
        assert result["success_rate"] >= 1.0, f"Resilience test failed: {result['success_rate']:.1%} success rate"
    
    async def test_concurrent_operations(self, performance_monitor):
        """Test concurrent operations handling"""
        
        async def concurrent_test():
            try:
                from intelligent_cofounder import IntelligentCoFounder
                from unittest.mock import Mock, AsyncMock, patch
                
                with patch('intelligent_cofounder.MCPClientManager', return_value=None):
                    cofounder = IntelligentCoFounder("Concurrent Test")
                    cofounder.memory_system = Mock()
                    cofounder.memory_system.search_memories = AsyncMock(return_value=[])
                    cofounder.memory_system.store_memory = AsyncMock()
                
                # Create multiple concurrent operations
                operations = []
                
                for i in range(10):
                    # Different types of operations
                    if i % 3 == 0:
                        op = cofounder.chat(f"Test message {i}", {"concurrent_test": True})
                    elif i % 3 == 1:
                        op = cofounder.analyze_command_intent(f"Test command {i}")
                    else:
                        op = cofounder.create_task_from_request({
                            "title": f"Concurrent Task {i}",
                            "description": f"Task {i}",
                            "priority": "medium"
                        })
                    
                    operations.append(op)
                
                # Wait for all operations
                results = await asyncio.gather(*operations, return_exceptions=True)
                
                # Count successes
                successes = sum(1 for r in results if not isinstance(r, Exception))
                
                return {
                    "success": True,
                    "total_operations": len(results),
                    "successful_operations": successes,
                    "success_rate": successes / len(results)
                }
            
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        result, duration, success = await performance_monitor.measure_async_operation(
            "concurrent_operations_test", concurrent_test
        )
        
        assert success, "Concurrent operations test failed"
        
        if isinstance(result, dict) and result.get("success"):
            assert result["success_rate"] >= 0.8, f"Too many concurrent operation failures: {result['success_rate']:.1%}"

if __name__ == "__main__":
    # Run E2E tests
    pytest.main([__file__, "-v", "--tb=short", "-m", "e2e"])