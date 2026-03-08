"""Image moderation orchestrator with parallel processing support"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

from config import settings
from database_client import DatabaseClient
from image_processor import ImageProcessor
from storage_client import StorageClient
from vision_client import VisionClient
from models import ImageType

logger = logging.getLogger(__name__)


@dataclass
class ModerationTask:
    """Represents a single image moderation task"""
    gcs_uri: str
    image_id: str
    image_uuid: str
    image_type: ImageType
    uploaded_by: Optional[str] = None


@dataclass
class ModerationTaskResult:
    """Result of a moderation task"""
    image_id: str
    status: str  # 'approved' or 'rejected'
    new_uri: str
    new_storage_path: str
    rejection_reason: Optional[str] = None
    vision_scores: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ImageModerator:
    """Orchestrates parallel image moderation processing"""
    
    def __init__(
        self,
        vision_client: VisionClient,
        storage_client: StorageClient,
        db_client: DatabaseClient,
        image_processor: ImageProcessor,
        max_concurrent: int = 10
    ):
        """Initialize the image moderator
        
        Args:
            vision_client: Client for Vision API
            storage_client: Client for GCS operations
            db_client: Client for database operations
            image_processor: Client for image processing
            max_concurrent: Maximum number of concurrent moderation tasks
        """
        self.vision_client = vision_client
        self.storage_client = storage_client
        self.db_client = db_client
        self.image_processor = image_processor
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        logger.info(f"ImageModerator initialized with max_concurrent={max_concurrent}")
    
    async def moderate_image(self, task: ModerationTask) -> ModerationTaskResult:
        """Moderate a single image with concurrency control
        
        Args:
            task: Moderation task to process
            
        Returns:
            ModerationTaskResult with processing outcome
        """
        async with self.semaphore:
            return await self._moderate_image_internal(task)
    
    async def _moderate_image_internal(self, task: ModerationTask) -> ModerationTaskResult:
        """Internal method to moderate a single image
        
        This is the core moderation logic extracted from main.py
        """
        try:
            logger.info(f"Starting moderation for image {task.image_id} ({task.image_type.value})")
            start_time = datetime.now()
            
            # Step 1: Download and resize image for Vision API analysis
            logger.debug(f"Downloading and resizing image: {task.gcs_uri}")
            try:
                resized_image_bytes = self.image_processor.resize_image_from_gcs(task.gcs_uri)
                vision_result = self.vision_client.analyze_image_from_bytes(resized_image_bytes)
            except Exception as resize_error:
                logger.warning(f"Failed to resize image {task.image_id}: {resize_error}")
                # Fallback to direct Vision API call if resize fails
                vision_result = self.vision_client.analyze_image(task.gcs_uri)
            
            logger.debug(f"Vision API result for {task.image_id}: approved={vision_result['approved']}")
            
            # Step 2: Determine destination bucket and path based on moderation result
            if vision_result["approved"]:
                status = "approved"
                rejection_reason = None
                
                # Route to correct bucket based on image_type
                if task.image_type == ImageType.PROFILE_PICTURE:
                    destination_bucket = settings.private_bucket
                    filename = task.gcs_uri.split("/")[-1]
                    destination_blob_name = filename
                elif task.image_type == ImageType.PRIZE_IMAGE:
                    destination_bucket = settings.public_bucket
                    filename = task.gcs_uri.split("/")[-1]
                    destination_blob_name = f"prize_images/{filename}"
                elif task.image_type == ImageType.RAFFLE_IMAGE:
                    destination_bucket = settings.public_bucket
                    filename = task.gcs_uri.split("/")[-1]
                    destination_blob_name = f"raffle_images/{filename}"
                else:
                    # Fallback to public bucket if type unknown
                    logger.warning(f"Unknown image_type: {task.image_type}, defaulting to public bucket")
                    destination_bucket = settings.public_bucket
                    filename = task.gcs_uri.split("/")[-1]
                    destination_blob_name = filename
                    
                logger.debug(f"Approved image {task.image_id} will move to: gs://{destination_bucket}/{destination_blob_name}")
            else:
                destination_bucket = settings.rejected_bucket
                status = "rejected"
                rejection_reason = vision_result["rejection_reason"]
                
                # Organize rejected images by type
                filename = task.gcs_uri.split("/")[-1]
                if task.image_type == ImageType.PROFILE_PICTURE:
                    destination_blob_name = f"profile_pictures/{filename}"
                elif task.image_type == ImageType.PRIZE_IMAGE:
                    destination_blob_name = f"prize_images/{filename}"
                elif task.image_type == ImageType.RAFFLE_IMAGE:
                    destination_blob_name = f"raffle_images/{filename}"
                else:
                    destination_blob_name = filename
                    
                logger.debug(f"Rejected image {task.image_id} will move to: gs://{destination_bucket}/{destination_blob_name}")
            
            # Step 3: Move image to appropriate bucket
            new_uri = self.storage_client.move_blob(
                source_uri=task.gcs_uri,
                destination_bucket=destination_bucket,
                destination_blob_name=destination_blob_name,
            )
            
            logger.debug(f"Moved image {task.image_id} to: {new_uri}")
            
            # Extract new storage path from GCS URI (format: gs://bucket/path -> bucket/path)
            new_storage_path = new_uri.replace("gs://", "")
            
            # Step 4: Update database
            try:
                self.db_client.update_moderation_status(
                    image_id=task.image_id,
                    image_type=task.image_type.value,
                    status=status,
                    rejection_reason=rejection_reason,
                    safe_search_result=vision_result.get("result"),
                    new_storage_path=new_storage_path,
                )
                logger.debug(f"Updated database for image {task.image_id}")
            except Exception as db_error:
                logger.error(f"Database update failed for {task.image_id}: {db_error}")
                # Don't fail the entire task - image is already moved
            
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"Completed moderation for {task.image_id}: {status} "
                f"(elapsed: {elapsed:.2f}s)"
            )
            
            return ModerationTaskResult(
                image_id=task.image_id,
                status=status,
                new_uri=new_uri,
                new_storage_path=new_storage_path,
                rejection_reason=rejection_reason,
                vision_scores=vision_result.get("result"),
            )
            
        except Exception as e:
            logger.exception(f"Error moderating image {task.image_id}: {e}")
            return ModerationTaskResult(
                image_id=task.image_id,
                status="error",
                new_uri="",
                new_storage_path="",
                error=str(e),
            )
    
    async def moderate_images_batch(self, tasks: List[ModerationTask]) -> List[ModerationTaskResult]:
        """Moderate multiple images in parallel
        
        Args:
            tasks: List of moderation tasks to process
            
        Returns:
            List of ModerationTaskResult objects (same order as input)
        """
        if not tasks:
            return []
        
        logger.info(f"Starting batch moderation for {len(tasks)} images")
        start_time = datetime.now()
        
        # Create tasks for parallel execution
        moderation_tasks = [self.moderate_image(task) for task in tasks]
        
        # Execute all tasks in parallel (with semaphore-controlled concurrency)
        results = await asyncio.gather(*moderation_tasks, return_exceptions=True)
        
        # Convert exceptions to error results
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Task {i} raised exception: {result}")
                final_results.append(ModerationTaskResult(
                    image_id=tasks[i].image_id,
                    status="error",
                    new_uri="",
                    new_storage_path="",
                    error=str(result),
                ))
            else:
                final_results.append(result)
        
        # Calculate statistics
        elapsed = (datetime.now() - start_time).total_seconds()
        approved = sum(1 for r in final_results if r.status == "approved")
        rejected = sum(1 for r in final_results if r.status == "rejected")
        errors = sum(1 for r in final_results if r.status == "error")
        
        logger.info(
            f"Batch moderation complete: {len(tasks)} images in {elapsed:.2f}s "
            f"(approved={approved}, rejected={rejected}, errors={errors})"
        )
        
        return final_results
