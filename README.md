# Moderation Worker Service

FastAPI-based service for processing image moderation events from Pub/Sub.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
uvicorn main:app --reload --host 0.0.0.0 --port 8080

# Or with Docker
docker build -t glundia-moderation-worker .
docker run -p 8080:8080 --env-file .env glundia-moderation-worker
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GCP_PROJECT` | Yes | GCP project ID |
| `PUBSUB_SUBSCRIPTION` | Yes | Pub/Sub subscription name |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `QUARANTINE_BUCKET` | Yes | GCS bucket for untrusted uploads (flat structure) |
| `PUBLIC_BUCKET` | Yes | GCS bucket for approved images (prize_images/, raffle_images/) |
| `PRIVATE_BUCKET` | Yes | GCS bucket for private images (profile_pictures/ - flat) |
| `REJECTED_BUCKET` | Yes | GCS bucket for rejected images (organized by type) |
| `VISION_API_THRESHOLD` | No | SafeSearch threshold (default: 0.7) |
| `ENVIRONMENT` | No | dev/staging/prod |

## Pub/Sub Message Format

```json
{
  "message": {
    "data": "base64_encoded_json",
    "messageId": "uuid"
  },
  "subscription": "projects/{project}/subscriptions/{subscription}"
}
```

The `data` field should contain:
```json
{
  "image_id": "uuid",
  "gcs_uri": "gs://bucket/object.jpg",
  "uploaded_by": "user_uuid",
  "timestamp": "2026-01-01T00:00:00Z",
  "image_type": "profile_picture|prize_image|raffle_image",
  "destination_path": "optional/custom/path"
}
```

## Image Type Routing

The service routes images based on `image_type`:

**Approved Images:**
- `profile_picture` → `PRIVATE_BUCKET/` (flat structure)
- `prize_image` → `PUBLIC_BUCKET/prize_images/`
- `raffle_image` → `PUBLIC_BUCKET/raffle_images/`

**Rejected Images:**
- `profile_picture` → `REJECTED_BUCKET/profile_pictures/`
- `prize_image` → `REJECTED_BUCKET/prize_images/`
- `raffle_image` → `REJECTED_BUCKET/raffle_images/`

## Development

```bash
# Run tests
pytest

# Run with docker-compose
docker-compose up
```

## Endpoints

- `GET /health` - Health check
- `POST /process-upload` - Pub/Sub push endpoint
- `GET /` - Service info
