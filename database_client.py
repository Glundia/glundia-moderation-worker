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
        image_type: str,
        status: str,
        rejection_reason: Optional[str] = None,
        safe_search_result: Optional[dict] = None,
        new_storage_path: Optional[str] = None,
    ) -> bool:
        """
        Update the moderation status of an image.

        Args:
            image_id: UUID of the image record (primary key)
            image_type: Type of image (profile_picture, raffle_image, prize_image)
            status: "approved", "rejected", or "pending"
            rejection_reason: Reason for rejection if applicable
            safe_search_result: SafeSearch API result (stored in vision_scores JSONB field)
            new_storage_path: New storage path after moving image (format: bucket_name/path/to/file.jpg)

        Returns:
            True if update was successful
        """
        # Determine table name
        table_map = {
            "profile_picture": "profile_pictures",
            "raffle_image": "raffle_images",
            "prize_image": "prize_images",
        }
        
        table_name = table_map.get(image_type)
        if not table_name:
            logger.error(f"Invalid image_type: {image_type}")
            return False
        
        # Note: vision_scores should be JSONB, rejection_reason goes to rejection_reason column
        # Update storage_path when new path is provided (after moving image between buckets)
        query = text(f"""
            UPDATE public.{table_name}
            SET moderation_status = :status,
                rejection_reason = :rejection_reason,
                vision_scores = :vision_scores::jsonb,
                storage_path = COALESCE(:new_storage_path, storage_path),
                moderated_at = NOW(),
                updated_at = NOW(),
                approved_at = CASE WHEN :status = 'approved' THEN NOW() ELSE approved_at END
            WHERE id::text = :image_id
        """)

        try:
            with self.engine.connect() as conn:
                # Convert safe_search_result dict to JSON string for JSONB column
                import json
                vision_scores_json = json.dumps(safe_search_result) if safe_search_result else None
                
                result = conn.execute(
                    query,
                    {
                        "status": status,
                        "rejection_reason": rejection_reason,
                        "vision_scores": vision_scores_json,
                        "new_storage_path": new_storage_path,
                        "image_id": image_id,
                    },
                )
                conn.commit()

            if result.rowcount == 0:
                logger.warning(f"Image {image_id} not found in {table_name}")
                return False

            logger.info(f"Updated image {image_id} in {table_name} to status {status}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update image {image_id}: {e}")
            return False

    def get_image_by_uuid(self, image_uuid: str) -> Optional[dict]:
        """
        Get image details from database using image_uuid.
        Searches across profile_pictures, raffle_images, and prize_images tables.

        Args:
            image_uuid: UUID of the image (from filename)

        Returns:
            Dictionary with image details including image_type, or None if not found
        """
        # Try profile_pictures first
        query = text("""
            SELECT id, user_id, image_uuid, moderation_status, 
                   storage_path, 'profile_picture' as image_type
            FROM public.profile_pictures
            WHERE image_uuid::text = :image_uuid
        """)
        
        with self.engine.connect() as conn:
            result = conn.execute(query, {"image_uuid": image_uuid})
            row = result.fetchone()
            
            if row:
                return {
                    "id": str(row.id),
                    "user_id": str(row.user_id),
                    "image_uuid": str(row.image_uuid),
                    "image_type": "profile_picture",
                    "moderation_status": row.moderation_status,
                    "storage_path": row.storage_path,
                }
            
            # Try raffle_images
            query = text("""
                SELECT id, raffle_id, image_uuid, moderation_status, 
                       storage_path, 'raffle_image' as image_type
                FROM public.raffle_images
                WHERE image_uuid::text = :image_uuid
            """)
            
            result = conn.execute(query, {"image_uuid": image_uuid})
            row = result.fetchone()
            
            if row:
                return {
                    "id": str(row.id),
                    "raffle_id": str(row.raffle_id),
                    "image_uuid": str(row.image_uuid),
                    "image_type": "raffle_image",
                    "moderation_status": row.moderation_status,
                    "storage_path": row.storage_path,
                }
            
            # Try prize_images
            query = text("""
                SELECT id, prize_id, image_uuid, moderation_status, 
                       storage_path, 'prize_image' as image_type
                FROM public.prize_images
                WHERE image_uuid::text = :image_uuid
            """)
            
            result = conn.execute(query, {"image_uuid": image_uuid})
            row = result.fetchone()
            
            if row:
                return {
                    "id": str(row.id),
                    "prize_id": str(row.prize_id),
                    "image_uuid": str(row.image_uuid),
                    "image_type": "prize_image",
                    "moderation_status": row.moderation_status,
                    "storage_path": row.storage_path,
                }
        
        logger.warning(f"Image with UUID {image_uuid} not found in any table")
        return None

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
