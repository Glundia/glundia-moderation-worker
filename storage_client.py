"""GCS operations for moving images between buckets."""

import logging
from typing import Optional

from google.cloud import storage

logger = logging.getLogger(__name__)


class StorageClient:
    """Google Cloud Storage client for image operations."""

    def __init__(self):
        """Initialize GCS client."""
        self.client = storage.Client()

    def move_blob(
        self,
        source_uri: str,
        destination_bucket: str,
        destination_blob_name: Optional[str] = None,
    ) -> str:
        """
        Move a blob from one bucket to another.

        Args:
            source_uri: Full GCS URI (gs://bucket/blob)
            destination_bucket: Name of destination bucket
            destination_blob_name: Name for blob in destination (default: same as source)

        Returns:
            GCS URI of the moved blob
        """
        # Parse source URI
        if not source_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {source_uri}")

        path_parts = source_uri[5:].split("/", 1)
        source_bucket_name = path_parts[0]
        source_blob_name = path_parts[1] if len(path_parts) > 1 else ""

        if destination_blob_name is None:
            destination_blob_name = source_blob_name

        source_bucket = self.client.bucket(source_bucket_name)
        source_blob = source_bucket.blob(source_blob_name)

        destination_bucket_obj = self.client.bucket(destination_bucket)
        destination_blob = source_bucket.copy_blob(
            source_blob, destination_bucket_obj, destination_blob_name
        )

        # Delete from source
        source_blob.delete()

        return f"gs://{destination_bucket}/{destination_blob_name}"

    def copy_blob(
        self,
        source_uri: str,
        destination_bucket: str,
        destination_blob_name: Optional[str] = None,
    ) -> str:
        """
        Copy a blob from one bucket to another (without deleting source).

        Args:
            source_uri: Full GCS URI (gs://bucket/blob)
            destination_bucket: Name of destination bucket
            destination_blob_name: Name for blob in destination (default: same as source)

        Returns:
            GCS URI of the copied blob
        """
        # Parse source URI
        if not source_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {source_uri}")

        path_parts = source_uri[5:].split("/", 1)
        source_bucket_name = path_parts[0]
        source_blob_name = path_parts[1] if len(path_parts) > 1 else ""

        if destination_blob_name is None:
            destination_blob_name = source_blob_name

        source_bucket = self.client.bucket(source_bucket_name)
        source_blob = source_bucket.blob(source_blob_name)

        destination_bucket_obj = self.client.bucket(destination_bucket)
        destination_blob = source_bucket.copy_blob(
            source_blob, destination_bucket_obj, destination_blob_name
        )

        return f"gs://{destination_bucket}/{destination_blob_name}"

    def delete_blob(self, gcs_uri: str) -> None:
        """
        Delete a blob from GCS.

        Args:
            gcs_uri: Full GCS URI (gs://bucket/blob)
        """
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        path_parts = gcs_uri[5:].split("/", 1)
        bucket_name = path_parts[0]
        blob_name = path_parts[1] if len(path_parts) > 1 else ""

        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.delete()

    def get_blob_metadata(self, gcs_uri: str) -> dict:
        """
        Get metadata for a blob.

        Args:
            gcs_uri: Full GCS URI (gs://bucket/blob)

        Returns:
            Dictionary with blob metadata
        """
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        path_parts = gcs_uri[5:].split("/", 1)
        bucket_name = path_parts[0]
        blob_name = path_parts[1] if len(path_parts) > 1 else ""

        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        metadata = blob.metadata or {}
        return {
            "content_type": blob.content_type,
            "size": blob.size,
            "created": blob.time_created,
            "updated": blob.updated,
            "metadata": metadata,
        }
