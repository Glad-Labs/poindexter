import requests
import json

# Test GraphQL with GET
query = '{ posts { data { id attributes { title slug } } } }'
url = 'http://localhost:1337/graphql'
params = {'query': query}

print("Testing GraphQL GET request...")
r = requests.get(url, params=params)
print(f'Status: {r.status_code}')
print(f'Response: {r.text[:1000]}')
print(f'Headers: {r.headers.get("content-type")}')
