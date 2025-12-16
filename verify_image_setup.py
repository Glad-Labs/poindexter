#!/usr/bin/env python3
"""
Setup Verification Checklist for Image Generation

Runs through all checks to verify image generation is ready.
"""

import os
import sys
import subprocess
from pathlib import Path

print("\n" + "="*70)
print("🔍 IMAGE GENERATION SETUP VERIFICATION")
print("="*70)

checks_passed = 0
checks_failed = 0

# Check 1: Environment Variables
print("\n✓ Check 1: Environment Variables")
print("-" * 70)

env_file = Path(".env.local")
if env_file.exists():
    with open(env_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        if "PEXELS_API_KEY" in content:
            print("✅ PEXELS_API_KEY found in .env.local")
            checks_passed += 1
        else:
            print("⚠️  PEXELS_API_KEY not found in .env.local")
            print("   Please add: PEXELS_API_KEY=your_api_key_here")
            print("   Get key from: https://www.pexels.com/api/")
            checks_failed += 1
else:
    print("⚠️  .env.local not found")
    print("   Create it with: PEXELS_API_KEY=your_api_key_here")
    checks_failed += 1

# Check 2: Backend Files
print("\n✓ Check 2: Backend Files")
print("-" * 70)

backend_files = [
    "src/cofounder_agent/routes/media_routes.py",
    "src/cofounder_agent/services/image_service.py",
    "src/cofounder_agent/utils/route_registration.py",
]

for filepath in backend_files:
    if Path(filepath).exists():
        print(f"✅ {filepath}")
        checks_passed += 1
    else:
        print(f"❌ {filepath} - NOT FOUND")
        checks_failed += 1

# Check 3: Frontend Files
print("\n✓ Check 3: Frontend Files")
print("-" * 70)

frontend_file = "web/oversight-hub/src/components/tasks/ResultPreviewPanel.jsx"
if Path(frontend_file).exists():
    with open(frontend_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        if "/api/media/generate-image" in content:
            print(f"✅ {frontend_file}")
            print("   - Has /api/media/generate-image endpoint call")
            checks_passed += 1
        else:
            print(f"⚠️  {frontend_file}")
            print("   - Endpoint call might not be updated")
            checks_failed += 1
else:
    print(f"❌ {frontend_file} - NOT FOUND")
    checks_failed += 1

# Check 4: Test Files
print("\n✓ Check 4: Test Files")
print("-" * 70)

test_file = "test_media_endpoints.py"
if Path(test_file).exists():
    print(f"✅ {test_file}")
    checks_passed += 1
else:
    print(f"⚠️  {test_file} - Test file not found")
    print("   You can still run tests manually with curl")
    checks_failed += 1

# Check 5: Route Registration
print("\n✓ Check 5: Route Registration")
print("-" * 70)

route_file = "src/cofounder_agent/utils/route_registration.py"
if Path(route_file).exists():
    with open(route_file, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        if "media_router" in content and "media_routes" in content:
            print(f"✅ {route_file}")
            print("   - media_router is registered")
            checks_passed += 1
        else:
            print(f"❌ {route_file}")
            print("   - media_router registration missing")
            checks_failed += 1

# Check 6: Python Syntax
print("\n✓ Check 6: Python Syntax")
print("-" * 70)

media_routes_file = "src/cofounder_agent/routes/media_routes.py"
try:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", media_routes_file],
        capture_output=True,
        text=True,
        timeout=5
    )
    if result.returncode == 0:
        print(f"✅ {media_routes_file}")
        print("   - No syntax errors")
        checks_passed += 1
    else:
        print(f"❌ {media_routes_file}")
        print(f"   - Syntax error: {result.stderr}")
        checks_failed += 1
except Exception as e:
    print(f"⚠️  Could not check syntax: {e}")
    checks_failed += 1

# Check 7: Documentation
print("\n✓ Check 7: Documentation")
print("-" * 70)

docs_file = "IMAGE_GENERATION_GUIDE.md"
if Path(docs_file).exists():
    print(f"✅ {docs_file}")
    print("   - Comprehensive setup guide available")
    checks_passed += 1
else:
    print(f"⚠️  {docs_file} - Guide not found")
    checks_failed += 1

# Summary
print("\n" + "="*70)
print("📊 VERIFICATION SUMMARY")
print("="*70)

total_checks = checks_passed + checks_failed
print(f"\nTotal Checks: {total_checks}")
print(f"✅ Passed: {checks_passed}")
print(f"❌ Failed: {checks_failed}")

if checks_failed == 0:
    print("\n🎉 All checks passed! Ready to use image generation.")
    print("\nNext steps:")
    print("  1. Start FastAPI server: python src/cofounder_agent/main.py")
    print("  2. Test endpoints: python test_media_endpoints.py")
    print("  3. Use in Oversight Hub: Click 'Generate Featured Image' button")
    sys.exit(0)
else:
    print(f"\n⚠️  {checks_failed} issue(s) need attention.")
    print("\nSee above for details and solutions.")
    sys.exit(1)
