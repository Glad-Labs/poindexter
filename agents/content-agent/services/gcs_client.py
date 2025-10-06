import logging
from google.cloud import storage
from google.auth import default
from google.auth.impersonated_credentials import Credentials
from config import config
import datetime

class GCSClient:
    """Client for interacting with Google Cloud Storage."""
    def __init__(self):
        # Get the default credentials from the gcloud CLI
        self.source_credentials, _ = default()
        
        # Create impersonated credentials
        self.target_credentials = Credentials(
            source_credentials=self.source_credentials,
            target_principal=config.GCP_SERVICE_ACCOUNT_EMAIL,
            target_scopes=["https://www.googleapis.com/auth/devstorage.full_control"],
        )

        self.client = storage.Client(project=config.GCP_PROJECT_ID, credentials=self.target_credentials)
        self.bucket_name = config.GCS_BUCKET_NAME
        self.bucket = self.client.bucket(self.bucket_name)

    def upload_file(self, source_file_path: str, destination_blob_name: str) -> str:
        """
        Uploads a file to the GCS bucket and returns a long-lived signed URL.
        """
        try:
            blob = self.bucket.blob(destination_blob_name)
            blob.upload_from_filename(source_file_path)

            # Generate a signed URL that is valid for 7 days.
            # This is the modern, secure way to grant temporary access to a file
            # without making the bucket or object publicly accessible.
            expiration_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
            signed_url = blob.generate_signed_url(
                version="v4",
                expiration=expiration_date,
                method="GET",
            )
            
            logging.info(f"File {source_file_path} uploaded to GCS. Signed URL generated.")
            return signed_url
        except Exception as e:
            logging.error(f"Failed to upload file to GCS or generate signed URL: {e}")
            raise