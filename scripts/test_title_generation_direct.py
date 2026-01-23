#!/usr/bin/env python3
"""
Direct test of LLM-based title generation functionality
"""

import asyncio
import sys
import os

# Add src to path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

async def test_title_generation_function():
    """Test the _generate_catchy_title function directly"""
    
    from cofounder_agent.services.content_router_service import _generate_catchy_title
    
    print("🧪 Testing LLM-based Title Generation\n")
    
    # Test cases
    test_cases = [
        {
            "topic": "The Future of Artificial Intelligence in Healthcare",
            "content_excerpt": "Artificial intelligence is revolutionizing healthcare by enabling early disease detection, personalized treatment plans, and drug discovery. Machine learning algorithms can now analyze medical images with greater accuracy than human radiologists..."
        },
        {
            "topic": "How to Learn Python Programming",
            "content_excerpt": "Learning Python has become increasingly popular in recent years. Python's simple syntax and vast library ecosystem make it an ideal language for beginners. Whether you're interested in web development, data science, or automation..."
        },
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"Test {i}:")
        print(f"  Topic: {test_case['topic']}")
        print(f"  Content preview: {test_case['content_excerpt'][:80]}...")
        print()
        
        try:
            print(f"  🔄 Generating title...")
            title = await _generate_catchy_title(
                topic=test_case['topic'],
                content_excerpt=test_case['content_excerpt']
            )
            
            if title:
                print(f"  ✅ Title generated successfully:")
                print(f"     \"{title}\"")
                print(f"     Length: {len(title)} characters\n")
            else:
                print(f"  ⚠️ Title generation returned None (fallback to topic)\n")
        
        except Exception as e:
            print(f"  ❌ Error: {e}\n")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_title_generation_function())
