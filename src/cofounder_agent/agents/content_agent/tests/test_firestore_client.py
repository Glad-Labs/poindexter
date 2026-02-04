from unittest.mock import MagicMock

import pytest
from src.agents.content_agent.services.firestore_client import FirestoreClient


@pytest.fixture
def mock_firestore_client(mocker):
    # Mock the firestore.Client to avoid actual GCP calls
    mocker.patch("google.cloud.firestore.Client", return_value=MagicMock())
    return FirestoreClient()


def test_firestore_client_initialization(mock_firestore_client):
    """
    Tests that the Firestore client initializes correctly.
    This is a basic test to ensure the testing framework is set up.
    """
    # Just check that the db attribute is set (mocked)
    assert hasattr(mock_firestore_client, "db")

    client = FirestoreClient()
    assert client.db is not None
    assert isinstance(client.db, MagicMock)


# To run this test:
# 1. Navigate to the 'agents/content-agent' directory
# 2. Run the command: pytest
