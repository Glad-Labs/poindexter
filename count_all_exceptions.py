#!/usr/bin/env python3
"""
Count ALL exception handlers and identify unstandardized ones.
Standardized = logger.error(...[function_name]..., exc_info=True)
"""

import re
import glob
import os

os.chdir("src/cofounder_agent/services")

files = sorted(glob.glob("*.py"))
total_handlers = 0
standardized_count = 0
unstandardized_files = []

for filename in files:
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find all except blocks more precisely
        # Match: except <ExceptionType> as e: followed by anything until next except/def/class
        pattern = r'except\s+\w+(?:\s+as\s+\w+)?:\s*(.*?)(?=\nexcept|\ndef\s|\nclass\s|\Z)'
        except_blocks = re.findall(pattern, content, re.DOTALL)
        
        file_handlers = len(except_blocks)
        file_standardized = 0
        file_unstandardized = []
        
        for block in except_blocks:
            total_handlers += 1
            
            # Check if this block has logger.error with [something] and exc_info=True
            has_bracket_prefix = bool(re.search(r'logger\.error\(.*?\[.*?\].*?exc_info=True', block, re.DOTALL))
            
            if has_bracket_prefix:
                standardized_count += 1
                file_standardized += 1
            else:
                # Check what it actually has
                has_logger_error = bool(re.search(r'logger\.error\(', block))
                has_exc_info = bool(re.search(r'exc_info=True', block))
                
                if has_logger_error and has_exc_info:
                    # Has logger.error and exc_info but missing [function_name]
                    file_unstandardized.append(f"missing [function_name] prefix")
                elif has_logger_error and not has_exc_info:
                    # Has logger.error but missing exc_info
                    file_unstandardized.append(f"missing exc_info=True")
                else:
                    # Some other logging or no logging
                    has_logger = bool(re.search(r'logger\.(warning|debug|info|error)\(', block))
                    if has_logger:
                        file_unstandardized.append(f"uses logger but not logger.error or missing exc_info")
                    else:
                        file_unstandardized.append(f"no logger.error call")
        
        if file_unstandardized:
            unstandardized_files.append((filename, file_handlers, file_standardized, file_unstandardized))
    
    except Exception as e:
        print(f"Error processing {filename}: {e}")

print(f"\n{'='*80}")
print(f"EXCEPTION HANDLER STANDARDIZATION SUMMARY")
print(f"{'='*80}")
print(f"Total exception handlers: {total_handlers}")
print(f"Standardized (logger.error + [func_name] + exc_info=True): {standardized_count}")
print(f"Unstandardized: {total_handlers - standardized_count}")
print(f"Standardization rate: {100*standardized_count/total_handlers:.1f}%" if total_handlers > 0 else "N/A")

if unstandardized_files:
    print(f"\n{'='*80}")
    print(f"FILES WITH UNSTANDARDIZED HANDLERS ({len(unstandardized_files)} files):")
    print(f"{'='*80}")
    
    # Sort by number of unstandardized handlers
    unstandardized_files.sort(key=lambda x: len(x[3]), reverse=True)
    
    for filename, total, standardized, issues in unstandardized_files[:20]:  # Show top 20
        unstandardized = total - standardized
        print(f"\n{filename}: {standardized}/{total} standardized ({unstandardized} issues)")
        for issue in issues[:3]:  # Show first 3 issues per file
            print(f"  - {issue}")
else:
    print(f"\n✅ ALL exception handlers are standardized!")
