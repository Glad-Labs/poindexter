#!/usr/bin/env python
"""
Quick startup script for Co-Founder Agent backend with task executor
"""
import sys
import os

# Add to path
sys.path.insert(0, os.path.dirname(__file__))

import uvicorn

if __name__ == "__main__":
    print("[+] Starting Glad Labs Co-Founder Agent backend...")
    print("    Port: 8001 (task executor: ENABLED)")
    
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8001,
        reload=False,
        log_level="info",
    )
