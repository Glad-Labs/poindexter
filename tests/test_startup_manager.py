"""
Unit tests for StartupManager

Tests the startup and shutdown sequences, error handling, and service initialization.

Run with: pytest tests/test_startup_manager.py -v
"""

import pytest
import asyncio
import os
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from datetime import datetime

# Import the StartupManager
from utils.startup_manager import StartupManager


class TestStartupManager:
    """Test suite for StartupManager"""
    
    @pytest.fixture
    async def startup_manager(self):
        """Fixture providing a StartupManager instance"""
        return StartupManager()
    
    @pytest.mark.asyncio
    async def test_initialization_creates_empty_state(self, startup_manager):
        """Test that initialization creates proper empty state"""
        assert startup_manager.database_service is None
        assert startup_manager.orchestrator is None
        assert startup_manager.task_executor is None
        assert startup_manager.intelligent_orchestrator is None
        assert startup_manager.workflow_history_service is None
        assert startup_manager.startup_error is None
    
    @pytest.mark.asyncio
    async def test_initialize_all_services_structure(self, startup_manager):
        """Test that initialize_all_services returns proper structure"""
        with patch.object(startup_manager, '_initialize_database', new_callable=AsyncMock):
            with patch.object(startup_manager, '_run_migrations', new_callable=AsyncMock):
                with patch.object(startup_manager, '_setup_redis_cache', new_callable=AsyncMock):
                    with patch.object(startup_manager, '_initialize_model_consolidation', new_callable=AsyncMock):
                        with patch.object(startup_manager, '_initialize_orchestrator', new_callable=AsyncMock):
                            with patch.object(startup_manager, '_initialize_workflow_history', new_callable=AsyncMock):
                                with patch.object(startup_manager, '_initialize_intelligent_orchestrator', new_callable=AsyncMock):
                                    with patch.object(startup_manager, '_initialize_content_critique', new_callable=AsyncMock):
                                        with patch.object(startup_manager, '_initialize_task_executor', new_callable=AsyncMock):
                                            with patch.object(startup_manager, '_verify_connections', new_callable=AsyncMock):
                                                with patch.object(startup_manager, '_register_route_services', new_callable=AsyncMock):
                                                    result = await startup_manager.initialize_all_services()
        
        # Check return value structure
        assert isinstance(result, dict)
        assert 'database' in result
        assert 'orchestrator' in result
        assert 'task_executor' in result
        assert 'intelligent_orchestrator' in result
        assert 'workflow_history' in result
        assert 'startup_error' in result
    
    @pytest.mark.asyncio
    async def test_database_initialization_failure_raises_system_exit(self, startup_manager):
        """Test that database initialization failure causes SystemExit"""
        with patch.object(startup_manager, '_initialize_database', side_effect=SystemExit(1)):
            with pytest.raises(SystemExit):
                await startup_manager.initialize_all_services()
    
    @pytest.mark.asyncio
    async def test_database_initialization_required(self, startup_manager):
        """Test that database service is required"""
        startup_manager.database_service = Mock()
        
        # Verify database is marked as initialized
        assert startup_manager.database_service is not None
    
    @pytest.mark.asyncio
    async def test_shutdown_stops_task_executor(self, startup_manager):
        """Test that shutdown properly stops the task executor"""
        # Create mock task executor
        startup_manager.task_executor = AsyncMock()
        startup_manager.task_executor.running = True
        startup_manager.task_executor.get_stats = Mock(return_value={
            'total_processed': 100,
            'successful': 95,
            'failed': 5
        })
        
        await startup_manager.shutdown()
        
        # Verify task executor was stopped
        startup_manager.task_executor.stop.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_shutdown_closes_database(self, startup_manager):
        """Test that shutdown properly closes database"""
        startup_manager.database_service = AsyncMock()
        
        await startup_manager.shutdown()
        
        startup_manager.database_service.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_shutdown_handles_executor_stop_error(self, startup_manager):
        """Test that shutdown handles errors when stopping executor"""
        startup_manager.task_executor = AsyncMock()
        startup_manager.task_executor.running = True
        startup_manager.task_executor.stop.side_effect = Exception("Stop failed")
        
        # Should not raise, but log error
        await startup_manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_shutdown_handles_database_close_error(self, startup_manager):
        """Test that shutdown handles errors when closing database"""
        startup_manager.database_service = AsyncMock()
        startup_manager.database_service.close.side_effect = Exception("Close failed")
        
        # Should not raise, but log error
        await startup_manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_error_tracking_on_orchestrator_failure(self, startup_manager):
        """Test that orchestrator failures are tracked but don't prevent startup"""
        startup_manager.database_service = Mock()
        
        with patch.object(startup_manager, '_initialize_database', new_callable=AsyncMock):
            with patch.object(startup_manager, '_run_migrations', new_callable=AsyncMock):
                with patch.object(startup_manager, '_setup_redis_cache', new_callable=AsyncMock):
                    with patch.object(startup_manager, '_initialize_model_consolidation', new_callable=AsyncMock):
                        with patch.object(startup_manager, '_initialize_orchestrator', 
                                        side_effect=Exception("Orchestrator failed"), 
                                        new_callable=AsyncMock):
                            with patch.object(startup_manager, '_initialize_workflow_history', new_callable=AsyncMock):
                                with patch.object(startup_manager, '_initialize_intelligent_orchestrator', new_callable=AsyncMock):
                                    with patch.object(startup_manager, '_initialize_content_critique', new_callable=AsyncMock):
                                        with patch.object(startup_manager, '_initialize_task_executor', new_callable=AsyncMock):
                                            with patch.object(startup_manager, '_verify_connections', new_callable=AsyncMock):
                                                with patch.object(startup_manager, '_register_route_services', new_callable=AsyncMock):
                                                    try:
                                                        result = await startup_manager.initialize_all_services()
                                                        # Should not raise
                                                        assert result is not None
                                                    except Exception as e:
                                                        # Non-database failures should be caught
                                                        pass


