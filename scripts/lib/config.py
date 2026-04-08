"""Shared configuration utilities for Glad Labs scripts."""

import os


def load_api_token():
    """Load the API token from environment or project .env.

    Checks (in order):
    1. API_TOKEN env var (matches what the FastAPI backend validates)
    2. GLADLABS_KEY env var (legacy)
    3. API_TOKEN from project .env file
    4. GLADLABS_KEY from OpenClaw workspace .env (legacy fallback)
    """
    token = os.getenv("API_TOKEN", "") or os.getenv("GLADLABS_KEY", "")
    if not token:
        # Try project .env first
        project_env = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        if os.path.exists(project_env):
            for line in open(project_env, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if line.startswith("API_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if not token:
        # Legacy fallback: OpenClaw workspace
        env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8", errors="ignore"):
                if line.startswith("GLADLABS_KEY="):
                    token = line.split("=", 1)[1].strip()
    return token
