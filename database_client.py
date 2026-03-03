"""Database operations for updating image moderation status."""

import logging
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)


class DatabaseClient:
    """Database client for updating image moderation status."""

    def __init__(self, database_url: str):
        """
        Initialize database client.

        Args:
            database_url: PostgreSQL connection string
        """
        self.engine: Engine = create_engine(
            database_url,
            poolclass=NullPool,  # Cloud Run manages connections
        )

    def update_moderation_status(
        self,
        image_id: str,
        status: str,
        rejection_reason: Optional[str] = None,
        safe_search_result: Optional[dict] = None,
    ) -> bool:
        """
        Update the moderation status of an image.

        Args:
            image_id: UUID of the image
            status: "approved", "rejected", or "pending"
            rejection_reason: Reason for rejection if applicable
            safe_search_result: SafeSearch API result if applicable

        Returns:
            True if update was successful
        """
        query = text("""
            UPDATE images
            SET moderation_status = :status,
                moderation_rejection_reason = :rejection_reason,
                moderation_safe_search = :safe_search,
                moderated_at = NOW()
            WHERE id = :image_id
        """)

        with self.engine.connect() as conn:
            result = conn.execute(
                query,
                {
                    "status": status,
                    "rejection_reason": rejection_reason,
                    "safe_search": str(safe_search_result) if safe_search_result else None,
                    "image_id": image_id,
                },
            )
            conn.commit()

        if result.rowcount == 0:
            logger.warning(f"Image {image_id} not found in database")
            return False

        logger.info(f"Updated image {image_id} status to {status}")
        return True

    def get_image(self, image_id: str) -> Optional[dict]:
        """
        Get image details from database.

        Args:
            image_id: UUID of the image

        Returns:
            Dictionary with image details or None if not found
        """
        query = text("""
            SELECT id, user_id, gcs_uri, moderation_status, created_at
            FROM images
            WHERE id = :image_id
        """)

        with self.engine.connect() as conn:
            result = conn.execute(query, {"image_id": image_id})
            row = result.fetchone()

        if row is None:
            return None

        return {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "gcs_uri": row.gcs_uri,
            "moderation_status": row.moderation_status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def check_duplicate_hash(self, perceptual_hash: str) -> Optional[str]:
        """
        Check if a perceptual hash already exists in blocked_hashes.

        Args:
            perceptual_hash: The pHash value to check

        Returns:
            Image ID if duplicate found, None otherwise
        """
        query = text("""
            SELECT image_id
            FROM blocked_hashes
            WHERE hash_value = :hash
            LIMIT 1
        """)

        with self.engine.connect() as conn:
            result = conn.execute(query, {"hash": perceptual_hash})
            row = result.fetchone()

        return str(row.image_id) if row else None

    def add_blocked_hash(
        self,
        image_id: str,
        hash_value: str,
        hash_type: str = "perceptual",
    ) -> bool:
        """
        Add a blocked hash to the database.

        Args:
            image_id: UUID of the image that created this hash
            hash_value: The hash value
            hash_type: Type of hash (e.g., "perceptual", "pdq")

        Returns:
            True if successful
        """
        query = text("""
            INSERT INTO blocked_hashes (image_id, hash_value, hash_type)
            VALUES (:image_id, :hash_value, :hash_type)
        """)

        with self.engine.connect() as conn:
            conn.execute(
                query,
                {
                    "image_id": image_id,
                    "hash_value": hash_value,
                    "hash_type": hash_type,
                },
            )
            conn.commit()

        logger.info(f"Added blocked hash for image {image_id}")
        return True
