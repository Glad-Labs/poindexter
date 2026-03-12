"""
Unit tests for services/health_service.py

Tests HealthService:
- check_health returns 'healthy' when startup is complete and DB is healthy
- check_health returns 'degraded' when startup_error is set
- check_health returns 'starting' when startup is not yet complete
- check_health returns 'degraded' component when DB health check raises
- check_health returns 'unavailable' component when no DB attached
- get_health_service returns a singleton

All tests are async. The FastAPI app and DatabaseService are mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.health_service import HealthService, get_health_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_app(db_service=None) -> MagicMock:
    """Build a minimal mock FastAPI app with optional state.database."""
    app = MagicMock()
    app.state = MagicMock()
    app.state.database = db_service
    return app


def make_healthy_db() -> AsyncMock:
    """Return a mock DB that reports healthy status."""
    db = AsyncMock()
    db.health_check = AsyncMock(return_value={"status": "healthy"})
    return db


def make_failing_db() -> AsyncMock:
    """Return a mock DB whose health_check raises an exception."""
    db = AsyncMock()
    db.health_check = AsyncMock(side_effect=RuntimeError("connection refused"))
    return db


# ---------------------------------------------------------------------------
# set_startup_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetStartupStatus:
    def test_sets_error_and_complete(self):
        svc = HealthService(make_app())
        svc.set_startup_status(error="boom", complete=True)
        assert svc._startup_error == "boom"
        assert svc._startup_complete is True

    def test_defaults_to_no_error_incomplete(self):
        svc = HealthService(make_app())
        assert svc._startup_error is None
        assert svc._startup_complete is False


# ---------------------------------------------------------------------------
# check_health — startup states
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckHealthStartupStates:
    @pytest.mark.asyncio
    async def test_healthy_when_complete_no_error(self):
        db = make_healthy_db()
        svc = HealthService(make_app(db))
        svc.set_startup_status(complete=True)

        result = await svc.check_health()

        assert result["status"] == "healthy"
        assert "startup_error" not in result

    @pytest.mark.asyncio
    async def test_degraded_when_startup_error_set(self):
        db = make_healthy_db()
        svc = HealthService(make_app(db))
        svc.set_startup_status(error="startup failed: missing env var", complete=True)

        result = await svc.check_health()

        assert result["status"] == "degraded"
        assert result["startup_error"] == "startup failed: missing env var"

    @pytest.mark.asyncio
    async def test_starting_when_not_yet_complete(self):
        db = make_healthy_db()
        svc = HealthService(make_app(db))
        # Startup not yet marked complete

        result = await svc.check_health()

        assert result["status"] == "starting"
        assert result["startup_complete"] is False


# ---------------------------------------------------------------------------
# check_health — database component
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckHealthDatabaseComponent:
    @pytest.mark.asyncio
    async def test_healthy_db_component(self):
        svc = HealthService(make_app(make_healthy_db()))
        svc.set_startup_status(complete=True)

        result = await svc.check_health()

        assert result["components"]["database"] == "healthy"

    @pytest.mark.asyncio
    async def test_degraded_db_component_when_health_check_raises(self):
        svc = HealthService(make_app(make_failing_db()))
        svc.set_startup_status(complete=True)

        result = await svc.check_health()

        assert result["components"]["database"] == "degraded"

    @pytest.mark.asyncio
    async def test_unavailable_when_no_db_attached(self):
        app = make_app(db_service=None)
        svc = HealthService(app)
        svc.set_startup_status(complete=True)

        result = await svc.check_health()

        assert result["components"]["database"] == "unavailable"

    @pytest.mark.asyncio
    async def test_degraded_when_health_check_returns_non_healthy_status(self):
        db = AsyncMock()
        db.health_check = AsyncMock(return_value={"status": "degraded"})
        svc = HealthService(make_app(db))
        svc.set_startup_status(complete=True)

        result = await svc.check_health()

        assert result["components"]["database"] == "degraded"


# ---------------------------------------------------------------------------
# check_health — response shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckHealthResponseShape:
    @pytest.mark.asyncio
    async def test_response_has_required_keys(self):
        svc = HealthService(make_app(make_healthy_db()))
        svc.set_startup_status(complete=True)

        result = await svc.check_health()

        assert "status" in result
        assert "service" in result
        assert "version" in result
        assert "timestamp" in result
        assert "components" in result

    @pytest.mark.asyncio
    async def test_service_name_is_correct(self):
        svc = HealthService(make_app())
        svc.set_startup_status(complete=True)

        result = await svc.check_health()

        assert result["service"] == "cofounder-agent"


# ---------------------------------------------------------------------------
# get_health_service singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetHealthServiceSingleton:
    def test_returns_health_service_instance(self):
        app = make_app()
        svc = get_health_service(app)
        assert isinstance(svc, HealthService)

    def test_returns_same_instance_on_repeated_calls(self):
        # Reset the global singleton first to ensure a clean state
        import services.health_service as hs_module
        hs_module.health_service = None

        app = make_app()
        svc1 = get_health_service(app)
        svc2 = get_health_service(app)
        assert svc1 is svc2
