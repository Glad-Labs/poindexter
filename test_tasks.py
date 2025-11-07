import requests
import json

token = '1cdef4eb369677d03e8721869670bb1d2497dbe39be92f8287bb2a61238451f4aec7eaeccb8e65886eb6939d814bec8701992176b6da2475016d037c8d0ed1209cb3028b56b676482cb813474a767a87422f0a7dd3458730b2ae6d24318573a56c0e3ccbf5fc364ec92eda0e65f11d3c6924e4c98f1187afd07d626f287ad61d'

headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
response = requests.get('http://localhost:8000/api/tasks?limit=20', headers=headers)

print(f'Status: {response.status_code}')
data = response.json()
tasks = data.get('tasks', [])
print(f'Total tasks: {len(tasks)}\n')

# Show which tasks have result data
for task in tasks[:10]:
    has_result = task.get('result') is not None
    result_str = 'NULL' if not has_result else str(task.get('result'))[:50]
    task_id_short = str(task.get('id', ''))[:8]
    task_name = task.get('task_name', 'N/A')
    status = task.get('status', 'unknown')
    
    print(f'{task_id_short}... | {task_name} ({status})')
    print(f'  Result: {result_str}')
    if has_result and isinstance(task.get('result'), dict):
        result = task.get('result')
        content = result.get('content') or result.get('generated_content') or 'NO CONTENT'
        print(f'  Content: {str(content)[:80]}')
    print()
