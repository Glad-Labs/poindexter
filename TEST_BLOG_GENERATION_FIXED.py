#!/usr/bin/env python
"""
Test blog post generation after fixing Ollama response extraction
"""

import asyncio
import sys
import os
import logging

# Add the src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Set logging to INFO to see detailed output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

from cofounder_agent.services.ai_content_generator import AIContentGenerator

async def test_blog_generation():
    """Test blog post generation"""
    
    print("\n" + "="*80)
    print("[TEST] BLOG GENERATION WITH FIXED OLLAMA RESPONSE EXTRACTION")
    print("="*80 + "\n")
    
    generator = AIContentGenerator(quality_threshold=7.0)
    
    try:
        print("[INFO] Generating blog post...")
        print("   Topic: How to Use AI for Content Generation")
        print("   Style: technical")
        print("   Tone: professional")
        print("   Target length: 1500 words")
        print("   Quality threshold: 7.0/10\n")
        
        content, model_used, metrics = await generator.generate_blog_post(
            topic="How to Use AI for Content Generation",
            style="technical",
            tone="professional",
            target_length=1500,
            tags=["AI", "content", "generation", "automation"]
        )
        
        print("\n[SUCCESS] GENERATION SUCCESSFUL!\n")
        print(f"   Model used: {model_used}")
        print(f"   Quality score: {metrics.get('final_quality_score', 0):.1f}/10")
        print(f"   Content length: {len(content)} characters")
        print(f"   Word count: {len(content.split())} words")
        print(f"   Generation time: {metrics.get('generation_time_seconds', 0):.1f}s")
        print(f"   Generation attempts: {metrics.get('generation_attempts', 0)}")
        print(f"   Refinement attempts: {metrics.get('refinement_attempts', 0)}")
        
        print("\n[INFO] CONTENT PREVIEW (first 500 chars):")
        print("-" * 80)
        print(content[:500])
        print("-" * 80)
        
        print("\n" + "="*80)
        print("[SUCCESS] TEST COMPLETE - GENERATION WORKING!")
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"\n[ERROR]: {e}")
        print("\nTraceback:")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(test_blog_generation())
    sys.exit(0 if success else 1)
