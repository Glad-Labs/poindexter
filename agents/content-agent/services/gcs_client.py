import logging
from google.cloud import storage
from config import config
import datetime

class GCSClient:
    """Client for interacting with Google Cloud Storage."""
    def __init__(self):
        self.client = storage.Client(project=config.GCP_PROJECT_ID)
        self.bucket_name = config.GCS_BUCKET_NAME
        self.bucket = self.client.bucket(self.bucket_name)

    def upload_file(self, source_file_path: str, destination_blob_name: str) -> str:
        """
        Uploads a file to the GCS bucket and returns a long-lived signed URL.
        """
        try:
            blob = self.bucket.blob(destination_blob_name)
            blob.upload_from_filename(source_file_path)
            
            # Generate a signed URL that is valid for 7 days (the maximum allowed)
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