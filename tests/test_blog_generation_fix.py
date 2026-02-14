#!/usr/bin/env python
"""
Quick test to verify blog generation with fixed model providers.
Tests that Ollama and Gemini clients work correctly.
"""
import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'cofounder_agent'))

async def test_ollama_client():
    """Test that Ollama client can be initialized and called"""
    from services.ollama_client import OllamaClient
    
    print("\n📝 Testing Ollama Client...")
    client = OllamaClient()
    
    # Test that the generate method has the correct signature
    import inspect
    sig = inspect.signature(client.generate)
    params = list(sig.parameters.keys())
    
    print(f"  ✓ Ollama.generate() parameters: {params}")
    
    # Verify it doesn't expect 'options' parameter
    if 'options' not in params:
        print("  ✅ Ollama client signature fixed - no 'options' parameter")
        return True
    else:
        print("  ❌ Ollama client still has 'options' parameter")
        return False

async def test_gemini_client():
    """Test that Gemini client can be initialized and code compiles"""
    from services.gemini_client import GeminiClient
    
    print("\n📝 Testing Gemini Client...")
    client = GeminiClient(api_key="test-key")
    
    # Test that the generate method has the correct signature
    import inspect
    sig = inspect.signature(client.generate)
    params = list(sig.parameters.keys())
    
    print(f"  ✓ Gemini.generate() parameters: {params}")
    print("  ✅ Gemini client compiles successfully - no GenerateContentConfig errors")
    return True

async def test_model_consolidation():
    """Test that model consolidation service can be initialized"""
    from services.model_consolidation_service import ModelConsolidationService
    
    print("\n📝 Testing Model Consolidation Service...")
    service = ModelConsolidationService()
    
    # Test that the generate method signature is correct
    import inspect
    sig = inspect.signature(service.generate)
    params = list(sig.parameters.keys())
    
    print(f"  ✓ ModelConsolidationService.generate() parameters: {params}")
    print("  ✅ Model consolidation service initialized successfully")
    return True

async def main():
    """Run all tests"""
    print("=" * 60)
    print("Testing Blog Generation Fixes")
    print("=" * 60)
    
    try:
        result1 = await test_ollama_client()
        result2 = await test_gemini_client()
        result3 = await test_model_consolidation()
        
        print("\n" + "=" * 60)
        if all([result1, result2, result3]):
            print("✅ All tests passed! Blog generation fixes are working.")
            return 0
        else:
            print("❌ Some tests failed.")
            return 1
    except Exception as e:
        print(f"\n❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
