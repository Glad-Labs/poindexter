#!/usr/bin/env python3
"""
Simple test server for GLAD Labs AI Co-Founder
This script bypasses the complex import issues by running directly
"""

import sys
import os
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Find .env file in project root (two levels up from this file)
    env_path = Path(__file__).parent.parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✅ Loaded environment variables from {env_path}")
    else:
        print(f"⚠️  No .env file found at {env_path}")
except ImportError:
    print("⚠️  python-dotenv not installed. Environment variables from .env will not be loaded.")
    print("   Install with: pip install python-dotenv")

try:
    from main import app
    import uvicorn
    
    if __name__ == "__main__":
        print("🚀 Starting GLAD Labs AI Co-Founder Agent Server...")
        print("📡 Server will be available at http://localhost:8000")
        print("📖 API documentation at http://localhost:8000/docs")
        print("🔧 Development mode - Google Cloud services simulated")
        
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            reload=False,  # Disable reload for stability
            log_level="info"
        )
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("🔧 Falling back to basic server...")
    
    # Fallback implementation
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import uvicorn
    
    # Simple fallback app
    app = FastAPI(title="GLAD Labs AI Co-Founder (Fallback)")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    class CommandRequest(BaseModel):
        command: str
    
    @app.get("/")
    async def root():
        return {"message": "GLAD Labs AI Co-Founder Fallback Server", "status": "running"}
    
    @app.post("/command")
    async def process_command(request: CommandRequest):
        return {"response": f"Fallback response for: {request.command}", "status": "fallback"}
    
    if __name__ == "__main__":
        uvicorn.run(app, host="0.0.0.0", port=8000)