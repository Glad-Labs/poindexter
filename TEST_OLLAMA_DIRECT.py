#!/usr/bin/env python
"""
Direct test of Ollama connection and response format.
Run this to diagnose blog generation issues.
"""

import httpx
import json
import asyncio

async def test_ollama():
    """Test Ollama connection and response format"""
    
    print("\n" + "="*80)
    print("🔍 OLLAMA CONNECTION TEST")
    print("="*80 + "\n")
    
    # 1. Check if Ollama is running
    print("1️⃣  Checking Ollama health...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:11434/api/tags")
            if response.status_code == 200:
                print(f"   ✅ Ollama is running (Status: 200)")
                models = response.json().get("models", [])
                print(f"   📦 Available models: {len(models)}")
                for model in models:
                    print(f"      - {model.get('name')}")
            else:
                print(f"   ❌ Ollama returned status {response.status_code}")
                return
    except Exception as e:
        print(f"   ❌ Cannot connect to Ollama: {e}")
        return
    
    # 2. Test simple generation
    print("\n2️⃣  Testing simple generation with neural-chat...")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {
                "model": "neural-chat:latest",
                "prompt": "Say hello to the world.",
                "system": "You are a helpful assistant.",
                "stream": False
            }
            
            response = await client.post(
                "http://localhost:11434/api/generate",
                json=payload
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Generation successful")
                print(f"   Response type: {type(data)}")
                print(f"   Response keys: {list(data.keys())}")
                print(f"   Text content: {data.get('response', '')[:100]}...")
                print(f"   Tokens generated: {data.get('eval_count', 0)}")
                print(f"   Total duration: {data.get('total_duration', 0) / 1e9:.1f}s")
            else:
                print(f"   ❌ Generation failed (status: {response.status_code})")
                print(f"   Response: {response.text[:200]}")
    except asyncio.TimeoutError:
        print(f"   ❌ Generation timed out (>120s)")
    except Exception as e:
        print(f"   ❌ Generation error: {e}")
    
    # 3. Test blog generation prompt
    print("\n3️⃣  Testing blog post generation...")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            system_prompt = """You are an expert technical writer and blogger.
Your writing style is technical.
Your tone is professional.
Write for an educated but general audience.
Generate approximately 1500 words.
Format as Markdown with proper headings (# for title, ## for sections, ### for subsections).
Include:
- Compelling introduction
- 3-5 main sections with practical insights
- Real-world examples or bullet points
- Clear conclusion with call-to-action
Tags: AI, blog, generation"""

            generation_prompt = """Write a professional blog post about: How to use AI for content generation

Requirements:
- Target length: approximately 1500 words
- Style: technical
- Tone: professional
- Format: Markdown with clear structure
- Include practical examples and insights
- End with a clear call-to-action

Start writing now:"""

            payload = {
                "model": "neural-chat:latest",
                "system": system_prompt,
                "prompt": generation_prompt,
                "stream": False
            }
            
            response = await client.post(
                "http://localhost:11434/api/generate",
                json=payload,
                timeout=120.0
            )
            
            if response.status_code == 200:
                data = response.json()
                content = data.get('response', '')
                word_count = len(content.split())
                
                print(f"   ✅ Blog generation successful")
                print(f"   Content length: {len(content)} characters")
                print(f"   Word count: {word_count}")
                print(f"   Tokens: {data.get('eval_count', 0)}")
                print(f"   Duration: {data.get('total_duration', 0) / 1e9:.1f}s")
                print(f"\n   📄 Content preview (first 300 chars):")
                print(f"   {content[:300]}...")
                
                # Check if it passes the validation threshold
                if len(content) > 100:
                    print(f"\n   ✅ Content length check: PASS (>{100} chars)")
                else:
                    print(f"\n   ❌ Content length check: FAIL (<={100} chars)")
            else:
                print(f"   ❌ Blog generation failed (status: {response.status_code})")
                print(f"   Response: {response.text[:300]}")
    except asyncio.TimeoutError:
        print(f"   ❌ Blog generation timed out (>120s)")
    except Exception as e:
        print(f"   ❌ Blog generation error: {e}")
    
    print("\n" + "="*80)
    print("✅ TEST COMPLETE")
    print("="*80 + "\n")

if __name__ == "__main__":
    asyncio.run(test_ollama())
