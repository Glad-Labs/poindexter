import requests
import json
import uuid
import jwt
import datetime

BASE_URL = "http://localhost:8000/api"
JWT_SECRET = "dev-jwt-secret-change-in-production-to-random-64-chars"
ALGORITHM = "HS256"

def generate_token():
    payload = {
        "user_id": "test-user-id",
        "sub": "test-user",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=15),
        "type": "access"
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)
    return token

def test_create_task_with_fields():
    print("Testing task creation with style, tone, and target_length...")
    
    token = generate_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    payload = {
        "task_name": f"Test Task {uuid.uuid4()}",
        "topic": "Test Topic",
        "category": "testing",
        "metadata": {
            "style": "technical",
            "tone": "professional",
            "word_count": 1234
        }
    }
    
    try:
        # 1. Create Task
        response = requests.post(f"{BASE_URL}/tasks", json=payload, headers=headers)
        if response.status_code != 201:
            print(f"❌ Failed to create task: {response.status_code} - {response.text}")
            return

        task_id = response.json()["id"]
        print(f"✅ Task created: {task_id}")
        
        # 2. Verify Task Details
        response = requests.get(f"{BASE_URL}/tasks/{task_id}", headers=headers)
        if response.status_code != 200:
            print(f"❌ Failed to get task details: {response.status_code}")
            return
            
        task = response.json()
        
        # Check fields
        style = task.get("style")
        tone = task.get("tone")
        target_length = task.get("target_length")
        
        print(f"   Style: {style}")
        print(f"   Tone: {tone}")
        print(f"   Target Length: {target_length}")
        
        if style == "technical" and tone == "professional" and target_length == 1234:
            print("✅ All fields correctly populated!")
        else:
            print("❌ Fields mismatch!")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_create_task_with_fields()
