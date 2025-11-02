#!/usr/bin/env python
"""Test script to POST multiple messages to /api/chat endpoint"""
import requests
import json
import time

def test_chat(message, conversation_id="test_session"):
    """Test a single chat message"""
    url = "http://127.0.0.1:8000/api/chat"
    payload = {
        "message": message,
        "model": "ollama",
        "conversationId": conversation_id
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Q: {message}")
            print(f"   A: {data['response']}\n")
            return True
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"   {response.text}\n")
            return False
    except Exception as e:
        print(f"❌ ERROR: {type(e).__name__}: {e}\n")
        return False

# Wait for server
time.sleep(1)

print("[TEST] Testing /api/chat endpoint")
print("=" * 60)

# Test multiple messages in sequence
test_chat("What is the capital of France?")
test_chat("How many continents are there?")
test_chat("What is Python?")

print("=" * 60)
print("[TEST] All tests complete!")
