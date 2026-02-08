"""
Root conftest.py - Pytest configuration for monorepo

This file is auto-discovered by pytest and sets up shared configuration
for all test directories in the monorepo.
"""

import sys
from pathlib import Path

# Add src/cofounder_agent to Python path so tests can import from it
backend_path = Path(__file__).parent / "src" / "cofounder_agent"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

print(f"✓ Added backend to PYTHONPATH: {backend_path}")
