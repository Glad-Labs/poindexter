#!/usr/bin/env python3
"""
Verification script - Confirms that the Ollama 500 error fix has been applied
Run this to verify the model list has been updated
"""

import sys
from pathlib import Path

def verify_fix():
    """Verify the fix was applied correctly"""
    
    print("\n" + "="*70)
    print("OLLAMA 500 ERROR FIX - VERIFICATION")
    print("="*70 + "\n")
    
    # Path to the file
    file_path = Path(__file__).parent / "src" / "cofounder_agent" / "services" / "ai_content_generator.py"
    
    if not file_path.exists():
        print(f"❌ ERROR: File not found at {file_path}")
        return False
    
    print(f"✓ Checking file: {file_path}\n")
    
    # Read the file
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check for the old problematic model
    if 'for model_name in ["neural-chat:latest", "deepseek-r1:14b", "llama2:latest"]:' in content:
        print("❌ FAILED: Old model list still present!")
        print("   Found: ['neural-chat:latest', 'deepseek-r1:14b', 'llama2:latest']")
        print("   This includes the problematic deepseek-r1:14b model")
        return False
    
    # Check for the new fixed model list
    if 'for model_name in ["neural-chat:latest", "llama2:latest", "qwen2:7b"]:' in content:
        print("✓ VERIFIED: New model list is correct!")
        print("   Found: ['neural-chat:latest', 'llama2:latest', 'qwen2:7b']")
        print("   ✓ deepseek-r1:14b removed (16GB+ VRAM required)")
        print("   ✓ qwen2:7b added (8GB VRAM, reliable)")
        
        # Count the changes
        if content.count('["neural-chat:latest", "llama2:latest", "qwen2:7b"]') == 1:
            print("   ✓ Single occurrence found (correct)")
        else:
            print(f"   ⚠ Found {content.count('neural-chat')} occurrences (may be duplicated)")
        
        print("\n✅ FIX SUCCESSFULLY APPLIED!\n")
        return True
    else:
        print("❌ FAILED: New model list not found!")
        print("   Expected: ['neural-chat:latest', 'llama2:latest', 'qwen2:7b']")
        print("   The fix may not have been applied correctly")
        
        # Show what's actually there
        if "for model_name in [" in content:
            import re
            match = re.search(r'for model_name in \[(.*?)\]:', content)
            if match:
                print(f"   Found instead: {match.group(0)}")
        
        return False

def check_requirements():
    """Check if required models are documented"""
    
    print("="*70)
    print("REQUIRED MODELS CHECK")
    print("="*70 + "\n")
    
    models = ["neural-chat:latest", "llama2:latest", "qwen2:7b"]
    
    print("Required models for Ollama:")
    for model in models:
        print(f"  ✓ {model}")
    
    print("\nTo download these models, run:")
    print("  ollama pull neural-chat:latest")
    print("  ollama pull llama2:latest")
    print("  ollama pull qwen2:7b")
    
    return True

def main():
    # Run verification
    fix_applied = verify_fix()
    check_requirements()
    
    # Summary
    print("="*70)
    if fix_applied:
        print("✅ VERIFICATION PASSED - Fix is ready!")
        print("\nNext steps:")
        print("  1. Download models: ollama pull neural-chat:latest")
        print("  2. Restart Ollama: taskkill /IM ollama.exe /F && ollama serve")
        print("  3. Restart FastAPI: cd src\\cofounder_agent && python main.py")
        print("  4. Test: python test_ollama_fix.py")
        print("="*70 + "\n")
        return 0
    else:
        print("❌ VERIFICATION FAILED - Fix not applied correctly")
        print("\nTroubleshooting:")
        print("  1. Check file: src/cofounder_agent/services/ai_content_generator.py")
        print("  2. Line 258 should have: ['neural-chat:latest', 'llama2:latest', 'qwen2:7b']")
        print("  3. Re-apply fix if needed")
        print("="*70 + "\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
