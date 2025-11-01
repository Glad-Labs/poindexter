#!/usr/bin/env python3
"""
Start FastAPI backend with environment loaded from .env.local

This script:
1. Loads environment variables from .env.local
2. Ensures DATABASE_URL is set for PostgreSQL
3. Starts the FastAPI server with uvicorn
"""

import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Get project root
PROJECT_ROOT = Path(__file__).parent.parent

# Load environment variables from .env.local
env_file = PROJECT_ROOT / ".env.local"
if env_file.exists():
    print(f"📦 Loading environment from: {env_file}")
    load_dotenv(env_file, override=True)
    print("✅ Environment loaded")
else:
    print(f"⚠️ Warning: .env.local not found at {env_file}")

# Verify critical environment variables
print("\n" + "="*70)
print("Environment Configuration")
print("="*70)

# Database configuration
database_url = os.getenv("DATABASE_URL", "")
database_client = os.getenv("DATABASE_CLIENT", "")

print(f"\nDatabase Configuration:")
print(f"  CLIENT: {database_client or 'sqlite (default)'}")
if database_url:
    # Mask password in output
    masked_url = database_url.replace(
        database_url.split('@')[0].split(':')[-1],
        '***'
    ) if '@' in database_url else database_url
    print(f"  URL: {masked_url}")
else:
    print(f"  URL: (using sqlite default)")

# AI Configuration
print(f"\nAI Configuration:")
print(f"  Ollama: {os.getenv('USE_OLLAMA', 'false')}")
print(f"  Ollama Host: {os.getenv('OLLAMA_HOST', 'not set')}")

api_keys = {
    "OpenAI": bool(os.getenv("OPENAI_API_KEY")),
    "Anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
    "Google": bool(os.getenv("GOOGLE_API_KEY")),
}
for provider, has_key in api_keys.items():
    print(f"  {provider}: {'✅' if has_key else '❌'}")

print("\n" + "="*70)
print("Starting FastAPI Server...")
print("="*70)
print()

# Change to cofounder_agent directory
os.chdir(PROJECT_ROOT / "src" / "cofounder_agent")

# Start uvicorn
try:
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"],
        check=False
    )
except KeyboardInterrupt:
    print("\n\n✋ Server stopped by user")
    sys.exit(0)
except Exception as e:
    print(f"\n❌ Error starting server: {e}")
    sys.exit(1)
