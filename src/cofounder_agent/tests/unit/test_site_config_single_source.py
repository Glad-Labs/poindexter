"""Regression: SiteConfig resolves from ``app.state.container``, never the
legacy ``app.state.site_config`` attribute.

After the worker hot-reload fix collapsed the two SiteConfig instances into
one (``build_container`` reuses the lifespan instance), the redundant
``app.state.site_config`` attribute was removed and every reader repointed
to the container. These tests pin that single-source invariant: the auth
middleware helper and the FastAPI route dependency both resolve via
``app.state.container`` and do NOT depend on ``app.state.site_config``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from middleware.api_token_auth import _request_site_config
from services.container import AppContainer
from services.container_registry import set_container
from services.site_config import SiteConfig
from utils.route_utils import get_site_config_dependency


@pytest.fixture(autouse=True)
def _no_registered_container():
    """Clear any process-wide container the conftest registered so the
    negative assertions exercise the true fallback, not a leaked container."""
    set_container(None)
    yield
    set_container(None)


def _request(container=None, legacy_site_config=None):
    """Build a minimal request whose ``app.state`` carries only what each
    test sets — SimpleNamespace (not MagicMock) so absent attributes are
    genuinely absent rather than auto-created truthy mocks."""
    state = SimpleNamespace()
    if container is not None:
        state.container = container
    if legacy_site_config is not None:
        state.site_config = legacy_site_config
    return SimpleNamespace(app=SimpleNamespace(state=state))


class TestMiddlewareResolvesViaContainer:
    def test_reads_container_site_config(self):
        sc = SiteConfig(initial_config={"environment": "production"})
        req = _request(container=AppContainer(site_config=sc, pool=MagicMock()))
        assert _request_site_config(req) is sc

    def test_ignores_legacy_site_config_attribute(self):
        legacy = SiteConfig(initial_config={"environment": "development"})
        req = _request(legacy_site_config=legacy)  # no container anywhere
        assert _request_site_config(req) is None


class TestDependencyResolvesViaContainer:
    def test_reads_container_site_config(self):
        sc = SiteConfig(initial_config={"site_url": "https://x"})
        req = _request(container=AppContainer(site_config=sc, pool=MagicMock()))
        assert get_site_config_dependency(req) is sc

    def test_ignores_legacy_site_config_attribute(self):
        legacy = SiteConfig(initial_config={"site_url": "https://legacy"})
        req = _request(legacy_site_config=legacy)  # no container anywhere
        assert get_site_config_dependency(req) is not legacy
