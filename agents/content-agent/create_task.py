import sys
import os
import logging
from datetime import datetime

# Add the project root to the Python path to allow for absolute imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from google.cloud import firestore
from config import config

def create_task():
    """
    A command-line utility to add a new content generation task to Firestore.
    """
    try:
        db = firestore.Client(project=config.GCP_PROJECT_ID)
        tasks_collection = db.collection("tasks")
        
        print("="*50)
        print("Create a New Content Generation Task")
        print("="*50)
        
        topic = input("Enter the Topic: ")
        primary_keyword = input("Enter the Primary Keyword: ")
        target_audience = input("Enter the Target Audience: ")
        category = input("Enter the Category: ")
        
        if not all([topic, primary_keyword, target_audience, category]):
            print("\n[ERROR] All fields are required. Task creation cancelled.")
            return

        task_data = {
            "topic": topic,
            "primary_keyword": primary_keyword,
            "target_audience": target_audience,
            "category": category,
            "status": "New",
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow()
        }
        
        doc_ref = tasks_collection.add(task_data)
        task_id = doc_ref[1].id
        
        print("\n" + "="*50)
        print(f"✅ Successfully created task with ID: {task_id}")
        print("="*50)

    except Exception as e:
        logging.error(f"An error occurred while creating the task: {e}", exc_info=True)
        print(f"\n[ERROR] Failed to create task. Please check the logs for details.")

if __name__ == "__main__":
    # Basic logging setup for the script
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    create_task()
