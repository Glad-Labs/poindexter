"""
Shared pytest configuration and fixtures for all tests.
Central conftest.py for the entire test suite.
"""
import os
import sys
import asyncio
import pytest
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
project_root = str(Path(__file__).parent.parent)
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'src'))
sys.path.insert(0, os.path.join(project_root, 'src/cofounder_agent'))

# Load environment variables
env_local_path = os.path.join(project_root, ".env.local")
if os.path.exists(env_local_path):
    load_dotenv(env_local_path, override=True)

# Import shared test utilities
from test_utils import test_utils, performance_monitor, test_config, TestConfig


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_config_fixture():
    """Test configuration fixture."""
    return test_config


@pytest.fixture(scope="session")
def project_root_path():
    """Return the project root path."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def performance_monitor_fixture():
    """Performance monitor fixture."""
    return performance_monitor


@pytest.fixture(scope="session")
def test_utils_fixture():
    """Test utilities fixture."""
    return test_utils


# Expose at module level for direct imports (backward compatibility with old tests)
TEST_CONFIG = test_config.__dict__
mock_api_responses = {}
performance_monitor = performance_monitor
test_utils = test_utils


# Pytest markers
def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "unit: unit tests")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "e2e: end-to-end tests")
    config.addinivalue_line("markers", "api: API endpoint tests")
    config.addinivalue_line("markers", "slow: slow running tests")
    config.addinivalue_line("markers", "skip_ci: skip in CI environment")
    config.addinivalue_line("markers", "asyncio: async tests")
    config.addinivalue_line("markers", "performance: performance tests")
    config.addinivalue_line("markers", "websocket: WebSocket tests")

