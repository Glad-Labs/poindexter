"""
This script defines a Google Cloud Function that acts as a secure endpoint 
for the Oversight Hub. It receives commands via an HTTP POST request and 
publishes them to a Google Cloud Pub/Sub topic for further processing by 
other services, such as the Co-Founder Agent.
"""
import os
import json
from google.cloud import pubsub_v1
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import auth

# Initialize Flask app
app = Flask(__name__)

# --- Security: CORS Configuration ---
# In a production environment, you should restrict the origins to your app's domain.
# For example: CORS(app, origins=["https://your-app-domain.com"])
CORS(app) 

# --- Firebase Admin Initialization ---
try:
    firebase_admin.initialize_app()
except ValueError:
    # If the app is already initialized, it will raise a ValueError.
    # This is to prevent errors during hot-reloads in a local dev environment.
    pass

# Get project ID and topic name from environment variables
PROJECT_ID = os.getenv('GCP_PROJECT')
TOPIC_NAME = os.getenv('PUBSUB_TOPIC')

if not PROJECT_ID or not TOPIC_NAME:
    raise ValueError("GCP_PROJECT and PUBSUB_TOPIC environment variables must be set.")

# Initialize Pub/Sub client
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_NAME)

def verify_firebase_token(request):
    """Verify Firebase ID token from the Authorization header."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None
    
    id_token = auth_header.split('Bearer ').pop()
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        print(f"Error verifying token: {e}")
        return None

@app.route('/intervene', methods=['POST'])
def intervene():
    """
    HTTP-triggered Cloud Function to publish a message to a Pub/Sub topic.

    This function is designed to be a secure endpoint for the Oversight Hub.
    It expects a JSON payload with a 'command' key.

    Request Body (JSON):
        {
            "command": "Your command for the AI agent",
            "task": { ... task object ... }
        }

    Returns:
        JSON: A status message indicating success or failure.
    """
    # --- Security: Verify Firebase Auth Token ---
    decoded_token = verify_firebase_token(request)
    if not decoded_token:
        return jsonify({"error": "Unauthorized. Invalid or missing Firebase Auth token."}), 403

    data = request.get_json()
    if not data or 'command' not in data:
        return jsonify({"error": "Invalid request. 'command' is required."}), 400

    try:
        message_json = json.dumps(data)
        message_bytes = message_json.encode('utf-8')

        # Publish the message to the Pub/Sub topic
        publish_future = publisher.publish(topic_path, data=message_bytes)
        publish_future.result()  # Wait for the publish operation to complete

        return jsonify({"status": "success", "message": f"Message published to {TOPIC_NAME}"}), 200

    except Exception as e:
        print(f"Error publishing message: {e}")
        return jsonify({"error": "An internal error occurred."}), 500

# This part is for local testing of the function
if __name__ == '__main__':
    # To test locally, you need to set the environment variables:
    # export GCP_PROJECT='your-gcp-project-id'
    # export PUBSUB_TOPIC='your-topic-name'
    app.run(port=8080, debug=True)
