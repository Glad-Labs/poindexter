#!/usr/bin/env python
"""
Backend startup script with proper path handling
Ensures PYTHONPATH includes the src directory for proper imports
"""

import os
import sys
import subprocess

# Get the project root (go up 3 levels: cofounder_agent -> src -> glad-labs-website)
script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(script_dir)
project_root = os.path.dirname(src_dir)

# Ensure src directory is in Python path
sys.path.insert(0, src_dir)
os.environ['PYTHONPATH'] = src_dir + os.pathsep + os.environ.get('PYTHONPATH', '')

# Change to project root
os.chdir(project_root)

print(f"[Backend] Project root: {project_root}")
print(f"[Backend] Python path includes: {src_dir}")
print(f"[Backend] Working directory: {os.getcwd()}")
print(f"[Backend] Starting uvicorn...\n")

# Run uvicorn with proper module path
# From project root, we can import src.cofounder_agent.main
cmd = [
    sys.executable,
    "-m",
    "uvicorn",
    "src.cofounder_agent.main:app",
    "--reload",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
]

try:
    subprocess.run(cmd, check=True)
except KeyboardInterrupt:
    print("\n[Backend] Shutdown requested")
except Exception as e:
    print(f"[Backend] Error: {e}")
    sys.exit(1)
