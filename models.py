"""Pub/Sub message models."""

from typing import Optional
from pydantic import BaseModel, Field


class GcsObjectMetadata(BaseModel):
    """GCS object metadata from Cloud Storage notification."""

    kind: str = Field(..., description="Object kind (storage#object)")
    id: str = Field(..., description="Object ID")
    selfLink: str = Field(..., description="Self link to object")
    name: str = Field(..., description="Object name (file path)")
    bucket: str = Field(..., description="Bucket name")
    generation: str = Field(..., description="Object generation")
    metageneration: str = Field(..., description="Object metageneration")
    contentType: str = Field(..., description="Content type")
    timeCreated: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Update timestamp")
    storageClass: str = Field(..., description="Storage class")
    size: str = Field(..., description="Object size in bytes")
    md5Hash: str = Field(..., description="MD5 hash")
    mediaLink: str = Field(..., description="Media download link")
    crc32c: str = Field(..., description="CRC32C checksum")
    etag: str = Field(..., description="Entity tag")


class UploadEvent(BaseModel):
    """Image upload event data (custom format from API)."""

    image_id: str = Field(..., description="UUID of the image record")
    gcs_uri: str = Field(..., description="GCS URI of the uploaded image")
    uploaded_by: str = Field(..., description="UUID of the user who uploaded")
    timestamp: str = Field(..., description="ISO timestamp of upload")


class PubsubMessage(BaseModel):
    """Pub/Sub message wrapper."""

    data: str = Field(..., description="Base64-encoded JSON event data")
    message_id: str = Field(..., description="Pub/Sub message ID")
    publish_time: Optional[str] = Field(None, description="Publish timestamp")


class PubsubPushRequest(BaseModel):
    """Incoming Pub/Sub push request."""

    message: PubsubMessage
    subscription: str


class ModerationResult(BaseModel):
    """Result of image moderation."""

    image_id: str
    status: str  # "approved", "rejected", "pending"
    safe_search: Optional[dict] = None
    rejection_reason: Optional[str] = None
    moved_to_bucket: Optional[str] = None
