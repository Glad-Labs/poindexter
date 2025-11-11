#!/usr/bin/env python3
import requests
import json

task_id = '172f2421-a994-4733-af73-bc9db722e8cf'
r = requests.get(f'http://localhost:8000/api/tasks/{task_id}')
data = r.json()

print(json.dumps(data, indent=2, default=str))
