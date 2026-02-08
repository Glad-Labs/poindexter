#!/usr/bin/env python3
"""
Debug JWT Token Issue - Check Exact Error

Tests to find the exact error in token validation
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

def test_token_with_datetime_objects():
    """Create token with datetime objects (WRONG)"""
    print("\n" + "="*80)
    print("TEST 1: Token with datetime objects")
    print("="*80)
    
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=24)
    
    payload = {
        "sub": "test-user",
        "user_id": "test-user-123",
        "email": "test@example.com",
        "username": "testuser",
        "auth_provider": "mock",
        "type": "access",
        "exp": expiry,
        "iat": now,
    }
    
    try:
        token = jwt.encode(payload, AuthConfig.SECRET_KEY, algorithm=AuthConfig.ALGORITHM)
        print(f"✓ Token created (might have issues)")
        print(f"  Token: {token[:50]}...")
        
        # Try to validate
        try:
            claims = JWTTokenValidator.verify_token(token)
            print(f"✓ Token validation passed!")
            return True
        except Exception as e:
            print(f"✗ Token validation failed: {e}")
            return False
    except Exception as e:
        print(f"✗ Token creation failed: {e}")
        return False

def test_token_with_timestamps():
    """Create token with Unix timestamps (CORRECT)"""
    print("\n" + "="*80)
    print("TEST 2: Token with Unix timestamps")
    print("="*80)
    
    now = datetime.now(timezone.utc)
    now_timestamp = now.timestamp()
    expiry_timestamp = (now + timedelta(hours=24)).timestamp()
    
    payload = {
        "sub": "test-user",
        "user_id": "test-user-123",
        "email": "test@example.com",
        "username": "testuser",
        "auth_provider": "mock",
        "type": "access",
        "exp": expiry_timestamp,
        "iat": now_timestamp,
    }
    
    try:
        token = jwt.encode(payload, AuthConfig.SECRET_KEY, algorithm=AuthConfig.ALGORITHM)
        print(f"✓ Token created")
        print(f"  Token: {token[:50]}...")
        
        # Try to validate
        try:
            claims = JWTTokenValidator.verify_token(token)
            print(f"✓ Token validation passed!")
            print(f"  Claims: {json.dumps({k: v for k, v in claims.items() if k not in ['exp', 'iat']}, indent=2)}")
            return True
        except Exception as e:
            print(f"✗ Token validation failed: {e}")
            return False
    except Exception as e:
        print(f"✗ Token creation failed: {e}")
        return False

def test_decode_datetime_token():
    """Decode token with datetime objects to see what happens"""
    print("\n" + "="*80)
    print("TEST 3: Decode datetime token payload")
    print("="*80)
    
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=24)
    
    payload = {
        "sub": "test-user",
        "user_id": "test-user-123",
        "email": "test@example.com",
        "username": "testuser",
        "auth_provider": "mock",
        "type": "access",
        "exp": expiry,
        "iat": now,
    }
    
    print(f"Original payload:")
    print(f"  exp type: {type(payload['exp'])}, value: {payload['exp']}")
    print(f"  iat type: {type(payload['iat'])}, value: {payload['iat']}")
    
    try:
        token = jwt.encode(payload, AuthConfig.SECRET_KEY, algorithm=AuthConfig.ALGORITHM)
        decoded = jwt.decode(token, AuthConfig.SECRET_KEY, algorithms=[AuthConfig.ALGORITHM])
        
        print(f"\nDecoded payload:")
        print(f"  exp type: {type(decoded['exp'])}, value: {decoded['exp']}")
        print(f"  iat type: {type(decoded['iat'])}, value: {decoded['iat']}")
        
        print(f"\nNote: JWT libraries convert datetime to string automatically")
        
    except Exception as e:
        print(f"Error: {e}")

def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("JWT TOKEN FORMAT DEBUG TEST")
    print("="*80)
    
    test_token_with_datetime_objects()
    test_token_with_timestamps()
    test_decode_datetime_token()

if __name__ == "__main__":
    main()
