"""Image processing utilities for resizing before Vision API analysis."""

import io
import logging
from typing import Tuple

from google.cloud import storage
from PIL import Image

logger = logging.getLogger(__name__)

# Maximum dimensions for Vision API processing
# Vision API accepts up to 10MB, but smaller = faster + cheaper
MAX_WIDTH = 1024
MAX_HEIGHT = 1024
JPEG_QUALITY = 85


class ImageProcessor:
    """Handles image resizing and optimization for Vision API."""

    def __init__(self):
        """Initialize GCS client for image operations."""
        self.storage_client = storage.Client()

    def resize_image_from_gcs(
        self, gcs_uri: str, max_width: int = MAX_WIDTH, max_height: int = MAX_HEIGHT
    ) -> bytes:
        """
        Download image from GCS, resize if needed, and return bytes.

        Args:
            gcs_uri: Full GCS URI (gs://bucket/blob)
            max_width: Maximum width in pixels
            max_height: Maximum height in pixels

        Returns:
            Resized image as bytes (JPEG format)

        Raises:
            ValueError: If GCS URI is invalid
            Exception: If download or processing fails
        """
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        # Parse GCS URI
        path_parts = gcs_uri[5:].split("/", 1)
        bucket_name = path_parts[0]
        blob_name = path_parts[1] if len(path_parts) > 1 else ""

        logger.info(f"Downloading image from {gcs_uri}")

        # Download image to memory
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        image_bytes = blob.download_as_bytes()

        original_size_kb = len(image_bytes) / 1024
        logger.info(f"Downloaded image: {original_size_kb:.2f} KB")

        # Open image with Pillow
        try:
            image = Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            logger.error(f"Failed to open image: {e}")
            raise

        original_width, original_height = image.size
        logger.info(f"Original dimensions: {original_width}x{original_height}")

        # Calculate resize dimensions (maintain aspect ratio)
        new_width, new_height = self._calculate_resize_dimensions(
            original_width, original_height, max_width, max_height
        )

        # Only resize if image is larger than max dimensions
        if new_width < original_width or new_height < original_height:
            logger.info(f"Resizing to: {new_width}x{new_height}")
            image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        else:
            logger.info("Image is within size limits, no resize needed")

        # Convert to RGB if needed (remove alpha channel for JPEG)
        if image.mode in ("RGBA", "LA", "P"):
            logger.info(f"Converting from {image.mode} to RGB")
            rgb_image = Image.new("RGB", image.size, (255, 255, 255))
            if image.mode == "P":
                image = image.convert("RGBA")
            rgb_image.paste(image, mask=image.split()[-1] if image.mode in ("RGBA", "LA") else None)
            image = rgb_image

        # Save to bytes buffer as JPEG
        output_buffer = io.BytesIO()
        image.save(output_buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        output_bytes = output_buffer.getvalue()

        resized_size_kb = len(output_bytes) / 1024
        reduction_percent = ((original_size_kb - resized_size_kb) / original_size_kb) * 100

        logger.info(
            f"Processed image: {resized_size_kb:.2f} KB "
            f"({reduction_percent:.1f}% reduction)"
        )

        return output_bytes

    def _calculate_resize_dimensions(
        self,
        original_width: int,
        original_height: int,
        max_width: int,
        max_height: int,
    ) -> Tuple[int, int]:
        """
        Calculate new dimensions maintaining aspect ratio.

        Args:
            original_width: Original image width
            original_height: Original image height
            max_width: Maximum allowed width
            max_height: Maximum allowed height

        Returns:
            Tuple of (new_width, new_height)
        """
        # Calculate aspect ratio
        aspect_ratio = original_width / original_height

        # Calculate dimensions constrained by width
        width_constrained_width = max_width
        width_constrained_height = int(max_width / aspect_ratio)

        # Calculate dimensions constrained by height
        height_constrained_height = max_height
        height_constrained_width = int(max_height * aspect_ratio)

        # Choose the constraint that results in smaller image
        if (
            width_constrained_width <= max_width
            and width_constrained_height <= max_height
        ):
            return width_constrained_width, width_constrained_height
        else:
            return height_constrained_width, height_constrained_height
