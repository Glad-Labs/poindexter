#!/usr/bin/env python3
"""
Validation Script: Async/Await Fixes in content_routes.py

This script validates that all async method calls in content_routes.py
now properly use await keywords, preventing Pydantic validation errors.

Expected Result: All checks should show ✅ FIXED
"""

import re
import sys

def check_await_keywords():
    """Check that all async method calls have await keywords"""
    
    with open('src/cofounder_agent/routes/content_routes.py', 'r') as f:
        content = f.read()
    
    print("=" * 80)
    print("🔍 ASYNC/AWAIT FIX VALIDATION")
    print("=" * 80)
    print()
    
    # Find all task_store method calls
    async_methods = [
        'create_task',
        'get_task',
        'update_task',
        'delete_task',
        'list_tasks',
        'get_drafts'
    ]
    
    all_good = True
    fixes_verified = 0
    
    for method in async_methods:
        # Pattern: task_store.method_name(
        pattern = rf'task_store\.{method}\('
        matches = list(re.finditer(pattern, content))
        
        if matches:
            print(f"📌 Method: task_store.{method}()")
            print(f"   Found {len(matches)} call(s)")
            
            for i, match in enumerate(matches, 1):
                # Get line number
                line_num = content[:match.start()].count('\n') + 1
                
                # Get the line content
                line_start = content.rfind('\n', 0, match.start()) + 1
                line_end = content.find('\n', match.end())
                line_content = content[line_start:line_end].strip()
                
                # Check if await is present before the method call
                # Look back up to 50 characters before the method call
                before_call = content[max(0, match.start()-50):match.start()]
                has_await = 'await' in before_call or before_call.strip().endswith('await')
                
                status = "✅ FIXED" if has_await else "❌ MISSING"
                prefix = "await " if has_await else ""
                
                print(f"   {i}. Line {line_num}: {status}")
                print(f"      {prefix}{line_content[:70]}")
                
                if has_await:
                    fixes_verified += 1
                else:
                    all_good = False
            
            print()
    
    print("=" * 80)
    print(f"✅ VERIFICATION COMPLETE")
    print(f"   Total fixes verified: {fixes_verified}")
    print(f"   Status: {'✅ ALL FIXED' if all_good else '❌ SOME ISSUES REMAINING'}")
    print("=" * 80)
    print()
    
    return all_good

if __name__ == '__main__':
    success = check_await_keywords()
    sys.exit(0 if success else 1)
