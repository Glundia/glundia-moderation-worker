"""FastAPI application for image moderation worker."""

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse

from config import settings
from database_client import DatabaseClient
from image_processor import ImageProcessor
from image_moderator import ImageModerator, ModerationTask
from models import (
    GcsObjectMetadata,
    ImageType,
    ModerationResult,
    PubsubPushRequest,
    UploadEvent,
)
from storage_client import StorageClient
from vision_client import VisionClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global clients (initialized on startup)
vision_client: VisionClient = None
storage_client: StorageClient = None
db_client: DatabaseClient = None
image_processor: ImageProcessor = None
image_moderator: ImageModerator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global vision_client, storage_client, db_client, image_processor, image_moderator

    # Initialize clients
    logger.info("Initializing moderation worker clients...")
    vision_client = VisionClient(threshold=settings.vision_api_threshold)
    storage_client = StorageClient(project=settings.gcp_project)
    db_client = DatabaseClient(settings.database_url)
    image_processor = ImageProcessor(project=settings.gcp_project)

    # Initialize the parallel image moderator
    max_concurrent = getattr(settings, "max_concurrent_moderations", 10)
    image_moderator = ImageModerator(
        vision_client=vision_client,
        storage_client=storage_client,
        db_client=db_client,
        image_processor=image_processor,
        max_concurrent=max_concurrent,
    )

    logger.info(f"Worker configured for project: {settings.gcp_project}")
    logger.info(f"Subscribed to: {settings.pubsub_subscription}")
    logger.info(f"Max concurrent moderations: {max_concurrent}")
    logger.info(
        f"Buckets - Quarantine: {settings.quarantine_bucket}, "
        f"Public: {settings.public_bucket}, "
        f"Private: {settings.private_bucket}, "
        f"Rejected: {settings.rejected_bucket}"
    )

    yield

    # Cleanup
    logger.info("Shutting down moderation worker...")


app = FastAPI(
    title="Glundia Image Moderation Worker",
    description="Processes image upload events and performs moderation using Vision API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "glundia-moderation-worker",
        "version": "1.0.0",
        "status": "running",
        "environment": settings.environment,
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "project": settings.gcp_project,
    }


@app.post("/process-upload")
async def process_upload(request: PubsubPushRequest) -> JSONResponse:
    """
    Process an image upload event from Pub/Sub.

    This endpoint is called by Cloud Pub/Sub when a new image
    is uploaded to the quarantine bucket. Supports both:
    - GCS notification format (direct from GCS bucket)
    - Custom UploadEvent format (from API)

    Args:
        request: Pub/Sub push request containing the upload event

    Returns:
        JSONResponse with processing result
    """
    try:
        # Decode the message data
        message_data = json.loads(base64.b64decode(request.message.data).decode())

        # Parse message and create moderation task
        task = await _parse_moderation_task(message_data)

        # Process image using parallel moderator
        result = await image_moderator.moderate_image(task)

        # Check for errors
        if result.error:
            logger.error(f"Moderation failed for {result.image_id}: {result.error}")
            return JSONResponse(
                content={
                    "status": "error",
                    "message": result.error,
                },
                status_code=500,
            )

        # Create response
        moderation_result = ModerationResult(
            image_id=result.image_id,
            status=result.status,
            safe_search=result.vision_scores,
            rejection_reason=result.rejection_reason,
            moved_to_bucket=result.new_storage_path.split("/")[
                0
            ],  # Extract bucket name
        )

        logger.info(f"Successfully processed image {result.image_id}: {result.status}")

        return JSONResponse(
            content={
                "status": "success",
                "result": moderation_result.model_dump(),
            },
            status_code=200,
        )

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.exception(f"Error processing upload: {e}")
        # Return 200 to acknowledge receipt (Pub/Sub will retry on error)
        return JSONResponse(
            content={
                "status": "error",
                "message": str(e),
            },
            status_code=500,
        )


def _infer_image_type_from_path(gcs_path: str) -> ImageType:
    """
    Intelligently infer image type from GCS storage path.

    Path patterns:
    - raffles/{raffle_uuid}/{image_uuid}.jpg -> RAFFLE_IMAGE
    - prizes/{prize_uuid}/{image_uuid}.jpg -> PRIZE_IMAGE
    - profile_pictures/{user_uuid}.jpg -> PROFILE_PICTURE

    Args:
        gcs_path: GCS object path (e.g., "raffles/abc-123/img.jpg")

    Returns:
        Inferred ImageType (defaults to PRIZE_IMAGE if pattern unclear)
    """
    path_lower = gcs_path.lower()

    if "/raffles/" in path_lower or path_lower.startswith("raffles/"):
        logger.info(f"Inferred RAFFLE_IMAGE from path: {gcs_path}")
        return ImageType.RAFFLE_IMAGE
    elif "/prizes/" in path_lower or path_lower.startswith("prizes/"):
        logger.info(f"Inferred PRIZE_IMAGE from path: {gcs_path}")
        return ImageType.PRIZE_IMAGE
    elif "/profile_pictures/" in path_lower or path_lower.startswith(
        "profile_pictures/"
    ):
        logger.info(f"Inferred PROFILE_PICTURE from path: {gcs_path}")
        return ImageType.PROFILE_PICTURE
    else:
        logger.warning(
            f"Could not infer image type from path: {gcs_path}, defaulting to PRIZE_IMAGE"
        )
        return ImageType.PRIZE_IMAGE


