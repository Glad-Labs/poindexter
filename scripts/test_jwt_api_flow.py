#!/usr/bin/env python3
"""
End-to-End JWT Authentication Flow Test

Tests the complete authentication flow by making actual API calls to the backend:
1. Create a valid JWT token
2. Test the /api/auth/me endpoint with token
3. Test protected endpoints requiring authentication
"""

import os
import sys
import json
import jwt
import httpx
from datetime import datetime, timedelta, timezone

# Add backend to path
backend_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'cofounder_agent')
sys.path.insert(0, backend_path)

from services.token_validator import AuthConfig, JWTTokenValidator, TokenType

# API Configuration
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:8000')

def create_test_token():
    """Create a valid JWT token for testing"""
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=24)
    
    payload = {
        "sub": "test-user",
        "user_id": "test-user-123",
        "email": "test@example.com",
        "username": "testuser",
        "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
        "name": "Test User",
        "auth_provider": "mock",
        "type": "access",
        "exp": expiry,
        "iat": now,
    }
    
    token = jwt.encode(payload, AuthConfig.SECRET_KEY, algorithm=AuthConfig.ALGORITHM)
    return token

def test_health_check():
    """Test /health endpoint (no auth required)"""
    print("\n" + "="*80)
    print("TEST 1: Health Check (No Auth Required)")
    print("="*80)
    
    try:
        response = httpx.get(f"{API_BASE_URL}/health", timeout=5.0)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 200:
            print("✓ Health check passed")
            return True
        else:
            print(f"✗ Unexpected status code: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Failed to connect to API: {e}")
        return False

def test_auth_me_with_token(token):
    """Test /api/auth/me endpoint with valid token"""
    print("\n" + "="*80)
    print("TEST 2: /api/auth/me with Valid Token")
    print("="*80)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = httpx.get(f"{API_BASE_URL}/api/auth/me", headers=headers, timeout=5.0)
        print(f"Status: {response.status_code}")
        print(f"Headers sent: Authorization: Bearer {token[:30]}...")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response:")
            print(json.dumps(data, indent=2))
            print("✓ Authentication successful")
            return True
        else:
            print(f"✗ Authentication failed: {response.status_code}")
            try:
                print(f"Error: {response.json()}")
            except:
                print(f"Error: {response.text}")
            return False
    except Exception as e:
        print(f"✗ Failed to call /api/auth/me: {e}")
        return False

def test_auth_me_without_token():
    """Test /api/auth/me endpoint without token"""
    print("\n" + "="*80)
    print("TEST 3: /api/auth/me without Token (Should Fail)")
    print("="*80)
    
    try:
        response = httpx.get(f"{API_BASE_URL}/api/auth/me", timeout=5.0)
        print(f"Status: {response.status_code}")
        
        if response.status_code == 401:
            print(f"✓ Correctly rejected unauthenticated request")
            try:
                print(f"Error: {response.json()}")
            except:
                print(f"Error: {response.text}")
            return True
        else:
            print(f"✗ Unexpected status code (expected 401): {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Failed to call /api/auth/me: {e}")
        return False

def test_auth_me_with_invalid_token():
    """Test /api/auth/me endpoint with invalid token"""
    print("\n" + "="*80)
    print("TEST 4: /api/auth/me with Invalid Token (Should Fail)")
    print("="*80)
    
    headers = {
        "Authorization": "Bearer invalid.token.here",
        "Content-Type": "application/json"
    }
    
    try:
        response = httpx.get(f"{API_BASE_URL}/api/auth/me", headers=headers, timeout=5.0)
        print(f"Status: {response.status_code}")
        
        if response.status_code == 401:
            print(f"✓ Correctly rejected invalid token")
            try:
                print(f"Error: {response.json()}")
            except:
                print(f"Error: {response.text}")
            return True
        else:
            print(f"✗ Unexpected status code (expected 401): {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Failed to call /api/auth/me: {e}")
        return False

def test_protected_endpoint(token):
    """Test a protected endpoint (e.g., /api/tasks)"""
    print("\n" + "="*80)
    print("TEST 5: Protected Endpoint /api/tasks with Valid Token")
    print("="*80)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = httpx.get(f"{API_BASE_URL}/api/tasks", headers=headers, timeout=5.0)
        print(f"Status: {response.status_code}")
        
        if response.status_code in [200, 400]:  # 400 might be ok if validation fails
            print("✓ Request was accepted (auth passed)")
            try:
                print(f"Response: {response.json()}")
            except:
                print(f"Response: {response.text[:200]}...")
            return True
        elif response.status_code == 401:
            print(f"✗ Authentication failed: {response.text}")
            return False
        else:
            print(f"Status: {response.status_code}")
            try:
                print(f"Response: {response.json()}")
            except:
                print(f"Response: {response.text[:200]}...")
            return True  # Request reached backend but might have different validation
    except Exception as e:
        print(f"✗ Failed to call /api/tasks: {e}")
        return False

def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("END-TO-END JWT AUTHENTICATION FLOW TEST")
    print("="*80)
    
    print(f"\nAPI Configuration:")
    print(f"  Base URL: {API_BASE_URL}")
    print(f"  JWT Secret (first 30 chars): {AuthConfig.SECRET_KEY[:30]}...")
    
    # Run tests
    results = {}
    
    results['health'] = test_health_check()
    
    token = create_test_token()
    print(f"\n✓ Created test token: {token[:50]}...")
    
    results['auth_me_valid'] = test_auth_me_with_token(token)
    results['auth_me_invalid'] = test_auth_me_without_token()
    results['auth_me_bad_token'] = test_auth_me_with_invalid_token()
    results['protected'] = test_protected_endpoint(token)
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    for test_name, result in results.items():
        status = "✓" if result else "✗"
        print(f"{status} {test_name}: {'PASSED' if result else 'FAILED'}")
    
    all_passed = all(results.values())
    if all_passed:
        print("\n✓ All authentication tests passed!")
    else:
        print(f"\n✗ Some tests failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
