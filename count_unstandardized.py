#!/usr/bin/env python3
"""
Count unstandardized exception handlers in service files.
Looks for except Exception blocks that use logger.warning/debug/info
without exc_info=True parameter.
"""

import re
import glob
import os

os.chdir("src/cofounder_agent/services")

files = glob.glob("*.py")
total_unstandardized = 0
details = []

for filename in files:
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find all except blocks with their logger calls
        # Look for except Exception followed by logger.warning/debug/info WITHOUT exc_info
        pattern = r'except Exception.*?:\n(.*?)(?=\n(?:    )?(?:except|else|finally|def|class|$))'
        except_blocks = re.findall(pattern, content, re.DOTALL)
        
        for block in except_blocks:
            # Check if block has logger.warning, logger.debug, or logger.info
            if re.search(r'logger\.(warning|debug|info)\(', block):
                # Check if exc_info=True is NOT present
                if 'exc_info=True' not in block and 'exc_info' not in block:
                    total_unstandardized += 1
                    details.append(f"{filename}: unstandardized handler found")
    
    except Exception as e:
        print(f"Error processing {filename}: {e}")

print(f"\nTotal unstandardized exception handlers: {total_unstandardized}")
print(f"\nFiles with unstandardized handlers: {len(set([d.split(':')[0] for d in details]))}")

if details:
    print("\nDetails:")
    for detail in details[:20]:  # Show first 20
        print(f"  - {detail}")
