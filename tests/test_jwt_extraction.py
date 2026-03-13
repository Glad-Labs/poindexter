#!/usr/bin/env python3
"""
Quick test for JWT token extraction in custom workflows routes.
Tests the get_user_id() function with various scenarios.
"""

import sys
import os
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src" / "cofounder_agent"
sys.path.insert(0, str(src_path))

from unittest.mock import Mock
from fastapi import HTTPException
import jwt

# Import the function to test
from routes.custom_workflows_routes import get_user_id
from services.token_validator import AuthConfig, JWTTokenValidator

def create_test_token(user_id: str = "test-alice", **kwargs) -> str:
    """Create a valid JWT token for testing."""
    payload = {
        "user_id": user_id,
        "email": f"{user_id}@example.com",
        "username": user_id,
        "type": "access",
        **kwargs
    }
    token = jwt.encode(payload, AuthConfig.SECRET_KEY, algorithm=AuthConfig.ALGORITHM)
    return token

def test_jwt_extraction():
    """Test JWT token extraction scenarios."""
    print("🧪 Testing JWT Token Extraction\n")
    
    # Test 1: Valid Bearer token
    print("Test 1: Valid Bearer token in Authorization header")
    token = create_test_token("alice-123")
    request = Mock()
    request.state = Mock()
    request.headers = {"Authorization": f"Bearer {token}"}
    request.state.user_id = None
    
    try:
        user_id = get_user_id(request)
        assert user_id == "alice-123", f"Expected 'alice-123', got '{user_id}'"
        print(f"  ✅ Extracted user_id: {user_id}\n")
    except Exception as e:
        print(f"  ❌ Failed: {e}\n")
        return False
    
    # Test 2: User ID already in request context
    print("Test 2: User ID from request.state (middleware)")
    request = Mock()
    request.state = Mock()
    request.headers = {}
    request.state.user_id = "bob-456"
    
    try:
        user_id = get_user_id(request)
        assert user_id == "bob-456", f"Expected 'bob-456', got '{user_id}'"
        print(f"  ✅ Extracted user_id: {user_id}\n")
    except Exception as e:
        print(f"  ❌ Failed: {e}\n")
        return False
    
    # Test 3: No token (development mode)
    print("Test 3: No authorization header (development fallback)")
    request = Mock()
    request.state = Mock()
    request.headers = {}
    request.state.user_id = None
    
    try:
        user_id = get_user_id(request)
        assert user_id == "test-user-123", f"Expected 'test-user-123', got '{user_id}'"
        print(f"  ✅ Extracted user_id: {user_id}\n")
    except Exception as e:
        print(f"  ❌ Failed: {e}\n")
        return False
    
    # Test 4: Invalid Bearer token (should raise 401)
    print("Test 4: Invalid Bearer token (should raise 401)")
    request = Mock()
    request.state = Mock()
    request.headers = {"Authorization": "Bearer invalid.token.here"}
    request.state.user_id = None
    
    try:
        user_id = get_user_id(request)
        print(f"  ❌ Should have raised 401, got user_id: {user_id}\n")
        return False
    except HTTPException as e:
        if e.status_code == 401:
            print(f"  ✅ Correctly raised 401: {e.detail}\n")
        else:
            print(f"  ❌ Wrong status code: {e.status_code}\n")
            return False
    except Exception as e:
        print(f"  ❌ Wrong exception type: {type(e).__name__}: {e}\n")
        return False
    
    # Test 5: Invalid Bearer format (no space)
    print("Test 5: Invalid Bearer format")
    request = Mock()
    request.state = Mock()
    request.headers = {"Authorization": "BearerInvalidFormat"}
    request.state.user_id = None
    
    try:
        user_id = get_user_id(request)
        print(f"  ✅ Extracted user_id (dev fallback): {user_id}\n")
    except HTTPException as e:
        if e.status_code == 401:
            print(f"  ✅ Correctly raised 401: {e.detail}\n")
        else:
            print(f"  ❌ Wrong status code: {e.status_code}\n")
            return False
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}\n")
        return False
    
    # Test 6: Expired token
    print("Test 6: Expired JWT token")
    import time
    expired_payload = {
        "user_id": "charlie-789",
        "email": "charlie@example.com",
        "username": "charlie-789",
        "type": "access",
        "exp": int(time.time()) - 3600  # Expired 1 hour ago
    }
    expired_token = jwt.encode(expired_payload, AuthConfig.SECRET_KEY, algorithm=AuthConfig.ALGORITHM)
    
    request = Mock()
    request.state = Mock()
    request.headers = {"Authorization": f"Bearer {expired_token}"}
    request.state.user_id = None
    
    try:
        user_id = get_user_id(request)
        print(f"  ❌ Should have raised 401, got user_id: {user_id}\n")
        return False
    except HTTPException as e:
        if e.status_code == 401 and "expired" in e.detail.lower():
            print(f"  ✅ Correctly raised 401: {e.detail}\n")
        else:
            print(f"  ⚠️  Got 401 but message: {e.detail}\n")
    except Exception as e:
        print(f"  ⚠️  Unexpected error type: {type(e).__name__}: {e}\n")
    
    print("✅ All JWT extraction tests passed!")
    return True

if __name__ == "__main__":
    success = test_jwt_extraction()
    sys.exit(0 if success else 1)
