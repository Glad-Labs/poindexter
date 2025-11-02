#!/usr/bin/env python
"""Test script for chat endpoint"""
import httpx
import json

async def test_chat():
    async with httpx.AsyncClient() as client:
        print("[TEST] Making POST request to /api/chat...")
        try:
            response = await client.post(
                "http://127.0.0.1:8000/api/chat",
                json={
                    "message": "What is 2+2?",
                    "model": "ollama",
                    "conversationId": "test123"
                },
                timeout=10
            )
            print(f"[TEST] Response status: {response.status_code}")
            print(f"[TEST] Response body: {response.text}")
        except Exception as e:
            print(f"[TEST] ERROR: {type(e).__name__}: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_chat())
