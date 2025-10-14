"""
Unit Tests for Intelligent Co-Founder System
Comprehensive test coverage for core AI co-founder functionality
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timedelta
import json
import sys
import os

# Add project paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from conftest import (
    test_data_manager, mock_business_data, mock_tasks, 
    performance_monitor, test_utils, run_with_timeout,
    pytest_marks
)

# Import components to test
try:
    from intelligent_cofounder import IntelligentCoFounder, BusinessContext, BusinessInsight
    from business_intelligence import BusinessIntelligenceSystem
    from memory_system import AIMemorySystem, MemoryType, ImportanceLevel
    from notification_system import SmartNotificationSystem
    from advanced_dashboard import AdvancedBusinessDashboard
    from multi_agent_orchestrator import MultiAgentOrchestrator, TaskPriority, OrchestrationTask
    from voice_interface import VoiceInterfaceSystem, VoiceCommand
except ImportError as e:
    pytest.skip(f"Could not import required modules: {e}", allow_module_level=True)

@pytest_marks["unit"]
class TestIntelligentCoFounder:
    """Test the main IntelligentCoFounder class"""
    
    @pytest.fixture
    async def cofounder(self):
        """Create IntelligentCoFounder instance for testing"""
        with patch('intelligent_cofounder.MCPClientManager', return_value=None):
            cofounder = IntelligentCoFounder("Test Company")
            # Mock the advanced systems to avoid actual initialization
            cofounder.business_intelligence = Mock()
            cofounder.memory_system = Mock()
            cofounder.notification_system = Mock()
            cofounder.dashboard = Mock()
            cofounder.orchestrator = Mock()
            cofounder.voice_interface = Mock()
            return cofounder
    
    async def test_initialization(self, cofounder):
        """Test co-founder initialization"""
        assert cofounder.business_name == "Test Company"
        assert cofounder.initialized == False
        assert hasattr(cofounder, 'business_intelligence')
        assert hasattr(cofounder, 'memory_system')
        assert hasattr(cofounder, 'notification_system')
    
    async def test_chat_basic_functionality(self, cofounder, mock_business_data):
        """Test basic chat functionality"""
        # Mock the chat method's dependencies
        cofounder.memory_system.search_memories = AsyncMock(return_value=[])
        cofounder.memory_system.store_memory = AsyncMock()
        
        # Mock MCP manager to avoid actual API calls
        with patch.object(cofounder, 'mcp_manager', None):
            response = await cofounder.chat("Hello, how are you?", "test_session")
        
        assert isinstance(response, dict)
        assert "response" in response
        assert response["success"] == True
        assert len(response["response"]) > 0
    
    async def test_command_analysis(self, cofounder):
        """Test command analysis functionality"""
        test_commands = [
            "show me business metrics",
            "create a new task for blog writing", 
            "what is our revenue this month",
            "generate a strategic plan"
        ]
        
        for command in test_commands:
            result = await cofounder.analyze_command_intent(command)
            
            assert isinstance(result, dict)
            assert "intent" in result
            assert "confidence" in result
            assert "entities" in result
            assert result["confidence"] > 0
    
    async def test_task_creation(self, cofounder):
        """Test task creation functionality"""
        cofounder.memory_system.store_memory = AsyncMock()
        
        task_request = {
            "title": "Test Task",
            "description": "Create a test blog post",
            "priority": "medium",
            "category": "content"
        }
        
        result = await cofounder.create_task_from_request(task_request)
        
        assert isinstance(result, dict)
        assert "success" in result
        assert "task_id" in result or "error" in result
    
    async def test_strategic_planning(self, cofounder, performance_monitor):
        """Test strategic planning functionality"""
        cofounder.memory_system.store_memory = AsyncMock()
        
        planning_request = {
            "timeframe": "Q4 2025",
            "focus_areas": ["content", "growth", "automation"],
            "current_metrics": {"revenue": 50000, "growth_rate": 0.15}
        }
        
        async def run_strategic_planning():
            return await cofounder.create_strategic_plan(planning_request)
        
        result, duration, success = await performance_monitor.measure_async_operation(
            "strategic_planning", run_strategic_planning
        )
        
        assert success
        assert isinstance(result, dict)
        assert "plan" in result or "error" in result
        
        # Performance assertion
        assert duration < 10.0, f"Strategic planning took too long: {duration}s"

@pytest_marks["unit"] 
class TestBusinessIntelligenceSystem:
    """Test the BusinessIntelligenceSystem class"""
    
    @pytest.fixture
    def bi_system(self):
        """Create BusinessIntelligenceSystem for testing"""
        return BusinessIntelligenceSystem()
    
    async def test_analyze_business_performance(self, bi_system, mock_business_data, test_utils):
        """Test business performance analysis"""
        result = await bi_system.analyze_business_performance(mock_business_data)
        
        test_utils.assert_valid_response_structure(result, ["success", "analysis"])
        
        if result["success"]:
            analysis = result["analysis"]
            assert "overall_score" in analysis
            assert "key_insights" in analysis
            assert "recommendations" in analysis
            assert isinstance(analysis["overall_score"], (int, float))
    
    async def test_predict_trends(self, bi_system, mock_business_data):
        """Test trend prediction functionality"""
        historical_data = [mock_business_data] * 12  # 12 months of data
        
        result = await bi_system.predict_trends(historical_data, forecast_periods=3)
        
        assert isinstance(result, dict)
        assert "predictions" in result or "error" in result
        
        if "predictions" in result:
            predictions = result["predictions"]
            assert len(predictions) <= 3  # Should not exceed requested periods
    
    async def test_generate_insights(self, bi_system, mock_business_data):
        """Test insight generation"""
        result = await bi_system.generate_business_insights(mock_business_data)
        
        assert isinstance(result, dict)
        assert "insights" in result or "error" in result
        
        if "insights" in result:
            insights = result["insights"]
            assert isinstance(insights, list)
            for insight in insights:
                assert "type" in insight
                assert "message" in insight
                assert "priority" in insight

@pytest_marks["unit"]
class TestMultiAgentOrchestrator:
    """Test the MultiAgentOrchestrator class"""
    
    @pytest.fixture
    def orchestrator(self):
        """Create MultiAgentOrchestrator for testing"""
        orchestrator = MultiAgentOrchestrator()
        # Stop the orchestration loop for testing
        orchestrator.orchestration_active = False
        return orchestrator
    
    async def test_agent_initialization(self, orchestrator):
        """Test that agents are properly initialized"""
        assert len(orchestrator.agents) > 0
        
        for agent_id, agent in orchestrator.agents.items():
            assert agent.id == agent_id
            assert len(agent.capabilities) > 0
            assert agent.status.value in ["idle", "busy", "error", "offline"]
    
    async def test_task_creation(self, orchestrator):
        """Test task creation and queuing"""
        task_id = await orchestrator.create_task(
            name="Test Task",
            description="Test task for unit testing",
            requirements=["blog_writing"],
            priority=TaskPriority.MEDIUM
        )
        
        assert task_id in orchestrator.tasks
        assert task_id in orchestrator.task_queue
        
        task = orchestrator.tasks[task_id]
        assert task.name == "Test Task"
        assert task.priority == TaskPriority.MEDIUM
    
    async def test_agent_assignment(self, orchestrator):
        """Test task assignment to agents"""
        # Create a task
        task_id = await orchestrator.create_task(
            name="Content Creation Task",
            description="Create blog content",
            requirements=["blog_writing", "content_optimization"],
            priority=TaskPriority.HIGH
        )
        
        # Assign task
        assigned_agent_id = await orchestrator.assign_task(task_id)
        
        if assigned_agent_id:  # Task was successfully assigned
            task = orchestrator.tasks[task_id]
            agent = orchestrator.agents[assigned_agent_id]
            
            assert task.assigned_agent_id == assigned_agent_id
            assert agent.current_task_id == task_id
            assert task_id not in orchestrator.task_queue
    
    async def test_orchestration_metrics(self, orchestrator):
        """Test orchestration metrics calculation"""
        # Create some test tasks
        for i in range(5):
            await orchestrator.create_task(
                name=f"Test Task {i}",
                description=f"Test task {i}",
                requirements=["blog_writing"],
                priority=TaskPriority.MEDIUM
            )
        
        status = await orchestrator.get_orchestration_status()
        
        assert isinstance(status, dict)
        assert "agents" in status
        assert "tasks" in status
        assert "metrics" in status
        
        metrics = status["metrics"]
        assert "total_tasks" in metrics
        assert "agent_utilization" in metrics
        assert metrics["total_tasks"] >= 5

@pytest_marks["unit"]
class TestVoiceInterfaceSystem:
    """Test the VoiceInterfaceSystem class"""
    
    @pytest.fixture
    def voice_interface(self):
        """Create VoiceInterfaceSystem for testing"""
        return VoiceInterfaceSystem()
    
    async def test_voice_command_processing(self, voice_interface, mock_voice_commands):
        """Test voice command processing"""
        for command_data in mock_voice_commands:
            # Simulate audio data (empty bytes for testing)
            audio_data = b"mock_audio_data"
            
            with patch.object(voice_interface, '_simulate_speech_to_text', 
                            return_value={"success": True, "text": command_data["text"], 
                                       "confidence": command_data["confidence"]}):
                
                result = await voice_interface.process_voice_input(audio_data, "test_session")
                
                assert result["success"] == True
                assert "transcription" in result
                assert "confidence" in result
                assert "intent" in result
                assert result["transcription"] == command_data["text"]
    
    async def test_intent_extraction(self, voice_interface):
        """Test intent extraction from voice commands"""
        test_commands = [
            ("show me business metrics", "get_business_metrics"),
            ("create a new task", "create_task"),
            ("help me", "help_support"),
            ("what can you do", "help_support"),
            ("unknown command", "unknown")
        ]
        
        for text, expected_intent in test_commands:
            result = await voice_interface._extract_intent(text)
            
            assert "intent" in result
            assert result["intent"] == expected_intent
    
    async def test_voice_response_generation(self, voice_interface):
        """Test voice response generation"""
        # Test different intents
        test_intents = ["get_business_metrics", "create_task", "help_support"]
        
        for intent in test_intents:
            command = VoiceCommand(
                id="test_command",
                text=f"test command for {intent}",
                confidence=0.9,
                timestamp=datetime.now(),
                intent=intent
            )
            
            response = await voice_interface._generate_voice_response(command, "test_session")
            
            assert isinstance(response, dict)
            assert "success" in response
            assert "text" in response
            
            if response["success"]:
                assert len(response["text"]) > 0
    
    async def test_voice_analytics(self, voice_interface):
        """Test voice interface analytics"""
        # Add some mock commands to history
        for i in range(5):
            command = VoiceCommand(
                id=f"test_{i}",
                text=f"test command {i}",
                confidence=0.9,
                timestamp=datetime.now(),
                intent="test_intent",
                processed=True
            )
            voice_interface.voice_commands.append(command)
        
        analytics = await voice_interface.get_voice_analytics()
        
        assert isinstance(analytics, dict)
        assert "total_commands" in analytics
        assert "success_rate" in analytics
        assert "average_confidence" in analytics
        assert analytics["total_commands"] == 5

@pytest_marks["unit"]
class TestAdvancedDashboard:
    """Test the AdvancedBusinessDashboard class"""
    
    @pytest.fixture
    def dashboard(self):
        """Create AdvancedBusinessDashboard for testing"""
        return AdvancedBusinessDashboard()
    
    async def test_metrics_collection(self, dashboard, test_utils):
        """Test comprehensive metrics collection"""
        metrics = await dashboard.collect_comprehensive_metrics()
        
        assert isinstance(metrics, dict)
        if "error" not in metrics:
            # Validate structure
            required_sections = ["task_management", "content_performance", 
                               "financial", "system_performance"]
            
            for section in required_sections:
                assert section in metrics, f"Missing section: {section}"
    
    async def test_kpi_updates(self, dashboard):
        """Test KPI card updates"""
        # First collect metrics to populate KPIs
        await dashboard.collect_comprehensive_metrics()
        
        # Check if KPIs were created
        assert len(dashboard.kpis) > 0
        
        for kpi_name, kpi in dashboard.kpis.items():
            assert hasattr(kpi, 'title')
            assert hasattr(kpi, 'current_value')
            assert hasattr(kpi, 'trend')
            assert kpi.trend in ["up", "down", "stable"]
    
    async def test_dashboard_data_retrieval(self, dashboard):
        """Test complete dashboard data retrieval"""
        dashboard_data = await dashboard.get_dashboard_data()
        
        assert isinstance(dashboard_data, dict)
        assert "timestamp" in dashboard_data
        
        expected_sections = ["kpis", "metrics", "trends", "insights"]
        for section in expected_sections:
            assert section in dashboard_data
    
    async def test_business_insights_generation(self, dashboard):
        """Test business insights generation"""
        # Collect metrics first to generate insights
        await dashboard.collect_comprehensive_metrics()
        
        assert isinstance(dashboard.insights, list)
        
        for insight in dashboard.insights:
            assert "type" in insight
            assert "category" in insight
            assert "message" in insight
            assert insight["type"] in ["positive", "warning", "critical"]

@pytest_marks["unit"]
class TestNotificationSystem:
    """Test the SmartNotificationSystem class"""
    
    @pytest.fixture
    def notification_system(self):
        """Create SmartNotificationSystem for testing"""
        return SmartNotificationSystem()
    
    async def test_notification_processing(self, notification_system):
        """Test notification processing"""
        # Test different types of notifications
        test_notifications = [
            {"type": "business_alert", "message": "Revenue target exceeded", "priority": "high"},
            {"type": "system_alert", "message": "System performance optimal", "priority": "low"},
            {"type": "task_alert", "message": "Task completion rate low", "priority": "medium"}
        ]
        
        for notification in test_notifications:
            result = await notification_system.process_notification(
                notification["type"], 
                notification["message"], 
                notification["priority"]
            )
            
            assert isinstance(result, dict)
            # The notification system should handle all types without error
    
    async def test_smart_alert_generation(self, notification_system, mock_business_data):
        """Test smart alert generation from business data"""
        alerts = await notification_system.generate_smart_alerts(mock_business_data)
        
        assert isinstance(alerts, list)
        
        for alert in alerts:
            assert "type" in alert
            assert "message" in alert
            assert "priority" in alert
            assert "context" in alert

# Performance benchmarks
@pytest_marks["performance"]
class TestPerformanceBenchmarks:
    """Performance benchmark tests"""
    
    async def test_chat_response_performance(self, performance_monitor):
        """Test chat response performance"""
        with patch('intelligent_cofounder.MCPClientManager', return_value=None):
            cofounder = IntelligentCoFounder("Performance Test")
            cofounder.memory_system = Mock()
            cofounder.memory_system.search_memories = AsyncMock(return_value=[])
            cofounder.memory_system.store_memory = AsyncMock()
        
        async def chat_operation():
            return await cofounder.chat("Test message", "perf_test")
        
        # Run multiple iterations
        for i in range(5):
            result, duration, success = await performance_monitor.measure_async_operation(
                f"chat_response_{i}", chat_operation
            )
            
            # Chat should respond within 2 seconds
            assert duration < 2.0, f"Chat response too slow: {duration}s"
            assert success, "Chat operation failed"
        
        # Get overall performance summary
        summary = performance_monitor.get_performance_summary()
        assert summary["success_rate"] >= 0.8  # At least 80% success rate
        assert summary["average_duration"] < 1.5  # Average under 1.5 seconds
    
    async def test_orchestrator_task_assignment_performance(self, performance_monitor):
        """Test orchestrator task assignment performance"""
        orchestrator = MultiAgentOrchestrator()
        orchestrator.orchestration_active = False
        
        async def task_assignment_operation():
            task_id = await orchestrator.create_task(
                name="Performance Test Task",
                description="Task for performance testing",
                requirements=["blog_writing"],
                priority=TaskPriority.MEDIUM
            )
            return await orchestrator.assign_task(task_id)
        
        result, duration, success = await performance_monitor.measure_async_operation(
            "task_assignment", task_assignment_operation
        )
        
        # Task assignment should be fast
        assert duration < 0.5, f"Task assignment too slow: {duration}s"

# Integration helpers
@pytest_marks["unit"]
class TestSystemIntegration:
    """Test system integration components"""
    
    async def test_cofounder_orchestrator_integration(self):
        """Test integration between co-founder and orchestrator"""
        with patch('intelligent_cofounder.MCPClientManager', return_value=None):
            cofounder = IntelligentCoFounder("Integration Test")
            
            # Mock dependencies
            cofounder.memory_system = Mock()
            cofounder.memory_system.store_memory = AsyncMock()
            
            # Test task delegation
            result = await cofounder.delegate_task_to_agent(
                "Test task description",
                ["blog_writing"],
                "medium"
            )
            
            assert isinstance(result, dict)
            # Should succeed or provide meaningful error
            assert "success" in result or "error" in result
    
    async def test_voice_cofounder_integration(self):
        """Test integration between voice interface and co-founder"""
        voice_interface = VoiceInterfaceSystem()
        
        # Test voice command that should trigger co-founder actions
        test_audio = b"mock_audio"
        
        with patch.object(voice_interface, '_simulate_speech_to_text', 
                        return_value={"success": True, "text": "show me business metrics", 
                                    "confidence": 0.95}):
            
            result = await voice_interface.process_voice_input(test_audio, "integration_test")
            
            assert result["success"] == True
            assert "response" in result
            assert result["intent"] == "get_business_metrics"

if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])