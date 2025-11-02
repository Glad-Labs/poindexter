#!/usr/bin/env python
"""Test the chat endpoint"""
import time
import httpx
import json

print("Waiting for backend to be ready...")
time.sleep(3)

try:
    print("Making chat request...")
    response = httpx.post(
        'http://localhost:8000/api/chat',
        json={
            'message': 'What is 2+2?',
            'model': 'ollama',
            'conversationId': 'test123'
        },
        timeout=120
    )
    
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text[:500]}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✓ Success!")
        print(f"  Response: {data.get('response')}")
        print(f"  Model: {data.get('model')}")
        print(f"  Tokens: {data.get('tokens_used')}")
    else:
        print(f"\n✗ Error: {response.status_code}")
        print(response.text)
        
except Exception as e:
    print(f"✗ Exception: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
