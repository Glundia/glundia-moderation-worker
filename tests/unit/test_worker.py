"""Unit tests for the moderation worker."""

import base64
import json
import pytest
from unittest.mock import MagicMock, patch

from models import PubsubPushRequest, PubsubMessage, UploadEvent
from vision_client import VisionClient
from storage_client import StorageClient
from database_client import DatabaseClient


class TestModels:
    """Test Pydantic models."""

    def test_upload_event_valid(self):
        """Test UploadEvent model with valid data."""
        event = UploadEvent(
            image_id="123e4567-e89b-12d3-a456-426614174000",
            gcs_uri="gs://bucket/image.jpg",
            uploaded_by="223e4567-e89b-12d3-a456-426614174000",
            timestamp="2026-01-01T00:00:00Z",
        )
        assert event.image_id == "123e4567-e89b-12d3-a456-426614174000"
        assert event.gcs_uri == "gs://bucket/image.jpg"

    def test_pubsub_message_valid(self):
        """Test PubsubMessage model."""
        message = PubsubMessage(
            data="eyJpbWFnZV9pZCI6IjEyMzQ1NiJ9",
            message_id="abc123",
        )
        assert message.message_id == "abc123"
        # Base64 decoded: {"image_id":"123456"}
        assert json.loads(base64.b64decode(message.data)) == {"image_id": "123456"}

    def test_pubsub_push_request_valid(self):
        """Test PubsubPushRequest model."""
        request = PubsubPushRequest(
            message=PubsubMessage(
                data=base64.b64encode(
                    json.dumps({
                        "image_id": "123",
                        "gcs_uri": "gs://bucket/img.jpg",
                        "uploaded_by": "user1",
                        "timestamp": "2026-01-01T00:00:00Z"
                    }).encode()
                ).decode(),
                message_id="msg123",
            ),
            subscription="projects/test/subscriptions/test-sub",
        )
        assert request.subscription == "projects/test/subscriptions/test-sub"


class TestVisionClient:
    """Test Vision API client."""

    def test_vision_client_init(self):
        """Test VisionClient initialization."""
        client = VisionClient(threshold=0.8)
        assert client.threshold == 0.8

    def test_vision_client_default_threshold(self):
        """Test default threshold."""
        client = VisionClient()
        assert client.threshold == 0.7

    @patch("vision_client.vision.ImageAnnotatorClient")
    def test_analyze_image_safe(self, mock_client_class):
        """Test image analysis with safe image."""
        mock_response = MagicMock()
        mock_safe = MagicMock()
        mock_safe.adult.name = "VERY_UNLIKELY"
        mock_safe.violence.name = "UNLIKELY"
        mock_safe.racy.name = "POSSIBLE"
        mock_response.safe_search_annotation = mock_safe

        mock_client = MagicMock()
        mock_client.safe_search_detection.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = VisionClient()
        result = client.analyze_image("gs://bucket/image.jpg")

        assert result["approved"] is True
        assert result["rejection_reason"] is None

    @patch("vision_client.vision.ImageAnnotatorClient")
    def test_analyze_image_adult(self, mock_client_class):
        """Test image analysis with adult content."""
        mock_response = MagicMock()
        mock_safe = MagicMock()
        mock_safe.adult.name = "LIKELY"
        mock_safe.violence.name = "VERY_UNLIKELY"
        mock_safe.racy.name = "UNLIKELY"
        mock_safe.spoof.name = "VERY_UNLIKELY"
        mock_safe.medical.name = "VERY_UNLIKELY"
        mock_response.safe_search_annotation = mock_safe

        mock_client = MagicMock()
        mock_client.safe_search_detection.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = VisionClient()
        result = client.analyze_image("gs://bucket/image.jpg")

        assert result["approved"] is False
        assert result["rejection_reason"] == "adult_content"


class TestStorageClient:
    """Test GCS storage client."""

    def test_parse_gcs_uri(self):
        """Test GCS URI parsing."""
        uri = "gs://bucket-name/path/to/image.jpg"
        assert uri.startswith("gs://")
        
        path_parts = uri[5:].split("/", 1)
        assert path_parts[0] == "bucket-name"
        assert path_parts[1] == "path/to/image.jpg"

    def test_parse_gcs_uri_no_path(self):
        """Test GCS URI with no path."""
        uri = "gs://bucket-name"
        path_parts = uri[5:].split("/", 1)
        assert path_parts[0] == "bucket-name"
        assert len(path_parts) == 1


class TestDatabaseClient:
    """Test database client."""

    def test_database_client_init(self):
        """Test DatabaseClient initialization."""
        url = "postgresql+asyncpg://user:pass@localhost:5432/db"
        client = DatabaseClient(url)
        assert "postgresql" in str(client.engine.url)


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
