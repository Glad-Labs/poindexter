"""
Unit tests for services/container.py

Tests the ServiceContainer singleton pattern, registration,
retrieval, and helper functions.
"""

from unittest.mock import MagicMock

import pytest

from services.container import (
    ServiceContainer,
    get_service,
    initialize_services,
    register_service,
    service_container,
)


@pytest.fixture(autouse=True)
def reset_container():
    """Reset the global service_container before each test."""
    service_container.clear()
    yield
    service_container.clear()


class TestServiceContainerSingleton:
    """ServiceContainer uses the singleton pattern."""

    def test_singleton_returns_same_instance(self):
        a = ServiceContainer()
        b = ServiceContainer()
        assert a is b

    def test_global_service_container_is_instance(self):
        assert isinstance(service_container, ServiceContainer)


class TestServiceContainerRegisterGet:
    """Register and retrieve services."""

    def test_register_and_get_service(self):
        svc = MagicMock()
        service_container.register("my_service", svc)
        assert service_container.get("my_service") is svc

    def test_get_missing_service_returns_none(self):
        assert service_container.get("nonexistent") is None

    def test_register_overwrites_previous(self):
        svc1 = MagicMock()
        svc2 = MagicMock()
        service_container.register("svc", svc1)
        service_container.register("svc", svc2)
        assert service_container.get("svc") is svc2

    def test_get_all_returns_copy(self):
        svc = MagicMock()
        service_container.register("alpha", svc)
        all_services = service_container.get_all()
        assert "alpha" in all_services
        # Modifying the returned dict should not affect the container
        all_services["alpha"] = None
        assert service_container.get("alpha") is svc

    def test_clear_removes_all_services(self):
        service_container.register("a", MagicMock())
        service_container.register("b", MagicMock())
        service_container.clear()
        assert service_container.get_all() == {}


class TestHelperFunctions:
    """Module-level helper functions delegate to global container."""

    def test_register_service_and_get_service(self):
        svc = MagicMock()
        register_service("helper_svc", svc)
        assert get_service("helper_svc") is svc

    def test_get_service_missing_returns_none(self):
        assert get_service("does_not_exist") is None

    def test_initialize_services_registers_all(self):
        db = MagicMock()
        cache = MagicMock()
        app = MagicMock()
        initialize_services(app, database=db, cache=cache)
        assert get_service("database") is db
        assert get_service("cache") is cache

    def test_initialize_services_empty_kwargs(self):
        """Calling with no services should not raise."""
        app = MagicMock()
        initialize_services(app)
        assert get_service("database") is None
