#!/usr/bin/env python3
"""
JWT Token Validation Test Script

Tests the complete JWT authentication flow:
1. Creates a valid JWT token matching backend expectations
2. Tests token validation endpoints
3. Verifies token claims extraction
"""

import os
import sys
import json
import jwt
from datetime import datetime, timedelta, timezone

# Add backend to path
backend_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'cofounder_agent')
sys.path.insert(0, backend_path)

from services.token_validator import AuthConfig, JWTTokenValidator, TokenType

def test_token_creation():
    """Test creating a valid JWT token"""
    print("\n" + "="*80)
    print("TEST 1: JWT Token Creation")
    print("="*80)
    
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
    print(f"✓ Created JWT token successfully")
    print(f"  Token (first 50 chars): {token[:50]}...")
    print(f"  Secret Key: {AuthConfig.SECRET_KEY[:30]}...")
    print(f"  Algorithm: {AuthConfig.ALGORITHM}")
    
    return token

def test_token_validation(token):
    """Test validating a JWT token"""
    print("\n" + "="*80)
    print("TEST 2: JWT Token Validation")
    print("="*80)
    
    try:
        claims = JWTTokenValidator.verify_token(token, TokenType.ACCESS)
        print(f"✓ Token validation succeeded!")
        print(f"  Claims extracted:")
        for key, value in claims.items():
            if key not in ["exp", "iat"]:
                print(f"    - {key}: {value}")
            else:
                print(f"    - {key}: {datetime.fromtimestamp(value, tz=timezone.utc)}")
        
        return claims
    except jwt.ExpiredSignatureError as e:
        print(f"✗ Token expired: {e}")
        return None
    except jwt.InvalidTokenError as e:
        print(f"✗ Invalid token: {e}")
        return None
    except Exception as e:
        print(f"✗ Unexpected error: {type(e).__name__}: {e}")
        return None

def test_token_structure(token):
    """Verify token structure"""
    print("\n" + "="*80)
    print("TEST 3: JWT Token Structure")
    print("="*80)
    
    parts = token.split(".")
    if len(parts) != 3:
        print(f"✗ Invalid token structure: expected 3 parts, got {len(parts)}")
        return False
    
    print(f"✓ Token has 3 parts (header.payload.signature)")
    
    # Decode and display parts
    import base64
    
    try:
        # Add padding if needed
        header_padded = parts[0] + "=" * (4 - len(parts[0]) % 4)
        payload_padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        
        header = json.loads(base64.urlsafe_b64decode(header_padded))
        payload = json.loads(base64.urlsafe_b64decode(payload_padded))
        
        print(f"\n  Header: {json.dumps(header, indent=4)}")
        print(f"\n  Payload: {json.dumps({k: v for k, v in payload.items() if k not in ['exp', 'iat']}, indent=4)}")
        print(f"\n  Signature: {parts[2][:30]}...")
        
        return True
    except Exception as e:
        print(f"✗ Failed to decode token parts: {e}")
        return False

def test_mock_auth_disabled():
    """Test that mock auth is disabled by default"""
    print("\n" + "="*80)
    print("TEST 4: Mock Auth Configuration")
    print("="*80)
    
    disable_auth = os.getenv("DISABLE_AUTH_FOR_DEV", "false")
    print(f"  DISABLE_AUTH_FOR_DEV={disable_auth}")
    
    if disable_auth.lower() == "true":
        print(f"⚠ WARNING: Mock auth is enabled! All requests will be authenticated without validation!")
    else:
        print(f"✓ Mock auth is disabled (secure)")
    
    return disable_auth.lower() != "true"

def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("JWT AUTHENTICATION VALIDATION TEST SUITE")
    print("="*80)
    
    # Test configuration
    print(f"\nBackend Configuration:")
    print(f"  JWT_SECRET: {AuthConfig.SECRET_KEY[:30]}...")
    print(f"  Algorithm: {AuthConfig.ALGORITHM}")
    print(f"  Token Expiry: {AuthConfig.ACCESS_TOKEN_EXPIRE_MINUTES} minutes")
    
    # Run tests
    test_mock_auth_disabled()
    token = test_token_creation()
    claims = test_token_validation(token)
    test_token_structure(token)
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    if claims:
        print("✓ All JWT validation tests passed!")
        print(f"\nToken is valid for:")
        print(f"  - User ID: {claims.get('user_id')}")
        print(f"  - Email: {claims.get('email')}")
        print(f"  - Auth Provider: {claims.get('auth_provider')}")
        print(f"  - Token Type: {claims.get('type')}")
    else:
        print("✗ JWT validation failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
