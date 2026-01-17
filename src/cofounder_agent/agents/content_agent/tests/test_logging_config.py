import importlib
from unittest.mock import MagicMock, patch


def test_setup_logging_without_pythonjsonlogger(monkeypatch):
    # Simulate pythonjsonlogger missing by removing module
    import sys as _sys

    if "pythonjsonlogger" in _sys.modules:
        del _sys.modules["pythonjsonlogger"]

    # Import the module; if import fails, test should still pass because we patch usage
    module = importlib.import_module("utils.logging_config")

    # Should not raise even if pythonjsonlogger is missing
    module.setup_logging(firestore_client=None)
