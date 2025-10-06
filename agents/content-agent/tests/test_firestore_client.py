import pytest
from unittest.mock import MagicMock
from services.firestore_client import FirestoreClient

def test_firestore_client_initialization(mocker):
    """
    Tests that the Firestore client initializes correctly.
    This is a basic test to ensure the testing framework is set up.
    """
    # Mock the firestore.Client to avoid actual GCP calls
    mocker.patch('google.cloud.firestore.Client', return_value=MagicMock())
    
    client = FirestoreClient()
    assert client.db is not None
    assert isinstance(client.db, MagicMock)

# To run this test:
# 1. Navigate to the 'agents/content-agent' directory
# 2. Run the command: pytest
