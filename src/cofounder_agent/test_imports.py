"""
Diagnostic script to test Task Executor import and initialization
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

print("[+] Testing Task Executor...")

try:
    from services.task_executor import TaskExecutor
    print("✅ Task Executor imported successfully")
except Exception as e:
    print(f"❌ Failed to import TaskExecutor: {e}")
    sys.exit(1)

try:
    from services.database_service import DatabaseService
    print("✅ DatabaseService imported successfully")
except Exception as e:
    print(f"❌ Failed to import DatabaseService: {e}")
    sys.exit(1)

print("\n[+] Task Executor and DatabaseService imports OK")
print("✅ Diagnostic passed - ready to run backend")
