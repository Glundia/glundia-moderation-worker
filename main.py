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
from models import ModerationResult, PubsubPushRequest, UploadEvent
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
    is uploaded to the quarantine bucket.

    Args:
        request: Pub/Sub push request containing the upload event

    Returns:
        JSONResponse with processing result
    """
    try:
        # Decode the message data
        message_data = json.loads(base64.b64decode(request.message.data).decode())
        upload_event = UploadEvent(**message_data)

        logger.info(f"Processing image: {upload_event.image_id}")
        logger.info(f"GCS URI: {upload_event.gcs_uri}")

        # Step 1: Analyze image with Vision API
        vision_result = vision_client.analyze_image(upload_event.gcs_uri)

        logger.info(f"Vision API result: approved={vision_result['approved']}")

        # Step 2: Determine destination bucket and status
        if vision_result["approved"]:
            destination_bucket = settings.public_bucket
            status = "approved"
            rejection_reason = None
        else:
            destination_bucket = settings.rejected_bucket
            status = "rejected"
            rejection_reason = vision_result["rejection_reason"]

        # Step 3: Move image to appropriate bucket
        new_uri = storage_client.move_blob(
            source_uri=upload_event.gcs_uri,
            destination_bucket=destination_bucket,
        )

        logger.info(f"Moved image to: {new_uri}")

        # Step 4: Update database
        db_client.update_moderation_status(
            image_id=upload_event.image_id,
            status=status,
            rejection_reason=rejection_reason,
            safe_search_result=vision_result.get("result"),
        )

        # Step 5: Create result
        result = ModerationResult(
            image_id=upload_event.image_id,
            status=status,
            safe_search=vision_result.get("result"),
            rejection_reason=rejection_reason,
            moved_to_bucket=destination_bucket,
        )

        logger.info(f"Successfully processed image {upload_event.image_id}: {status}")

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
