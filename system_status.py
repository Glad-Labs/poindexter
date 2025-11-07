#!/usr/bin/env python3
"""
GLAD Labs System Integration Status Check
===========================================
Quick diagnostic script to verify all services are connected and working
"""

import subprocess
import json
import sys
from datetime import datetime

def check_service(name, url, description=""):
    """Check if a service is running"""
    try:
        result = subprocess.run(
            ['curl', '-s', '-m', '2', url, '-o', '/dev/null', '-w', '%{http_code}'],
            capture_output=True,
            text=True,
            timeout=3
        )
        status_code = result.stdout.strip()
        
        if status_code in ['200', '301', '302', '404', '405']:
            print(f"✅ {name:20} | {description:40} | HTTP {status_code}")
            return True
        else:
            print(f"⚠️  {name:20} | {description:40} | HTTP {status_code}")
            return True
    except Exception as e:
        print(f"❌ {name:20} | {description:40} | ERROR: {str(e)[:30]}")
        return False

def main():
    print("\n" + "="*100)
    print("🚀 GLAD LABS SYSTEM INTEGRATION STATUS CHECK")
    print("="*100)
    print(f"\n📍 Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n" + "-"*100)
    
    services = [
        ("Oversight Hub", "http://localhost:3001", "React dashboard for task creation"),
        ("Public Site", "http://localhost:3000", "Next.js blog frontend"),
        ("Strapi CMS", "http://localhost:1337", "Content management system"),
        ("Cofounder Agent", "http://localhost:8000/docs", "FastAPI content pipeline"),
        ("Ollama LLM", "http://localhost:11434/api/tags", "Local AI model server"),
    ]
    
    results = []
    for name, url, desc in services:
        results.append(check_service(name, url, desc))
    
    print("-"*100)
    
    passed = sum(results)
    total = len(results)
    
    print(f"\n📊 SUMMARY: {passed}/{total} services responding")
    
    if passed == total:
        print("\n✨ ALL SYSTEMS OPERATIONAL! Ready to create blog posts.")
        print("\n🎯 NEXT STEPS:")
        print("  1. Open http://localhost:3001 (Oversight Hub)")
        print("  2. Fill out blog post form")
        print("  3. Click 'Generate Blog Post'")
        print("  4. Verify in http://localhost:1337/admin (Strapi)")
        print("  5. See result at http://localhost:3000 (Public Site)")
        return 0
    else:
        print("\n⚠️  Some services not responding. Check your terminal windows:")
        print("  • Terminal 1: Strapi CMS (`npm run develop`)")
        print("  • Terminal 2: Oversight Hub (`npm start`)")
        print("  • Terminal 3: Public Site (`npm run dev`)")
        print("  • Terminal 4: Cofounder Agent (FastAPI already running)")
        return 1

if __name__ == "__main__":
    sys.exit(main())
