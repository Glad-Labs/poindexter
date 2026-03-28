"""
Tests for utils/service_dependencies.py

Covers:
- get_unified_orchestrator: success and missing (HTTPException 500)
- get_quality_service: success and missing
- get_database_service: success (app.state.db_service), fallback (app.state.database), missing
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from utils.service_dependencies import (
    get_database_service,
    get_quality_service,
    get_unified_orchestrator,
)


def _make_request(state_attrs: dict):
    """Build a minimal mock Request with app.state attributes."""
    state = MagicMock()
    # Configure getattr to return values from state_attrs dict
    for attr, val in state_attrs.items():
        setattr(state, attr, val)

    app = MagicMock()
    app.state = state

    req = MagicMock()
    req.app = app
    return req


class TestGetUnifiedOrchestrator:
    def test_returns_orchestrator_when_present(self):
        mock_orchestrator = MagicMock()
        req = _make_request({"unified_orchestrator": mock_orchestrator})
        result = get_unified_orchestrator(req)
        assert result is mock_orchestrator

    def test_raises_500_when_not_initialized(self):
        req = _make_request({"unified_orchestrator": None})
        with pytest.raises(HTTPException) as exc_info:
            get_unified_orchestrator(req)
        assert exc_info.value.status_code == 500
        assert "UnifiedOrchestrator" in exc_info.value.detail

    def test_raises_500_when_attribute_missing(self):
        # getattr returns None for missing attributes
        state = MagicMock(spec=[])  # no attributes defined
        app = MagicMock()
        app.state = state
        req = MagicMock()
        req.app = app
        with pytest.raises(HTTPException) as exc_info:
            get_unified_orchestrator(req)
        assert exc_info.value.status_code == 500


class TestGetQualityService:
    def test_returns_quality_service_when_present(self):
        mock_service = MagicMock()
        req = _make_request({"quality_service": mock_service})
        result = get_quality_service(req)
        assert result is mock_service

    def test_raises_500_when_not_initialized(self):
        req = _make_request({"quality_service": None})
        with pytest.raises(HTTPException) as exc_info:
            get_quality_service(req)
        assert exc_info.value.status_code == 500
        assert "UnifiedQualityService" in exc_info.value.detail

    def test_raises_500_when_attribute_missing(self):
        state = MagicMock(spec=[])
        app = MagicMock()
        app.state = state
        req = MagicMock()
        req.app = app
        with pytest.raises(HTTPException) as exc_info:
            get_quality_service(req)
        assert exc_info.value.status_code == 500


class TestGetDatabaseService:
    def test_returns_db_service_from_db_service_attr(self):
        mock_db = MagicMock()
        req = _make_request({"db_service": mock_db, "database": None})
        result = get_database_service(req)
        assert result is mock_db

    def test_falls_back_to_database_attr_when_db_service_is_none(self):
        mock_db = MagicMock()
        req = _make_request({"db_service": None, "database": mock_db})
        result = get_database_service(req)
        assert result is mock_db

    def test_raises_500_when_both_attrs_are_none(self):
        req = _make_request({"db_service": None, "database": None})
        with pytest.raises(HTTPException) as exc_info:
            get_database_service(req)
        assert exc_info.value.status_code == 500
        assert "DatabaseService" in exc_info.value.detail

    def test_prefers_db_service_over_database(self):
        primary = MagicMock(name="primary")
        fallback = MagicMock(name="fallback")
        req = _make_request({"db_service": primary, "database": fallback})
        result = get_database_service(req)
        assert result is primary

    def test_detail_mentions_service_not_initialized(self):
        req = _make_request({"db_service": None, "database": None})
        with pytest.raises(HTTPException) as exc_info:
            get_database_service(req)
        assert (
            "not initialized" in exc_info.value.detail.lower()
            or "DatabaseService" in exc_info.value.detail
        )