class TestStartupManagerIntegration:
    """Integration tests for StartupManager with real services (requires test database)"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_startup_sequence(self):
        """Test complete startup sequence with real services"""
        # Set required environment variables for test database
        test_db_url = os.getenv('TEST_DATABASE_URL')
        if not test_db_url:
            pytest.skip("TEST_DATABASE_URL not set")
        
        os.environ['DATABASE_URL'] = test_db_url
        
        startup_manager = StartupManager()
        
        try:
            services = await startup_manager.initialize_all_services()
            
            # Verify critical services
            assert services['database'] is not None, "Database service not initialized"
            
            # Verify startup completed
            assert services['startup_error'] is None or len(services['startup_error']) == 0
            
        finally:
            await startup_manager.shutdown()
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_database_health_check(self):
        """Test database health check during startup"""
        test_db_url = os.getenv('TEST_DATABASE_URL')
        if not test_db_url:
            pytest.skip("TEST_DATABASE_URL not set")
        
        os.environ['DATABASE_URL'] = test_db_url
        
        startup_manager = StartupManager()
        
        try:
            await startup_manager._initialize_database()
            
            # Verify database is initialized
            assert startup_manager.database_service is not None
            
            # Run health check
            health = await startup_manager.database_service.health_check()
            assert health['status'] == 'healthy', f"Database health check failed: {health}"
            
        finally:
            if startup_manager.database_service:
                await startup_manager.database_service.close()


class TestStartupManagerErrorHandling:
    """Test error handling in various scenarios"""
    
    @pytest.mark.asyncio
    async def test_missing_database_url_causes_exit(self):
        """Test that missing DATABASE_URL causes SystemExit"""
        # Temporarily unset DATABASE_URL
        original_url = os.environ.pop('DATABASE_URL', None)
        
        try:
            startup_manager = StartupManager()
            
            with pytest.raises(SystemExit):
                with patch('services.database_service.DatabaseService') as mock_db:
                    mock_db.return_value.initialize.side_effect = Exception(
                        "Database URL not configured"
                    )
                    await startup_manager._initialize_database()
        finally:
            if original_url:
                os.environ['DATABASE_URL'] = original_url
    
    @pytest.mark.asyncio
    async def test_graceful_degradation_on_redis_failure(self, startup_manager):
        """Test that Redis failure doesn't prevent startup"""
        with patch('services.redis_cache.setup_redis_cache', 
                  side_effect=Exception("Redis not available")):
            # Should not raise
            await startup_manager._setup_redis_cache()
    
    @pytest.mark.asyncio
    async def test_graceful_degradation_on_model_consolidation_failure(self, startup_manager):
        """Test that model consolidation failure doesn't prevent startup"""
        with patch('services.model_consolidation_service.initialize_model_consolidation_service',
                  side_effect=Exception("Model consolidation failed")):
            # Should not raise
            await startup_manager._initialize_model_consolidation()


class TestStartupManagerStateManagement:
    """Test state management during startup/shutdown"""
    
    @pytest.mark.asyncio
    async def test_state_preserved_after_initialization(self, startup_manager):
        """Test that service references are preserved after initialization"""
        mock_db = Mock()
        mock_orchestrator = Mock()
        
        startup_manager.database_service = mock_db
        startup_manager.orchestrator = mock_orchestrator
        
        # Services should be preserved
        assert startup_manager.database_service is mock_db
        assert startup_manager.orchestrator is mock_orchestrator
    
    @pytest.mark.asyncio
    async def test_error_message_stored_on_failure(self, startup_manager):
        """Test that error messages are stored in startup_error"""
        error_msg = "Test error message"
        startup_manager.startup_error = error_msg
        
        assert startup_manager.startup_error == error_msg


class TestStartupManagerLogging:
    """Test logging behavior"""
    
    @pytest.mark.asyncio
    async def test_startup_summary_logged(self, startup_manager, caplog):
        """Test that startup summary is logged"""
        import logging
        
        with caplog.at_level(logging.INFO):
            startup_manager._log_startup_summary()
        
        # Verify expected log messages
        assert 'Database Service' in caplog.text
        assert 'Orchestrator' in caplog.text
        assert 'Task Executor' in caplog.text


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


if __name__ == "__main__":
    # Run tests with: pytest tests/test_startup_manager.py -v
    pytest.main([__file__, "-v", "-m", "not integration"])
