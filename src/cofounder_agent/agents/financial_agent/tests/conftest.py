"""
Pytest configuration for Financial Agent tests.

Provides shared fixtures and test configuration for cost tracking
and financial analysis tests.
"""

import pytest
import sys
from pathlib import Path

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))


# ============================================================================
# PYTEST CONFIGURATION
# ============================================================================


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests for individual functions/methods")
    config.addinivalue_line(
        "markers", "integration: Integration tests requiring multiple components"
    )
    config.addinivalue_line("markers", "api: Tests requiring API calls")
    config.addinivalue_line("markers", "slow: Slow-running tests")
