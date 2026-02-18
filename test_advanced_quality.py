#!/usr/bin/env python3
"""
Advanced Quality Assessment Tests
Focus on: Output quality, hallucination detection, model routing, error handling
"""

import asyncio
import json
import time
from datetime import datetime
import httpx

BACKEND_URL = "http://localhost:8000"

class QualityTest:
    def __init__(self):
        self.results = []
        self.failures = []
        
    async def test_chat_accuracy(self):
        """Test if AI provides accurate responses about system"""
        print("\n🧪 TEST: Chat Output Accuracy & Hallucination Detection")
        print("-" * 70)
        
        test_queries = [
            {
                "query": "What programming languages is Glad Labs built with?",
                "keywords": ["python", "javascript", "typescript", "react", "fastapi"],
                "description": "System Architecture Question"
            },
            {
                "query": "What are the main agent types in this system?",
                "keywords": ["content", "financial", "market", "compliance", "agent"],
                "description": "Agent Types Question"
            },
            {
                "query": "How many LLM providers are supported?",
                "keywords": ["4", "five", "provider", "openai", "anthropic", "google", "ollama"],
                "description": "Provider Count Question"
            }
        ]
        
        async with httpx.AsyncClient(timeout=10) as client:
            for i, test_case in enumerate(test_queries, 1):
                try:
                    print(f"\n  Test {i}: {test_case['description']}")
                    print(f"  Query: {test_case['query']}")
                    
                    response = await client.post(
                        f"{BACKEND_URL}/api/chat",
                        json={"message": test_case['query']},
                        timeout=10
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        answer = data.get("response", "")
                        
                        # Check for keyword presence
                        found_keywords = [kw for kw in test_case['keywords'] 
                                        if kw.lower() in answer.lower()]
                        
                        accuracy_score = len(found_keywords) / len(test_case['keywords']) * 100
                        
                        print(f"  Response Length: {len(answer)} chars")
                        print(f"  Keywords Found: {len(found_keywords)}/{len(test_case['keywords'])} ({accuracy_score:.0f}%)")
                        
                        if accuracy_score < 50:
                            print(f"  ❌ HALLUCINATION DETECTED: Low keyword match")
                            self.failures.append({
                                "test": test_case['description'],
                                "issue": "Low accuracy score",
                                "score": accuracy_score
                            })
                        elif accuracy_score > 70:
                            print(f"  ✅ ACCURATE RESPONSE")
                        else:
                            print(f"  ⚠️  PARTIALLY ACCURATE")
                        
                        # Show snippet
                        print(f"  Response Preview: {answer[:150]}...")
                        
                    else:
                        print(f"  ⚠️  API Error: {response.status_code}")
                        
                except Exception as e:
                    print(f"  ❌ Error: {str(e)}")
                    self.failures.append({
                        "test": test_case['description'],
                        "issue": str(e)
                    })
                
                await asyncio.sleep(1)  # Rate limit
        
        print("\n" + "="*70)
        print(f"Accuracy Tests Completed. Hallucinations Detected: {len(self.failures)}")
        return len(self.failures) == 0
    
    async def test_api_error_handling(self):
        """Test how API handles invalid inputs"""
        print("\n🧪 TEST: API Error Handling & Validation")
        print("-" * 70)
        
        test_cases = [
            {
                "name": "Invalid JSON",
                "method": "POST",
                "endpoint": "/api/chat",
                "data": "invalid json",
                "expect_error": True
            },
            {
                "name": "Missing required field",
                "method": "POST",
                "endpoint": "/api/chat",
                "data": json.dumps({"not_message": "test"}),
                "expect_error": True
            },
            {
                "name": "Empty message",
                "method": "POST",
                "endpoint": "/api/chat",
                "data": json.dumps({"message": ""}),
                "expect_error": False  # May be valid
            }
        ]
        
        async with httpx.AsyncClient(timeout=10) as client:
            for i, test_case in enumerate(test_cases, 1):
                try:
                    print(f"\n  Test {i}: {test_case['name']}")
                    
                    response = await client.post(
                        f"{BACKEND_URL}{test_case['endpoint']}",
                        content=test_case['data'],
                        headers={"Content-Type": "application/json"},
                        timeout=5
                    )
                    
                    print(f"  Status Code: {response.status_code}")
                    
                    if test_case['expect_error'] and response.status_code < 400:
                        print(f"  ⚠️  Should have returned error, got {response.status_code}")
                        self.failures.append({
                            "test": test_case['name'],
                            "issue": f"Expected error, got {response.status_code}"
                        })
                    elif not test_case['expect_error'] and response.status_code >= 400:
                        print(f"  ⚠️  Unexpected error: {response.status_code}")
                    else:
                        print(f"  ✅ Correct error handling")
                    
                    if response.status_code >= 400:
                        print(f"  Error: {response.text[:100]}")
                        
                except Exception as e:
                    print(f"  ❌ Exception: {str(e)}")
                    self.failures.append({
                        "test": test_case['name'],
                        "issue": str(e)
                    })
                
                await asyncio.sleep(0.5)
        
        print("\n" + "="*70)

async def run_quality_tests():
    """Execute advanced quality tests"""
    tester = QualityTest()
    
    print("\n" + "="*70)
    print("🔬 ADVANCED SYSTEM QUALITY ASSESSMENT")
    print("="*70)
    
    # Run tests
    await tester.test_chat_accuracy()
    await tester.test_api_error_handling()
    
    # Final summary
    print("\n" + "="*70)
    print("📋 QUALITY ASSESSMENT SUMMARY")
    print("="*70)
    
    if tester.failures:
        print(f"\n⚠️  ISSUES DETECTED ({len(tester.failures)}):")
        for i, failure in enumerate(tester.failures, 1):
            print(f"  {i}. [{failure.get('test', 'Unknown')}] {failure.get('issue', 'Unknown issue')}")
    else:
        print("\n✅ No critical issues detected in advanced tests")
    
    print("\n" + "="*70 + "\n")

if __name__ == "__main__":
    asyncio.run(run_quality_tests())
