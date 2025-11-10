import re

with open('src/cofounder_agent/tests/test_memory_system.py', 'r') as f:
    content = f.read()

# Replace all Memory( with create_test_memory(
content = re.sub(
    r'memory\s*=\s*Memory\(',
    'memory = create_test_memory(',
    content
)

# Remove the datetime imports from Memory() calls by replacing the patterns
# that have created_at and last_accessed
patterns_to_fix = [
    (r'create_test_memory\(([^)]*?),\s*created_at=[^,]+,\s*last_accessed=[^,]+,', 
     r'create_test_memory(\1,'),
]

for pattern, replacement in patterns_to_fix:
    content = re.sub(pattern, replacement, content)

with open('src/cofounder_agent/tests/test_memory_system.py', 'w') as f:
    f.write(content)

print('✅ Fixed all Memory() constructor calls')
