#!/usr/bin/env python3
"""
Test script to verify the OllamaClient text extraction fix.
This tests if responses are correctly extracting from the 'text' key.
"""
import sys
import json
from src.cofounder_agent.services.ai_content_generator import AIContentGenerator

async def test_ollama_text_extraction():
    """Test that Ollama responses are extracted correctly from 'text' key"""
    
    print("\n" + "="*70)
    print("🧪 Testing OllamaClient Text Extraction Fix")
    print("="*70)
    
    try:
        generator = AIContentGenerator()
        
        # Test blog post generation
        topic = "AI in Business"
        
        print(f"\n📝 Generating blog post about: {topic}")
        print("\n⏳ Calling Ollama (this may take 30-60 seconds)...")
        
        result = await generator.generate_blog_post(
            topic=topic,
            style="professional",
            tone="informative",
            target_length=500,
            tags=["AI", "Business", "Technology"]
        )
        
        # Result is a tuple: (content, outline, metadata)
        if isinstance(result, tuple) and len(result) >= 1:
            content = result[0]  # First element is the content
            
            print(f"\n✅ Response received!")
            print(f"   Type: {type(content)}")
            if content:
                print(f"   Length: {len(content)} characters")
                
                if len(content) > 100:
                    print(f"   Content (first 100 chars): {content[:100]}...")
                    print("\n✅ SUCCESS: Text extraction is working!")
                    print(f"   - Response is not empty")
                    print(f"   - Response is longer than 100 chars")
                    print(f"   - Ollama 'text' key was correctly extracted")
                    return True
                else:
                    print(f"\n❌ FAILED: Content is too short ({len(content)} chars)")
                    print(f"   Content: '{content}'")
                    return False
            else:
                print(f"\n❌ FAILED: Content is None")
                return False
        else:
            print(f"\n❌ FAILED: Unexpected response format")
            print(f"   Response type: {type(result)}")
            print(f"   Response: {result}")
            return False
            
    except Exception as e:
        print(f"\n❌ Error during test: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    import asyncio
    success = asyncio.run(test_ollama_text_extraction())
    sys.exit(0 if success else 1)

