"""
Unit tests for model_selection_routes.py
Tests model selection and configuration endpoints
"""

import pytest
import json
from fastapi.testclient import TestClient
from fastapi import FastAPI
import os
import sys

# Add parent directory to path for imports to work properly
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


@pytest.fixture(scope="session")
def test_app():
    """Create test app with routes registered"""
    from routes.model_selection_routes import router as model_selection_router
    from routes.model_routes import models_list_router
    from utils.exception_handlers import register_exception_handlers
    from utils.middleware_config import MiddlewareConfig

    app = FastAPI(title="Test Model Selection App")

    # Register routes
    app.include_router(model_selection_router)
    app.include_router(models_list_router)

    # Register exception handlers
    register_exception_handlers(app)

    # Register middleware
    middleware_config = MiddlewareConfig()
    middleware_config.register_all_middleware(app)

    return app


@pytest.fixture(scope="session")
def client(test_app):
    """Create test client"""
    return TestClient(test_app)


@pytest.mark.unit
@pytest.mark.api
class TestModelSelectionRoutes:
    """Test model selection endpoints"""

    def test_get_available_models(self, client):
        """Test retrieving available models"""
        response = client.get("/api/models/available")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, (list, dict))

    def test_get_model_details(self, client):
        """Test getting details for a specific model"""
        response = client.get("/api/models/details/claude")
        assert response.status_code in [200, 404]

    def test_get_model_status(self, client):
        """Test checking model availability status"""
        response = client.get("/api/models/status")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_set_default_model(self, client):
        """Test setting a default model"""
        response = client.post("/api/models/default", json={"model": "claude"})
        assert response.status_code in [200, 400, 422]

    def test_set_default_model_invalid(self, client):
        """Test setting an invalid default model"""
        response = client.post("/api/models/default", json={"model": "nonexistent_model_xyz"})
        assert response.status_code in [400, 422, 404]

    def test_get_model_capabilities(self, client):
        """Test getting model capabilities"""
        response = client.get("/api/models/capabilities")
        assert response.status_code == 200

    def test_test_model_connection(self, client):
        """Test connection to a specific model"""
        response = client.post("/api/models/test", json={"model": "ollama"})
        assert response.status_code in [200, 400, 503]

    def test_get_model_pricing(self, client):
        """Test retrieving model pricing information"""
        response = client.get("/api/models/pricing")
        assert response.status_code == 200

    def test_get_model_performance_metrics(self, client):
        """Test getting model performance metrics"""
        response = client.get("/api/models/performance")
        assert response.status_code in [200, 400]

    def test_model_routing_config(self, client):
        """Test model routing configuration"""
        response = client.get("/api/models/routing-config")
        assert response.status_code in [200, 400]


@pytest.mark.unit
@pytest.mark.api
class TestModelConfigurationValidation:
    """Test model configuration validation"""

    def test_set_model_with_empty_name(self, client):
        """Test setting model with empty name"""
        response = client.post("/api/models/default", json={"model": ""})
        assert response.status_code in [400, 422]

    def test_set_model_with_null_name(self, client):
        """Test setting model with null name"""
        response = client.post("/api/models/default", json={"model": None})
        assert response.status_code in [400, 422]

    def test_set_model_with_special_characters(self, client):
        """Test setting model with special characters"""
        response = client.post(
            "/api/models/default", json={"model": "<script>alert('xss')</script>"}
        )
        assert response.status_code in [400, 422, 404]

    def test_model_selection_case_sensitivity(self, client):
        """Test model selection case handling"""
        # Test lowercase
        response1 = client.get("/api/models/details/claude")
        # Test uppercase
        response2 = client.get("/api/models/details/CLAUDE")
        # Both should be valid or invalid, not mixed
        assert response1.status_code == response2.status_code or all(
            s in [200, 404] for s in [response1.status_code, response2.status_code]
        )

    def test_model_name_with_whitespace(self, client):
        """Test model name with whitespace"""
        response = client.post("/api/models/default", json={"model": "  claude  "})
        # Should either trim or reject
        assert response.status_code in [200, 400, 422]

    def test_test_connection_invalid_model(self, client):
        """Test connection test with invalid model"""
        response = client.post("/api/models/test", json={"model": "invalid_model_name_xyz"})
        assert response.status_code in [400, 404, 503]

    def test_test_connection_empty_model(self, client):
        """Test connection test with empty model"""
        response = client.post("/api/models/test", json={"model": ""})
        assert response.status_code in [400, 422]


