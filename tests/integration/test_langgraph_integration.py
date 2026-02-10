#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Complete LangGraph Backend & WebSocket Integration Test"""

import asyncio
import json
import websockets
import subprocess
import sys
import time
import pytest

@pytest.fixture(scope="function")
async def request_id():
    """Fixture to get request_id from HTTP endpoint"""
    import httpx
    
    url = "http://localhost:8000/api/content/langgraph/blog-posts"
    payload = {
        "topic": "Python Testing Best Practices",
        "keywords": ["testing", "python", "pytest"],
        "audience": "developers",
        "tone": "technical",
        "word_count": 1500
    }
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            data = response.json()
            return data.get("request_id")
    except Exception:
        return None


@pytest.mark.asyncio
async def test_http_endpoint():
    """Test HTTP POST endpoint"""
    import httpx
    
    print("\n" + "="*70)
    print("TEST 1: HTTP POST Endpoint")
    print("="*70)
    
    url = "http://localhost:8000/api/content/langgraph/blog-posts"
    payload = {
        "topic": "Python Testing Best Practices",
        "keywords": ["testing", "python", "pytest"],
        "audience": "developers",
        "tone": "technical",
        "word_count": 1500
    }
    
    print(f"POST {url}")
    print(f"Body: {json.dumps(payload, indent=2)}")
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            
            print(f"\n✅ Status: {response.status_code}")
            data = response.json()
            print(f"Response:")
            print(json.dumps(data, indent=2))
            
            # Assert successful response
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert "request_id" in data, "Missing request_id in response"
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise


@pytest.mark.asyncio
async def test_websocket_endpoint(request_id):
    """Test WebSocket endpoint"""
    print("\n" + "="*70)
    print("TEST 2: WebSocket Endpoint")
    print("="*70)
    
    if not request_id:
        print("⚠️  Skipping WebSocket test - no request_id from HTTP test")
        pytest.skip("request_id not available")
    
    uri = f"ws://localhost:8000/api/content/langgraph/ws/blog-posts/{request_id}"
    print(f"Connecting to: {uri}\n")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("✅ WebSocket connected!")
            
            messages = []
            phases = []
            
            try:
                while True:
                    message = await asyncio.wait_for(websocket.recv(), timeout=15)
                    data = json.loads(message)
                    messages.append(data)
                    
                    msg_type = data.get("type")
                    if msg_type == "progress":
                        node = data.get("node")
                        progress = data.get("progress")
                        phases.append({"node": node, "progress": progress})
                        print(f"  📊 {node:12} {progress:3}% {'█' * (progress//10)} {'░' * (10 - progress//10)}")
                    elif msg_type == "complete":
                        print(f"  ✅ complete: {data.get('status')}")
                        break
                    elif msg_type == "error":
                        print(f"  ❌ error: {data.get('error')}")
                        break
                        
            except asyncio.TimeoutError:
                print("⏱️  WebSocket timeout")
            
            print(f"\n📈 Summary:")
            print(f"  Total messages: {len(messages)}")
            print(f"  Phases: {len(phases)}")
            for phase in phases:
                print(f"    - {phase['node']}: {phase['progress']}%")
                
    except Exception as e:
        print(f"❌ WebSocket Error: {str(e)}")
        raise


# Note: When running with pytest, use:
#   pytest tests/integration/test_langgraph_integration.py -v -s
# The -s flag shows print output
