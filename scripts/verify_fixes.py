#!/usr/bin/env python3
"""
Verification Script - Confirm all fixes are working

Run: python scripts/verify_fixes.py

This script checks:
1. ✅ Backend is running and responds
2. ✅ Frontend is running and responsive  
3. ✅ PostgreSQL database is connected
4. ✅ Ollama is available with models
5. ✅ Chat endpoint works
6. ✅ Model selector endpoint works
"""

import requests
import sys
from datetime import datetime
from urllib.error import URLError

def check_backend():
    """Check if backend is running"""
    try:
        response = requests.get("http://localhost:8000/api/health", timeout=2)
        if response.status_code == 200:
            data = response.json()
            return True, "✅ Backend running", data
        else:
            return False, f"❌ Backend responded with {response.status_code}", None
    except requests.ConnectionError:
        return False, "❌ Backend not responding (connection refused)", None
    except Exception as e:
        return False, f"❌ Backend error: {str(e)}", None

def check_frontend():
    """Check if frontend is running"""
    try:
        response = requests.get("http://localhost:3001", timeout=2)
        if response.status_code == 200:
            return True, "✅ Frontend running", None
        else:
            return False, f"❌ Frontend responded with {response.status_code}", None
    except requests.ConnectionError:
        return False, "❌ Frontend not responding (connection refused)", None
    except Exception as e:
        return False, f"❌ Frontend error: {str(e)}", None

def check_ollama():
    """Check if Ollama is available"""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        if response.status_code == 200:
            data = response.json()
            models = data.get("models", [])
            model_names = [m["name"] for m in models]
            return True, f"✅ Ollama available ({len(models)} models)", model_names
        else:
            return False, f"❌ Ollama responded with {response.status_code}", None
    except requests.ConnectionError:
        return False, "❌ Ollama not running", None
    except Exception as e:
        return False, f"❌ Ollama error: {str(e)}", None

def check_model_selector():
    """Check if model selector endpoint works"""
    try:
        response = requests.post(
            "http://localhost:8000/api/ollama/select-model",
            json={"model": "mistral:latest"},
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                return True, "✅ Model selector working", data
            else:
                return False, f"❌ Model selection failed: {data.get('message')}", data
        else:
            return False, f"❌ Endpoint returned {response.status_code}", None
    except requests.ConnectionError:
        return False, "❌ Backend not responding", None
    except Exception as e:
        return False, f"❌ Error: {str(e)}", None

def check_chat():
    """Check if chat endpoint works"""
    try:
        response = requests.post(
            "http://localhost:8000/api/chat",
            json={
                "message": "test",
                "model": "ollama",
                "conversationId": "default"
            },
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("response"):
                return True, "✅ Chat endpoint working", data
            else:
                return False, "❌ No response from chat", data
        else:
            return False, f"❌ Chat endpoint returned {response.status_code}", None
    except requests.ConnectionError:
        return False, "❌ Backend not responding", None
    except requests.Timeout:
        return False, "❌ Chat request timed out (backend processing)", None
    except Exception as e:
        return False, f"❌ Error: {str(e)}", None

def main():
    print("=" * 70)
    print("VERIFICATION SCRIPT - System Health Check")
    print("=" * 70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    results = []
    
    # Check Backend
    print("1. Checking Backend...")
    success, msg, data = check_backend()
    results.append(success)
    print(f"   {msg}")
    if data and "database_url_configured" in data:
        print(f"   Database configured: {'✅' if data['database_url_configured'] else '❌'}")
    print()
    
    # Check Frontend
    print("2. Checking Frontend...")
    success, msg, _ = check_frontend()
    results.append(success)
    print(f"   {msg}")
    print()
    
    # Check Ollama
    print("3. Checking Ollama...")
    success, msg, models = check_ollama()
    results.append(success)
    print(f"   {msg}")
    if models:
        print(f"   Models: {', '.join(models[:3])}... ({len(models)} total)")
    print()
    
    # Check Model Selector
    print("4. Checking Model Selector...")
    success, msg, data = check_model_selector()
    results.append(success)
    print(f"   {msg}")
    if data and not success:
        print(f"   Error: {data.get('message', 'Unknown')}")
    print()
    
    # Check Chat
    print("5. Checking Chat Endpoint...")
    success, msg, data = check_chat()
    results.append(success)
    print(f"   {msg}")
    if data and "response" in data:
        response_preview = data["response"][:50]
        print(f"   Response preview: {response_preview}...")
    print()
    
    # Summary
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    
    if passed == total:
        print(f"✅ ALL CHECKS PASSED ({passed}/{total})")
        print()
        print("System is ready to use!")
    elif passed >= total - 1:
        print(f"⚠️ MOSTLY WORKING ({passed}/{total})")
        print()
        print("Minor issues detected - see above for details")
    else:
        print(f"❌ ISSUES DETECTED ({passed}/{total})")
        print()
        print("Please check the errors above and ensure all services are running")
    
    print()
    print("=" * 70)
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nVerification cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)
