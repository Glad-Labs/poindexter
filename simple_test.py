#!/usr/bin/env python3
"""Simple test to understand phase input handling"""

import asyncio
import json
import requests

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

async def test():
    # Make the request with ALL the required inputs upfront
    payload = {
        "topic": "The Benefits of Artificial Intelligence in Healthcare",
        "focus": "medical diagnosis, drug discovery, personalized medicine, ethical considerations",
        "prompt": "Create an engaging blog post based on the research findings about AI in healthcare.",
        "target_audience": "healthcare professionals and IT decision-makers",
        "tone": "professional",
        "model": "ollama-mistral"
    }
    
    print("Sending request to /api/workflows/execute/blog_post")
    print(f"Payload keys: {list(payload.keys())}")
    print()
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/workflows/execute/blog_post",
            json=payload,
            headers=HEADERS,
            timeout=60
        )
        
        print(f"Status: {resp.status_code}")
        data = resp.json()
        print(f"Response keys: {list(data.keys())}")
        print(f"Execution ID: {data.get('execution_id')}")
        print(f"Status: {data.get('status')}")
        print(f"Error: {data.get('error_message')}")
        
        if data.get('phase_results'):
            print(f"\nPhase results:")
            for phase_name, result in data['phase_results'].items():
                print(f"  {phase_name}: {result.get('status', 'unknown')}")
                if result.get('error'):
                    print(f"    Error: {result.get('error')[:200]}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