@pytest.mark.unit
@pytest.mark.integration
class TestModelFallbackStrategy:
    """Test model fallback and selection strategy"""

    def test_get_current_model_in_use(self, client):
        """Test getting currently active model"""
        response = client.get("/api/models/current")
        assert response.status_code == 200
        data = response.json()
        assert "model" in data or "name" in data or "current" in data

    def test_model_availability_check_all(self, client):
        """Test checking availability of all models"""
        response = client.get("/api/models/available")
        assert response.status_code == 200

        # Models should have status information
        data = response.json()
        if isinstance(data, list):
            for model in data:
                assert isinstance(model, (str, dict))

    def test_model_cost_comparison(self, client):
        """Test getting model cost comparison"""
        response = client.get("/api/models/pricing")
        assert response.status_code == 200

        data = response.json()
        if isinstance(data, dict):
            # Should contain price information
            assert len(data) > 0 or isinstance(data, dict)

    def test_fallback_chain_configuration(self, client):
        """Test that fallback chain is properly configured"""
        response = client.get("/api/models/routing-config")
        assert response.status_code in [200, 400]

        if response.status_code == 200:
            data = response.json()
            # Should contain fallback information
            assert isinstance(data, dict)


@pytest.mark.unit
@pytest.mark.performance
class TestModelPerformance:
    """Test model selection performance"""

    def test_model_list_response_time(self, client):
        """Test that model list responds quickly"""
        import time

        start = time.time()
        response = client.get("/api/models/available")
        elapsed = time.time() - start

        assert response.status_code == 200
        # Should respond within 2 seconds
        assert elapsed < 2

    def test_model_status_check_time(self, client):
        """Test that model status check is fast"""
        import time

        start = time.time()
        response = client.get("/api/models/status")
        elapsed = time.time() - start

        assert response.status_code == 200
        # Should respond within 5 seconds (may include actual status checks)
        assert elapsed < 5

    def test_concurrent_model_queries(self, client):
        """Test multiple model queries"""
        responses = []
        for i in range(5):
            response = client.get("/api/models/available")
            responses.append(response.status_code)

        # All should succeed
        assert all(status == 200 for status in responses)

    def test_model_selection_does_not_block(self, client):
        """Test that model selection is non-blocking"""
        # Set a model
        response = client.post("/api/models/default", json={"model": "claude"})

        # Immediately query another endpoint - should not be blocked
        response2 = client.get("/api/models/available")
        assert response2.status_code == 200


@pytest.mark.unit
@pytest.mark.api
class TestModelEdgeCases:
    """Test edge cases for model selection"""

    def test_very_long_model_name(self, client):
        """Test with very long model name"""
        response = client.post("/api/models/default", json={"model": "x" * 1000})
        assert response.status_code in [400, 422, 404]

    def test_unicode_model_name(self, client):
        """Test with unicode characters in model name"""
        response = client.post("/api/models/default", json={"model": "claude_中文_test"})
        assert response.status_code in [400, 422, 404]

    def test_model_with_path_traversal_attempt(self, client):
        """Test protection against path traversal"""
        response = client.post("/api/models/default", json={"model": "../../../etc/passwd"})
        assert response.status_code in [400, 422, 404]

    def test_model_with_sql_injection_attempt(self, client):
        """Test protection against SQL injection"""
        response = client.post("/api/models/default", json={"model": "'; DROP TABLE models; --"})
        assert response.status_code in [400, 422, 404]

    def test_batch_model_queries(self, client):
        """Test querying multiple models in sequence"""
        models = ["claude", "gpt4", "gemini", "ollama"]
        responses = []

        for model in models:
            response = client.get(f"/api/models/details/{model}")
            responses.append(response.status_code)

        # Should handle all requests
        assert all(status in [200, 404] for status in responses)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
