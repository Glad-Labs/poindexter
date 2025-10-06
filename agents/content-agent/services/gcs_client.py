import logging
from google.cloud import storage
from config import config

class GCSClient:
    """Client for interacting with Google Cloud Storage."""
    def __init__(self):
        self.client = storage.Client(project=config.GCP_PROJECT_ID)
        self.bucket_name = config.GCS_BUCKET_NAME
        self.bucket = self.client.bucket(self.bucket_name)

    def upload_file(self, source_file_path: str, destination_blob_name: str) -> str:
        """
        Uploads a file to the GCS bucket and makes it public.

        Args:
            source_file_path (str): The local path to the file to upload.
            destination_blob_name (str): The desired name of the object in GCS.

        Returns:
            str: The public URL of the uploaded file.
        """
        try:
            blob = self.bucket.blob(destination_blob_name)
            blob.upload_from_filename(source_file_path)
            blob.make_public()
            logging.info(f"File {source_file_path} uploaded to {destination_blob_name} and made public.")
            return blob.public_url
        except Exception as e:
            logging.error(f"Failed to upload file to GCS: {e}")
            raise