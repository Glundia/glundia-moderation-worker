"""FastAPI application for image moderation worker."""

import base64
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse

from config import settings
from database_client import DatabaseClient
from models import GcsObjectMetadata, ImageType, ModerationResult, PubsubPushRequest, UploadEvent
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global vision_client, storage_client, db_client

    # Initialize clients
    logger.info("Initializing moderation worker clients...")
    vision_client = VisionClient(threshold=settings.vision_api_threshold)
    storage_client = StorageClient()
    db_client = DatabaseClient(settings.database_url)

    logger.info(f"Worker configured for project: {settings.gcp_project}")
    logger.info(f"Subscribed to: {settings.pubsub_subscription}")
    logger.info(f"Buckets - Quarantine: {settings.quarantine_bucket}, "
                f"Public: {settings.public_bucket}, "
                f"Private: {settings.private_bucket}, "
                f"Rejected: {settings.rejected_bucket}")

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
        
        # Detect message format and extract relevant data
        image_type = None  # Will be set based on message format
        destination_path = None
        
        if "kind" in message_data and message_data.get("kind") == "storage#object":
            # GCS notification format (Production flow)
            # Metadata is read from the GCS blob custom metadata
            gcs_metadata = GcsObjectMetadata(**message_data)
            gcs_uri = f"gs://{gcs_metadata.bucket}/{gcs_metadata.name}"
            timestamp = gcs_metadata.timeCreated
            
            logger.info(f"Processing GCS notification for: {gcs_uri}")
            
            # Extract image_uuid from filename (always present in GCS URI)
            # Format: gs://bucket/path/to/IMAGE_UUID.ext
            filename = gcs_metadata.name.split("/")[-1]
            image_uuid = filename.rsplit(".", 1)[0] if "." in filename else filename
            
            logger.info(f"Extracted image_uuid from filename: {image_uuid}")
            
            # Try to fetch blob custom metadata first
            image_type_str = None
            image_id = None
            uploaded_by = None
            
            try:
                blob_metadata = storage_client.get_blob_metadata(gcs_uri)
                custom_metadata = blob_metadata.get("metadata", {})
                
                # Extract metadata fields set by API during upload (if available)
                image_type_str = custom_metadata.get("image_type")
                image_id = custom_metadata.get("image_id")
                uploaded_by = custom_metadata.get("user_id")
                
                if image_type_str:
                    logger.info(f"Read from GCS metadata - image_type: {image_type_str}, image_id: {image_id}")
                
            except Exception as metadata_error:
                logger.warning(f"Could not read GCS blob metadata: {metadata_error}")
            
            # If metadata is missing, look up in database using image_uuid
            if not image_type_str or not image_id:
                logger.info(f"Missing metadata, looking up image_uuid {image_uuid} in database")
                try:
                    db_record = db_client.get_image_by_uuid(image_uuid)
                    if db_record:
                        image_type_str = db_record["image_type"]
                        image_id = db_record["id"]
                        uploaded_by = db_record.get("user_id")
                        logger.info(f"Found in database - type: {image_type_str}, id: {image_id}")
                    else:
                        logger.warning(f"Image UUID {image_uuid} not found in database")
                except Exception as db_error:
                    logger.warning(f"Database lookup failed: {db_error}")
            
            # Convert image_type string to enum (with fallback)
            if image_type_str:
                try:
                    image_type = ImageType(image_type_str)
                except ValueError:
                    logger.warning(f"Invalid image_type: {image_type_str}, defaulting to prize_image")
                    image_type = ImageType.PRIZE_IMAGE
            else:
                logger.warning("No image_type found, defaulting to prize_image")
                image_type = ImageType.PRIZE_IMAGE
            
            # Final fallback for image_id
            if not image_id:
                logger.warning(f"No image_id found, using image_uuid as fallback: {image_uuid}")
                image_id = image_uuid
            
        else:
            # Custom UploadEvent format (Legacy support for testing)
            # This path is kept for backward compatibility but should not be used in production
            logger.warning("Received custom UploadEvent format - this is deprecated, use GCS metadata instead")
            upload_event = UploadEvent(**message_data)
            gcs_uri = upload_event.gcs_uri
            image_id = upload_event.image_id
            uploaded_by = upload_event.uploaded_by
            timestamp = upload_event.timestamp
            image_type = upload_event.image_type
            destination_path = upload_event.destination_path
            
            logger.info(f"Processing legacy upload event for image: {image_id}, type: {image_type}")

        logger.info(f"GCS URI: {gcs_uri}")

        # Step 1: Analyze image with Vision API
        vision_result = vision_client.analyze_image(gcs_uri)

        logger.info(f"Vision API result: approved={vision_result['approved']}")

        # Step 2: Determine destination bucket, path, and status based on image_type and moderation result
        if vision_result["approved"]:
            status = "approved"
            rejection_reason = None
            
            # Route to correct bucket based on image_type
            if image_type == ImageType.PROFILE_PICTURE:
                destination_bucket = settings.private_bucket
                # Profile pictures go to private bucket (flat structure)
                # Extract filename from source URI
                filename = gcs_uri.split("/")[-1]
                destination_blob_name = filename
            elif image_type == ImageType.PRIZE_IMAGE:
                destination_bucket = settings.public_bucket
                # Prize images go to public bucket under prize_images/
                filename = gcs_uri.split("/")[-1]
                destination_blob_name = f"prize_images/{filename}"
            elif image_type == ImageType.RAFFLE_IMAGE:
                destination_bucket = settings.public_bucket
                # Raffle images go to public bucket under raffle_images/
                filename = gcs_uri.split("/")[-1]
                destination_blob_name = f"raffle_images/{filename}"
            else:
                # Fallback to public bucket if type unknown
                logger.warning(f"Unknown image_type: {image_type}, defaulting to public bucket")
                destination_bucket = settings.public_bucket
                filename = gcs_uri.split("/")[-1]
                destination_blob_name = filename
                
            logger.info(f"Approved image will move to: gs://{destination_bucket}/{destination_blob_name}")
        else:
            destination_bucket = settings.rejected_bucket
            status = "rejected"
            rejection_reason = vision_result["rejection_reason"]
            
            # Organize rejected images by type
            filename = gcs_uri.split("/")[-1]
            if image_type == ImageType.PROFILE_PICTURE:
                destination_blob_name = f"profile_pictures/{filename}"
            elif image_type == ImageType.PRIZE_IMAGE:
                destination_blob_name = f"prize_images/{filename}"
            elif image_type == ImageType.RAFFLE_IMAGE:
                destination_blob_name = f"raffle_images/{filename}"
            else:
                destination_blob_name = filename
                
            logger.info(f"Rejected image will move to: gs://{destination_bucket}/{destination_blob_name}")

        # Step 3: Move image to appropriate bucket with correct path
        new_uri = storage_client.move_blob(
            source_uri=gcs_uri,
            destination_bucket=destination_bucket,
            destination_blob_name=destination_blob_name,
        )

        logger.info(f"Moved image to: {new_uri}")

        # Step 4: Update database (only if image_id and image_type are valid)
        if image_id and image_type and len(image_id) > 10:  # Basic check for UUID-like format
            try:
                # Convert ImageType enum to string for database client
                image_type_str = image_type.value
                
                db_client.update_moderation_status(
                    image_id=image_id,
                    image_type=image_type_str,
                    status=status,
                    rejection_reason=rejection_reason,
                    safe_search_result=vision_result.get("result"),
                )
                logger.info(f"Updated database for image {image_id} ({image_type_str})")
            except Exception as db_error:
                logger.warning(f"Could not update database: {db_error}")

        # Step 5: Create result
        result = ModerationResult(
            image_id=image_id or "unknown",
            status=status,
            safe_search=vision_result.get("result"),
            rejection_reason=rejection_reason,
            moved_to_bucket=destination_bucket,
        )

        logger.info(f"Successfully processed image {image_id}: {status}")

        return JSONResponse(
            content={
                "status": "success",
                "result": result.model_dump(),
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
