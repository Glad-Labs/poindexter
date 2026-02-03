#!/usr/bin/env python3
"""
Test script to verify Google Gemini SDK is working with the new google-genai package
"""

import os
import sys
import io

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def test_sdk_imports():
    """Test that both SDKs can be imported"""
    print("\n" + "="*70)
    print("🧪 Google Generative AI SDK Test")
    print("="*70)
    
    # Test new SDK
    print("\n1️⃣  Testing new google.genai SDK...")
    try:
        import google.genai
        print(f"   ✅ google.genai version: {google.genai.__version__}")
        print(f"   ✅ Package location: {google.genai.__file__}")
    except ImportError as e:
        print(f"   ❌ Failed to import google.genai: {e}")
        return False
    
    # Test old SDK (should show deprecation warning)
    print("\n2️⃣  Testing legacy google.generativeai SDK...")
    try:
        import google.generativeai
        print(f"   ✅ google.generativeai version: {google.generativeai.__version__}")
        print(f"   ⚠️  This SDK is deprecated - use google.genai instead")
    except ImportError as e:
        print(f"   ❌ Failed to import google.generativeai: {e}")
    
    # Test the import pattern used in codebase
    print("\n3️⃣  Testing fallback import pattern (used in codebase)...")
    try:
        try:
            import google.genai as genai
            sdk_used = "google.genai (NEW)"
        except ImportError:
            import google.generativeai as genai
            sdk_used = "google.generativeai (LEGACY)"
        
        print(f"   ✅ Successfully imported: {sdk_used}")
        print(f"   ✅ Available methods: {len(dir(genai))} (GenerativeModel, Client, etc.)")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False
    
    return True


def test_environment_config():
    """Test environment configuration for Gemini"""
    print("\n4️⃣  Checking Gemini API Key configuration...")
    
    gemini_keys = [
        ('GOOGLE_API_KEY', os.getenv('GOOGLE_API_KEY')),
        ('GEMINI_API_KEY', os.getenv('GEMINI_API_KEY')),
    ]
    
    for key_name, key_value in gemini_keys:
        if key_value:
            # Show only first 10 chars for security
            masked = key_value[:10] + "..." if len(key_value) > 10 else "***"
            print(f"   ✅ {key_name}: {masked}")
        else:
            print(f"   ⚠️  {key_name}: Not set")
    
    return bool(os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY'))


def main():
    print("\n" + "="*70)
    print("GOOGLE GENERATIVE AI SDK VERIFICATION")
    print("="*70)
    print("Purpose: Verify google-genai is installed and working")
    print("Expected: google.genai (new) imports successfully")
    print("="*70)
    
    # Run tests
    sdk_test_passed = test_sdk_imports()
    api_key_configured = test_environment_config()
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    if sdk_test_passed:
        print("✅ SDK Import: PASSED")
    else:
        print("❌ SDK Import: FAILED")
    
    if api_key_configured:
        print("✅ API Key: CONFIGURED")
    else:
        print("⚠️  API Key: NOT CONFIGURED (Gemini won't work)")
    
    print("\n" + "="*70)
    print("WHAT'S NEXT:")
    print("="*70)
    print("1. When you select Gemini from the frontend model selector,")
    print("   the backend will now use google.genai (new SDK)")
    print("2. Check backend logs for: '✅ Using google.genai (new SDK v1.61.0+)'")
    print("3. If google.generativeai is used, you'll see:")
    print("   '⚠️  Using google.generativeai (legacy/deprecated SDK)'")
    print("4. Content generation should complete without deprecation warnings")
    print("="*70 + "\n")
    
    return 0 if (sdk_test_passed and api_key_configured) else 1


if __name__ == "__main__":
    sys.exit(main())
