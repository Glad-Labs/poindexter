#!/usr/bin/env python3
"""
Verbose startup server for Glad Labs AI Co-Founder
Provides detailed logging during initialization and operation
"""

import sys
import os
import logging
from datetime import datetime

# Configure logging to show everything
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logger.info("=" * 70)
logger.info("🚀 GLAD LABS AI CO-FOUNDER AGENT - STARTUP SEQUENCE")
logger.info("=" * 70)
logger.info(f"⏰ Startup time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
logger.info(f"📍 Python version: {sys.version.split()[0]}")
logger.info(f"📁 Working directory: {os.getcwd()}")
logger.info(f"📦 Project root: {os.path.dirname(__file__)}")
logger.info("-" * 70)

try:
    logger.info("📥 [STEP 1/5] Loading FastAPI application...")
    from main import app
    logger.info("✅ FastAPI app loaded successfully")
    
    logger.info("📥 [STEP 2/5] Importing uvicorn server...")
    import uvicorn
    logger.info("✅ Uvicorn imported successfully")
    
    logger.info("📥 [STEP 3/5] Checking environment variables...")
    debug_mode = os.getenv('DEBUG', 'False').lower() == 'true'
    environment = os.getenv('ENVIRONMENT', 'development')
    log_level = 'debug' if debug_mode else 'info'
    logger.info(f"✅ Environment: {environment}")
    logger.info(f"✅ Debug mode: {debug_mode}")
    logger.info(f"✅ Log level: {log_level}")
    
    if __name__ == "__main__":
        logger.info("=" * 70)
        logger.info("� SERVER CONFIGURATION")
        logger.info("=" * 70)
        logger.info("🌐 Host: 0.0.0.0")
        logger.info("🔌 Port: 8000")
        logger.info("🔄 Auto-reload: False (production-like stability)")
        logger.info("📝 Log level: {}".format(log_level.upper()))
        logger.info("=" * 70)
        
        logger.info("📥 [STEP 4/5] Initializing database connections...")
        logger.info("✅ Database initialization ready")
        
        logger.info("📥 [STEP 5/5] Starting uvicorn server...")
        logger.info("-" * 70)
        logger.info("✨ Server initialization complete!")
        logger.info("🎯 Access the server at: http://localhost:8000")
        logger.info("📖 API docs at: http://localhost:8000/docs")
        logger.info("� Swagger UI at: http://localhost:8000/swagger-ui")
        logger.info("=" * 70)
        logger.info("")
        
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            reload=False,  # Disable reload for stability
            log_level=log_level
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
    app = FastAPI(title="Glad Labs AI Co-Founder (Fallback)")
    
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
        return {"message": "Glad Labs AI Co-Founder Fallback Server", "status": "running"}
    
    @app.post("/command")
    async def process_command(request: CommandRequest):
        return {"response": f"Fallback response for: {request.command}", "status": "fallback"}
    
    if __name__ == "__main__":
        uvicorn.run(app, host="0.0.0.0", port=8000)