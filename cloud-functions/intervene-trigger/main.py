import os
import json
from google.cloud import pubsub_v1
from flask import Flask, request, jsonify

# Initialize Flask app
app = Flask(__name__)

# Get project ID and topic name from environment variables
PROJECT_ID = os.getenv('GCP_PROJECT')
TOPIC_NAME = os.getenv('PUBSUB_TOPIC')

if not PROJECT_ID or not TOPIC_NAME:
    raise ValueError("GCP_PROJECT and PUBSUB_TOPIC environment variables must be set.")

# Initialize Pub/Sub client
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_NAME)

@app.route('/intervene', methods=['POST'])
def intervene():
    """
    HTTP-triggered Cloud Function to publish a message to a Pub/Sub topic.
    This function is designed to be a secure endpoint for the Oversight Hub.
    """
    # --- Security: CORS and Authentication ---
    # In a production environment, you would add more robust security here,
    # such as checking for a valid Firebase Auth token in the request headers
    # and configuring CORS to only allow requests from your Oversight Hub's domain.
    
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
