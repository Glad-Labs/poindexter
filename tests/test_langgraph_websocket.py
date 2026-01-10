#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test LangGraph WebSocket endpoint"""

import asyncio
import json
import websockets
import sys
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src/cofounder_agent'))

async def test_websocket():
    """Test WebSocket connection to LangGraph blog progress endpoint"""
    request_id = "test-request-123"
    uri = f"ws://localhost:8000/api/content/langgraph/ws/blog-posts/{request_id}"
    
    logger.info(f"Connecting to WebSocket: {uri}")
    
    try:
        async with websockets.connect(uri) as websocket:
            logger.info("✅ WebSocket connected!")
            
            # Receive messages from the server
            messages = []
            try:
                while True:
                    message = await asyncio.wait_for(websocket.recv(), timeout=10)
                    data = json.loads(message)
                    messages.append(data)
                    logger.info(f"Message: {json.dumps(data, indent=2)}")
                    
                    # Stop if we get complete message
                    if data.get("type") == "complete":
                        break
            except asyncio.TimeoutError:
                logger.info("⏱️  WebSocket timeout (expected if server not streaming)")
            
            logger.info(f"\n📊 Received {len(messages)} messages:")
            for i, msg in enumerate(messages, 1):
                logger.info(f"  {i}. {msg.get('type')}: {msg.get('node', msg.get('error', 'N/A'))}")
                
    except Exception as e:
        logger.error(f"❌ Error: {str(e)}")

# Run the test
if __name__ == "__main__":
    asyncio.run(test_websocket())
