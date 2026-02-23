#!/usr/bin/env python3
"""
Auto-standardize exception handlers across all service files.
Converts all "except Exception as e:" handlers to use [_operation_name] prefix format.
"""

import re
import os
import sys
from pathlib import Path
from typing import List, Tuple, Set

# Force UTF-8 output on Windows
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Files already completed in previous sessions
COMPLETED_FILES = {
    "custom_workflows_service.py",
    "tasks_db.py",
    "task_executor.py",
    "image_service.py",
    "unified_orchestrator.py",
}

SERVICES_DIR = Path("src/cofounder_agent/services")


def get_latest_method_name(content: str, exception_line_no: int) -> str:
    """
    Extract the most recent method/function name before the exception line.
    """
    lines = content.split('\n')
    
    # Search backwards from exception line for a def statement
    for i in range(exception_line_no - 1, -1, -1):
        line = lines[i]
        match = re.search(r'def\s+(\w+)\s*\(', line)
        if match:
            method_name = match.group(1)
            # Skip private helpers, use meaningful operation names
            if method_name.startswith('_'):
                return method_name
            return f'_{method_name}'
    
    return '_unknown'


def extract_exception_context(lines: List[str], exception_idx: int) -> Tuple[str, str]:
    """
    Extract exception handler context and identify the logging statement.
    Returns (operation_name, exception_block_text)
    """
    operation_name = get_latest_method_name('\n'.join(lines), exception_idx)
    
    # Collect exception handler block (next 10 lines typically)
    block = []
    for i in range(exception_idx, min(exception_idx + 10, len(lines))):
        block.append(lines[i])
        if 'logger.' in lines[i]:
            break
    
    return operation_name, '\n'.join(block)


def standardize_exception_handler(content: str) -> Tuple[str, int]:
    """
    Standardize all except Exception handlers in content.
    Returns (modified_content, count_standardized)
    """
    lines = content.split('\n')
    count = 0
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Find "except Exception as e:" pattern
        if 'except Exception as e:' in line:
            operation_name = get_latest_method_name(content, i)
            
            # Check if this exception handler is already standardized
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                
                # Skip if already has [_operation_name] prefix
                if f'[{operation_name}]' in next_line or '[_' in next_line:
                    i += 1
                    continue
                
                # Find the logger line (usually within next 3 lines)
                logger_idx = None
                for j in range(i + 1, min(i + 4, len(lines))):
                    if 'logger.' in lines[j]:
                        logger_idx = j
                        break
                
                if logger_idx is not None:
                    logger_line = lines[logger_idx]
                    
                    # Skip if already standardized
                    if '[_' in logger_line:
                        i += 1
                        continue
                    
                    # Standardize the logger line
                    # Pattern 1: logger.error/warning/debug("...", ...) format
                    if 'logger.' in logger_line:
                        # Match various logger patterns
                        patterns = [
                            # Pattern: logger.error("[%s]...", param, ...)
                            (r'logger\.(\w+)\s*\(\s*"(\[%s\])?([^"]+)"', 
                             lambda m: f'logger.{m.group(1)}(f"[{operation_name}] {m.group(3)}"'),
                            # Pattern: logger.error("[%s] msg: %s", param, var)
                            (r'logger\.(\w+)\s*\(\s*"(\[%s\])?([^"]+)"\s*,\s*([^)]+)\)',
                             lambda m: f'logger.{m.group(1)}(f"[{operation_name}] {m.group(3)}", {m.group(4)})'),
                            # Pattern: logger.error(f"[_op] msg: {e}")
                            (r'logger\.(\w+)\s*\(\s*f"(\[_\w+\])?([^"]+)"',
                             lambda m: f'logger.{m.group(1)}(f"[{operation_name}] {m.group(3)}"'),
                        ]
                        
                        new_line = logger_line
                        for pattern, replacement in patterns:
                            if re.search(pattern, logger_line):
                                new_line = re.sub(pattern, replacement, logger_line)
                                break
                        
                        # Ensure exc_info=True for errors
                        if 'logger.error' in new_line and 'exc_info' not in new_line:
                            if new_line.rstrip().endswith(')'):
                                new_line = new_line.rstrip()[:-1] + ', exc_info=True)'
                        
                        lines[logger_idx] = new_line
                        count += 1
        
        i += 1
    
    return '\n'.join(lines), count


def process_services_dir():
    """
    Process all service files and standardize exceptions.
    Returns summary dict.
    """
    results = {
        'files_processed': 0,
        'files_skipped': 0,
        'exceptions_standardized': 0,
        'files': {}
    }
    
    if not SERVICES_DIR.exists():
        print(f"Services directory not found: {SERVICES_DIR}")
        return results
    
    # Find all Python service files
    service_files = sorted(SERVICES_DIR.glob('*.py'))
    
    for service_file in service_files:
        filename = service_file.name
        
        # Skip already completed files
        if filename in COMPLETED_FILES:
            results['files_skipped'] += 1
            print(f"[SKIP] Skipping {filename} (already completed)")
            continue
        
        # Read file with UTF-8 encoding
        try:
            with open(service_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"[FAIL] Failed to read {filename}: {e}")
            continue
        
        # Count exceptions before
        count_before = len(re.findall(r'except Exception as e:', content))
        if count_before == 0:
            results['files_skipped'] += 1
            print(f"[SKIP] Skipping {filename} (no exceptions)")
            continue
        
        # Standardize
        try:
            new_content, standardized_count = standardize_exception_handler(content)
        except Exception as e:
            print(f"[FAIL] Failed to process {filename}: {e}")
            continue
        
        # Write back if changed
        if new_content != content:
            try:
                with open(service_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                results['files_processed'] += 1
                results['exceptions_standardized'] += standardized_count
                results['files'][filename] = {
                    'total_exceptions': count_before,
                    'standardized': standardized_count,
                    'status': '[OK]'
                }
                print(f"[OK] {filename}: {standardized_count}/{count_before} exceptions standardized")
            except Exception as e:
                print(f"[FAIL] Failed to write {filename}: {e}")
        else:
            results['files_skipped'] += 1
            print(f"[SKIP] {filename}: No changes needed")
    
    return results


def validate_syntax(filepath: str) -> bool:
    """Quick syntax validation using Python's compile."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            compile(f.read(), filepath, 'exec')
        return True
    except SyntaxError as e:
        print(f"  [FAIL] Syntax error in {filepath}: {e}")
        return False
    except Exception as e:
        print(f"  [FAIL] Error validating {filepath}: {e}")
        return False


if __name__ == '__main__':
    print("=" * 70)
    print("Auto-standardizing exception handlers across service files...")
    print("=" * 70)
    print()
    
    results = process_services_dir()
    
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Files processed: {results['files_processed']}")
    print(f"Files skipped: {results['files_skipped']}")
    print(f"Total exceptions standardized: {results['exceptions_standardized']}")
    print()
    
    # Validate syntax on processed files
    print("Validating syntax on modified files...")
    syntax_errors = []
    for filename, details in results['files'].items():
        filepath = SERVICES_DIR / filename
        if not validate_syntax(str(filepath)):
            syntax_errors.append(filename)
        else:
            print(f"  [OK] {filename}")
    
    print()
    if syntax_errors:
        print(f"[FAIL] {len(syntax_errors)} file(s) with syntax errors:")
        for f in syntax_errors:
            print(f"  - {f}")
    else:
        print("[OK] All modified files have valid syntax")
    
    print()
    print("=" * 70)