async def _retry_database_lookup(
    image_uuid: str, max_retries: int = 5, initial_delay: float = 0.2
) -> Optional[dict]:
    """
    Retry database lookup with exponential backoff.

    This handles the race condition where Pub/Sub notifications arrive
    before database transactions commit.

    Args:
        image_uuid: UUID of the image to look up
        max_retries: Maximum number of retry attempts (default: 5)
        initial_delay: Initial delay in seconds (default: 0.2s)

    Returns:
        Dictionary with image details if found, None otherwise
    """
    delay = initial_delay

    for attempt in range(max_retries):
        try:
            db_record = db_client.get_image_by_uuid(image_uuid)
            if db_record:
                if attempt > 0:
                    logger.info(
                        f"Database lookup succeeded on attempt {attempt + 1}/{max_retries} "
                        f"after {delay:.2f}s total delay"
                    )
                return db_record

            # Record not found yet, wait before retry
            if attempt < max_retries - 1:
                logger.debug(
                    f"Image {image_uuid} not found, retrying in {delay:.2f}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff

        except Exception as db_error:
            logger.warning(
                f"Database lookup error on attempt {attempt + 1}/{max_retries}: {db_error}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2

    logger.error(
        f"Failed to find image {image_uuid} in database after {max_retries} attempts"
    )
    return None


async def _parse_moderation_task(message_data: dict) -> ModerationTask:
    """Parse Pub/Sub message and create a ModerationTask

    Supports both GCS notification format and legacy custom format.

    Args:
        message_data: Decoded Pub/Sub message data

    Returns:
        ModerationTask ready for processing
    """
    if "kind" in message_data and message_data.get("kind") == "storage#object":
        # GCS notification format (Production flow)
        gcs_metadata = GcsObjectMetadata(**message_data)
        gcs_uri = f"gs://{gcs_metadata.bucket}/{gcs_metadata.name}"

        logger.info(f"Processing GCS notification for: {gcs_uri}")

        # Extract image_uuid from filename (always present in GCS URI)
        filename = gcs_metadata.name.split("/")[-1]
        image_uuid = filename.rsplit(".", 1)[0] if "." in filename else filename

        logger.debug(f"Extracted image_uuid from filename: {image_uuid}")

        # Try to fetch blob custom metadata first
        image_type_str = None
        image_id = None
        uploaded_by = None

        try:
            blob_metadata = storage_client.get_blob_metadata(gcs_uri)
            custom_metadata = blob_metadata.get("metadata", {})

            image_type_str = custom_metadata.get("image_type")
            image_id = custom_metadata.get("image_id")
            uploaded_by = custom_metadata.get("user_id")

            if image_type_str:
                logger.debug(
                    f"Read from GCS metadata - image_type: {image_type_str}, image_id: {image_id}"
                )
        except Exception as metadata_error:
            logger.warning(f"Could not read GCS blob metadata: {metadata_error}")

        # If metadata is missing, look up in database using image_uuid with retry logic
        if not image_type_str or not image_id:
            logger.debug(
                f"Missing metadata, looking up image_uuid {image_uuid} in database"
            )
            db_record = await _retry_database_lookup(image_uuid, max_retries=5)
            if db_record:
                image_type_str = db_record["image_type"]
                image_id = db_record["id"]
                uploaded_by = db_record.get("user_id")
                logger.info(
                    f"Found in database - type: {image_type_str}, id: {image_id}"
                )
            else:
                logger.warning(
                    f"Image UUID {image_uuid} not found in database after retries"
                )

        # Convert image_type string to enum (with fallback)
        if image_type_str:
            try:
                image_type = ImageType(image_type_str)
            except ValueError:
                logger.warning(
                    f"Invalid image_type: {image_type_str}, falling back to path parsing"
                )
                image_type = _infer_image_type_from_path(gcs_metadata.name)
        else:
            # Infer image type from storage path as intelligent fallback
            logger.warning("No image_type found, inferring from storage path")
            image_type = _infer_image_type_from_path(gcs_metadata.name)

        # Final fallback for image_id
        if not image_id:
            logger.warning(
                f"No image_id found, using image_uuid as fallback: {image_uuid}"
            )
            image_id = image_uuid

    else:
        # Custom UploadEvent format (Legacy support for testing)
        logger.warning("Received custom UploadEvent format - this is deprecated")
        upload_event = UploadEvent(**message_data)
        gcs_uri = upload_event.gcs_uri
        image_id = upload_event.image_id
        uploaded_by = upload_event.uploaded_by
        image_type = upload_event.image_type

        # Extract image_uuid from GCS URI
        filename = gcs_uri.split("/")[-1]
        image_uuid = filename.rsplit(".", 1)[0] if "." in filename else filename

        logger.debug(
            f"Processing legacy upload event for image: {image_id}, type: {image_type}"
        )

    return ModerationTask(
        gcs_uri=gcs_uri,
        image_id=image_id,
        image_uuid=image_uuid,
        image_type=image_type,
        uploaded_by=uploaded_by,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
