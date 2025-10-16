# Testing Guide

This repo includes Python tests for the content agent. Below are quick steps for Windows PowerShell.

## Prerequisites

- Python 3.10+ installed and available in PATH
- Recommended: create/activate a virtual env scoped to the repo

## Install Python dependencies

From the repo root:

```powershell
# Optional: create a venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install pip deps
pip install -r requirements.txt
```

## Run tests

Run all tests from the repo root so the configured pytest paths work:

```powershell
pytest -q
```

To run just the new content-agent unit tests:

```powershell
pytest -q src/agents/content_agent/tests/test_config.py src/agents/content_agent/tests/test_logging_config.py src/agents/content_agent/tests/test_orchestrator_init.py src/agents/content_agent/tests/test_orchestrator_start_stop.py
```

Notes:

- Tests default to non-strict config validation and do not require a real .env; strict behavior is covered in a unit test.
- Google Cloud and Firestore heavy imports are stubbed in tests; no cloud auth is required.
- If you're in VS Code, ensure the Python interpreter is set to the virtualenv you created.
