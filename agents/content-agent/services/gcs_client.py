import logging
from google.cloud import storage
from config import config

class GCSClient:
    """Client for interacting with Google Cloud Storage."""
    def __init__(self):
        self.client = storage.Client(project=config.GCP_PROJECT_ID)
        self.bucket_name = config.GCS_BUCKET_NAME
        self.bucket = self.client.bucket(self.bucket_name)
        self._ensure_public_access()

    def _ensure_public_access(self):
        """
        Ensures the bucket has a public access policy for all users to view objects.
        This is the modern, recommended approach for Uniform buckets.
        """
        try:
            policy = self.bucket.get_iam_policy(requested_policy_version=3)
            # Check if 'allUsers' already has the 'Storage Object Viewer' role
            if not any(b.role == 'roles/storage.objectViewer' and 'allUsers' in b.members for b in policy.bindings):
                policy.bindings.append({
                    "role": "roles/storage.objectViewer",
                    "members": {"allUsers"},
                })
                self.bucket.set_iam_policy(policy)
                logging.info(f"Set public read access (IAM) on bucket '{self.bucket_name}'.")
            else:
                logging.info(f"Public read access (IAM) already set on bucket '{self.bucket_name}'.")
        except Exception as e:
            logging.error(f"Failed to set IAM policy on GCS bucket: {e}")
            raise

    def upload_file(self, source_file_path: str, destination_blob_name: str) -> str:
        """
        Uploads a file to the GCS bucket. The public URL is constructed manually,
        as the object is now publicly readable via the bucket's IAM policy.
        """
        try:
            blob = self.bucket.blob(destination_blob_name)
            blob.upload_from_filename(source_file_path)
            
            # Construct the public URL manually. This is the standard format.
            public_url = f"https://storage.googleapis.com/{self.bucket_name}/{destination_blob_name}"
            
            logging.info(f"File {source_file_path} uploaded to {public_url}.")
            return public_url
        except Exception as e:
            logging.error(f"Failed to upload file to GCS: {e}")
            raise