#!/usr/bin/env python3
"""Fix all broken imports from utils. to use relative imports"""
import re
from pathlib import Path

# Files to fix
files_to_fix = [
    "src/cofounder_agent/services/admin_db.py",
    "src/cofounder_agent/services/content_orchestrator.py",
    "src/cofounder_agent/services/tasks_db.py",
    "src/cofounder_agent/services/users_db.py",
    "src/cofounder_agent/services/content_db.py",
    "src/cofounder_agent/utils/service_dependencies.py",
    "src/cofounder_agent/tests/test_phase2_integration.py",
    "src/cofounder_agent/routes/task_routes.py",
    "src/cofounder_agent/routes/subtask_routes.py",
    "src/cofounder_agent/routes/settings_routes.py",
    "src/cofounder_agent/routes/quality_routes.py",
    "src/cofounder_agent/routes/orchestrator_routes.py",
    "src/cofounder_agent/routes/natural_language_content_routes.py",
    "src/cofounder_agent/routes/content_routes.py",
    "src/cofounder_agent/routes/bulk_task_routes.py",
    "src/cofounder_agent/routes/analytics_routes.py",
    "src/cofounder_agent/main.py",
]

def fix_imports_in_file(file_path):
    """Fix utils imports in a single file"""
    path = Path(file_path)
    if not path.exists():
        print(f"SKIP: {file_path} - file not found")
        return False
    
    try:
        content = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        content = path.read_text(encoding='cp1252', errors='replace')
    
    original_content = content
    
    # Replace all "from utils." with relative path based on file location
    # Files in services/, routes/, utils/ need "../utils."
    # Files in root (main.py) need "utils."
    
    if "services/" in str(path) or "routes/" in str(path) or "tests/" in str(path):
        # These need to go up one level then into utils
        content = re.sub(r"^from utils\.", "from ..utils.", content, flags=re.MULTILINE)
    elif path.name == "service_dependencies.py" and "utils" in str(path):
        # This is inside utils/service_dependencies.py, don't change
        pass
    else:
        # main.py and others in cofounder_agent root  - use from utils.
        # This is fine as-is, but we need to ensure utils is in sys.path
        pass
    
    if content != original_content:
        try:
            path.write_text(content, encoding='utf-8')
        except UnicodeEncodeError:
            path.write_text(content, encoding='cp1252', errors='replace')
        print(f"FIX:  {file_path}")
        # Show what changed
        orig_lines = original_content.split('\n')
        new_lines = content.split('\n')
        for i, (orig, new) in enumerate(zip(orig_lines, new_lines)):
            if orig != new:
                print(f"      Line {i+1}:")
                print(f"        OLD: {orig}")
                print(f"        NEW: {new}")
        return True
    else:
        print(f"OK:   {file_path}")
        return False

if __name__ == "__main__":
    print("FIXING BROKEN UTILS IMPORTS")
    print("=" * 80)
    
    count_fixed = 0
    for file_path in files_to_fix:
        if fix_imports_in_file(file_path):
            count_fixed += 1
    
    print("=" * 80)
    print(f"FIXED {count_fixed}/{len(files_to_fix)} files")
