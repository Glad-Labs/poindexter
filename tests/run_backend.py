#!/usr/bin/env python
"""
Wrapper script to run FastAPI backend with proper Python venv.
This avoids the `poetry run` namespace package import issues.
"""
import subprocess
import sys
from pathlib import Path
import os

# Determine venv path - look for py3.13
venv_candidates = [
    Path.home() / "AppData" / "Local" / "pypoetry" / "Cache" / "virtualenvs" / "glad-labs-u37iqGWH-py3.13" / "Scripts" / "python.exe",
    Path.home() / "AppData" / "Local" / "pypoetry" / "Cache" / "virtualenvs" / "glad-labs-backend-YHugfB---py3.13" / "Scripts" / "python.exe",
]

venv_path = None
for candidate in venv_candidates:
    if candidate.exists():
        venv_path = candidate
        break

if not venv_path:
    print(f"ERROR: Poetry venv (Python 3.13) not found!")
    print(f"Checked: {venv_candidates}")
    sys.exit(1)

# Get the project root and backend directory
project_root = Path(__file__).parent
backend_dir = project_root / "src" / "cofounder_agent"

# Change to the backend directory
os.chdir(backend_dir)

# Run uvicorn with the venv Python
cmd = [
    str(venv_path),
    "-m",
    "uvicorn",
    "main:app",
    "--reload",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
]

print(f"[Backend Launcher] Starting FastAPI backend")
print(f"[Backend Launcher] Project root: {project_root}")
print(f"[Backend Launcher] Backend dir: {backend_dir}")
print(f"[Backend Launcher] Python: {venv_path}")
print(f"[Backend Launcher] Current dir: {os.getcwd()}")
print()

result = subprocess.run(cmd, cwd=str(backend_dir))
sys.exit(result.returncode)
