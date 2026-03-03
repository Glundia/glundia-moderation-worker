"""Pub/Sub message models."""

from typing import Optional
from pydantic import BaseModel, Field


class UploadEvent(BaseModel):
    """Image upload event data."""

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
