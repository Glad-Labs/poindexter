"""Shared configuration utilities for Glad Labs scripts."""

import os


def load_api_token():
    """Load the GLADLABS_KEY from environment or OpenClaw workspace .env."""
    token = os.getenv("GLADLABS_KEY", "")
    if not token:
        env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("GLADLABS_KEY="):
                    token = line.split("=", 1)[1].strip()
    return token
