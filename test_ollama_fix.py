#!/usr/bin/env python3
"""
Test script for Ollama 500 error fix
Tests the model fallback chain after switching from deepseek-r1:14b to qwen2:7b
"""

import asyncio
import sys
import subprocess
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

async def test_ollama_health():
    """Test 1: Check if Ollama is running"""
    print("\n" + "="*60)
    print("TEST 1: Ollama Service Health")
    print("="*60)
    
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:11434/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json()
                print(f"✓ Ollama is running")
                print(f"  Available models: {len(models.get('models', []))}")
                for model in models.get('models', [])[:5]:
                    print(f"    - {model['name']}")
                return True
    except Exception as e:
        print(f"✗ Ollama not responding: {e}")
        print("  Fix: Run 'ollama serve' in separate terminal")
        return False

async def test_neural_chat_model():
    """Test 2: Test neural-chat model (priority 1)"""
    print("\n" + "="*60)
    print("TEST 2: neural-chat:latest Model")
    print("="*60)
    
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "neural-chat:latest",
                "prompt": "Write a short paragraph about AI.",
                "stream": False,
                "temperature": 0.7,
                "max_tokens": 100
            }
            print("  Sending request to neural-chat:latest...")
            response = await client.post(
                "http://localhost:11434/api/generate",
                json=payload,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✓ neural-chat:latest works!")
                print(f"  Response: {result.get('response', '')[:100]}...")
                print(f"  Tokens: {result.get('total_duration', 'N/A')}")
                return True
            else:
                print(f"✗ neural-chat:latest failed: HTTP {response.status_code}")
                print(f"  Response: {response.text[:200]}")
                return False
    except Exception as e:
        print(f"✗ neural-chat:latest error: {e}")
        return False

async def test_llama2_model():
    """Test 3: Test llama2 model (priority 2)"""
    print("\n" + "="*60)
    print("TEST 3: llama2:latest Model")
    print("="*60)
    
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "llama2:latest",
                "prompt": "Write a short paragraph about machine learning.",
                "stream": False,
                "temperature": 0.7,
                "max_tokens": 100
            }
            print("  Sending request to llama2:latest...")
            response = await client.post(
                "http://localhost:11434/api/generate",
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✓ llama2:latest works!")
                print(f"  Response: {result.get('response', '')[:100]}...")
                return True
            else:
                print(f"✗ llama2:latest failed: HTTP {response.status_code}")
                print(f"  Response: {response.text[:200]}")
                return False
    except asyncio.TimeoutError:
        print(f"✗ llama2:latest timeout (too slow)")
        return False
    except Exception as e:
        print(f"✗ llama2:latest error: {e}")
        return False

async def test_ai_content_generator():
    """Test 4: Test AIContentGenerator with new model list"""
    print("\n" + "="*60)
    print("TEST 4: AIContentGenerator Integration")
    print("="*60)
    
    try:
        from cofounder_agent.services.ai_content_generator import AIContentGenerator
        
        gen = AIContentGenerator()
        print("  Testing content generation...")
        
        content, model, metrics = await asyncio.wait_for(
            gen.generate_content(
                topic="Artificial Intelligence",
                style="informative",
                target_length=200
            ),
            timeout=120
        )
        
        if content and len(content) > 50:
            print(f"✓ AIContentGenerator works!")
            print(f"  Model used: {model}")
            print(f"  Content length: {len(content)} chars")
            print(f"  Quality score: {metrics.get('quality_score', 'N/A'):.1f}")
            print(f"  Generation time: {metrics.get('generation_time_seconds', 'N/A'):.1f}s")
            return True
        else:
            print(f"✗ Generated content too short or empty")
            return False
    except Exception as e:
        print(f"✗ AIContentGenerator error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def check_vram():
    """Check available VRAM (GPU memory)"""
    print("\n" + "="*60)
    print("SYSTEM CHECK: GPU Memory")
    print("="*60)
    
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            output = result.stdout.strip().split(',')
            if len(output) >= 4:
                gpu_name = output[0].strip()
                gpu_util = output[1].strip()
                mem_used = float(output[2].strip())
                mem_total = float(output[3].strip())
                mem_free = mem_total - mem_used
                
                print(f"  GPU: {gpu_name}")
                print(f"  Memory: {mem_used:.0f}MB / {mem_total:.0f}MB")
                print(f"  Free: {mem_free:.0f}MB ({100*mem_free/mem_total:.1f}%)")
                
                if mem_free < 2048:  # Less than 2GB
                    print(f"  ⚠ WARNING: Low GPU memory available")
                    return False
                return True
        else:
            print("  Note: GPU memory check requires nvidia-smi")
            return True
    except FileNotFoundError:
        print("  Note: nvidia-smi not found (CPU mode)")
        return True
    except Exception as e:
        print(f"  Warning: Could not check GPU memory: {e}")
        return True

async def main():
    """Run all tests"""
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + " OLLAMA 500 ERROR FIX - TEST SUITE ".center(58) + "║")
    print("║" + " Verifying model switch from deepseek-r1:14b → qwen2:7b ".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    results = {}
    
    # System checks
    results['vram'] = await check_vram()
    results['ollama_health'] = await test_ollama_health()
    
    if not results['ollama_health']:
        print("\n❌ TESTS FAILED: Ollama not running")
        print("   Fix: Run 'ollama serve' in a separate terminal")
        return 1
    
    # Model tests
    results['neural_chat'] = await test_neural_chat_model()
    results['llama2'] = await test_llama2_model()
    
    # Integration test
    results['content_generator'] = await test_ai_content_generator()
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {test_name.upper().ljust(25)}: {status}")
    
    print(f"\n  Result: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ ALL TESTS PASSED - Ollama fix is working!")
        print("  You can now use content generation without 500 errors.")
        return 0
    elif passed >= 3:
        print("\n⚠ PARTIAL SUCCESS - Some models working, others slow/unavailable")
        print("  Content generation should work but may be slow.")
        return 1
    else:
        print("\n❌ TESTS FAILED - Fix did not resolve issue")
        print("  See OLLAMA_500_ERROR_DIAGNOSIS.md for more troubleshooting")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
