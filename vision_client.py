"""Vision API integration for image moderation."""

import base64
import json
import logging
from typing import Optional

from google.cloud import vision
from google.cloud.vision_v1 import types

logger = logging.getLogger(__name__)


class VisionClient:
    """Google Vision API client for image moderation."""

    def __init__(self, threshold: float = 0.7):
        """
        Initialize Vision API client.

        Args:
            threshold: SafeSearch probability threshold (0.0-1.0)
        """
        self.client = vision.ImageAnnotatorClient()
        self.threshold = threshold

    def analyze_image(self, gcs_uri: str) -> dict:
        """
        Analyze an image using Vision API SafeSearch.

        Args:
            gcs_uri: GCS URI of the image (gs://bucket/object.jpg)

        Returns:
            Dictionary with SafeSearch results and moderation decision
        """
        # Use GCS URI directly for Vision API
        image_source = types.ImageSource(gcs_image_uri=gcs_uri)
        image = types.Image(source=image_source)

        # Perform safe search detection
        response = self.client.safe_search_detection(image=image)
        safe_search = response.safe_search_annotation

        # Parse results
        result = {
            "adult": safe_search.adult.name,
            "violence": safe_search.violence.name,
            "racy": safe_search.racy.name,
            "spoof": safe_search.spoof.name,
            "medical": safe_search.medical.name,
        }

        # Determine if image should be rejected
        # Levels: UNKNOWN, VERY_UNLIKELY, UNLIKELY, POSSIBLE, LIKELY, VERY_LIKELY
        reject_levels = {"LIKELY", "VERY_LIKELY"}

        rejection_reason = None
        if safe_search.adult.name in reject_levels:
            rejection_reason = "adult_content"
        elif safe_search.violence.name in reject_levels:
            rejection_reason = "violence"
        elif safe_search.racy.name in reject_levels:
            rejection_reason = "racy"

        is_approved = rejection_reason is None

        return {
            "result": result,
            "approved": is_approved,
            "rejection_reason": rejection_reason,
            "confidence": {
                "adult": safe_search.adult,
                "violence": safe_search.violence,
                "racy": safe_search.racy,
            },
        }

    def detect_logos(self, gcs_uri: str) -> list[dict]:
        """
        Detect logos in an image.

        Args:
            gcs_uri: GCS URI of the image

        Returns:
            List of detected logos with description and confidence
        """
        image_source = types.ImageSource(gcs_image_uri=gcs_uri)
        image = types.Image(source=image_source)

        response = self.client.logo_detection(image=image)
        logos = response.logo_annotations

        return [
            {
                "description": logo.description,
                "confidence": logo.score,
                "bounding_poly": (
                    str(logo.bounding_poly) if logo.bounding_poly else None
                ),
            }
            for logo in logos
        ]

    def detect_labels(self, gcs_uri: str, max_results: int = 10) -> list[dict]:
        """
        Detect labels in an image.

        Args:
            gcs_uri: GCS URI of the image
            max_results: Maximum number of labels to return

        Returns:
            List of detected labels with description and confidence
        """
        image_source = types.ImageSource(gcs_image_uri=gcs_uri)
        image = types.Image(source=image_source)

        response = self.client.label_detection(image=image, max_results=max_results)
        labels = response.label_annotations

        return [
            {
                "description": label.description,
                "confidence": label.score,
            }
            for label in labels
        ]
