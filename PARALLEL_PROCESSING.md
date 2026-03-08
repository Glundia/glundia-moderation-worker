# Parallel Image Moderation Processing

## Overview

This implementation provides high-performance parallel processing of image moderation tasks using asyncio and semaphore-controlled concurrency.

## Architecture Changes

### Before (Sequential Processing)
Each Pub/Sub message processed one image at a time:

```
Message 1 → Process Image 1 (10s) → Done
Message 2 → Process Image 2 (10s) → Done
Message 3 → Process Image 3 (10s) → Done

Total time for 5 images: 50+ seconds
```

### After (Parallel Processing with Concurrency Control)
Multiple images processed simultaneously with intelligent limits:

```
Messages 1-5 arrive → Process 5 images in parallel (10s) → All done

Total time for 5 images: ~10 seconds (5× faster)
```

## Performance Improvements

| Scenario | Before | After | Improvement |
|----------|--------|-------|-------------|
| **1 image** | ~10s | ~10s | Same (baseline) |
| **5 images** | ~50s | ~10s | **5× faster** |
| **10 images** | ~100s | ~20s | **5× faster** |
| **20 images** | ~200s | ~40s | **5× faster** |

*Note: Times assume max_concurrent=10. Actual performance depends on Vision API latency and network conditions.*

## Key Features

### 1. Semaphore-Controlled Concurrency
Prevents overwhelming system resources:

```python
# Initialize with max 10 concurrent tasks
semaphore = asyncio.Semaphore(max_concurrent=10)

# Each task acquires semaphore before processing
async with semaphore:
    await process_image()
```

### 2. Isolated Error Handling
One image failure doesn't affect others:

```python
# Process all images, capture exceptions
results = await asyncio.gather(*tasks, return_exceptions=True)

# Result: 4 succeeded, 1 failed (others unaffected)
```

### 3. Detailed Performance Logging
Track performance per image and per batch:

```
INFO - Starting moderation for image abc123 (raffle_image)
INFO - Completed moderation for abc123: approved (elapsed: 8.32s)
INFO - Batch moderation complete: 5 images in 10.15s (approved=4, rejected=1, errors=0)
```

### 4. Configurable Concurrency Limits
Tune performance vs. resource usage:

```env
# .env
MAX_CONCURRENT_MODERATIONS=10  # Default: 10
```

## New Components

### ImageModerator Class

Central orchestrator for parallel moderation:

```python
class ImageModerator:
    def __init__(
        self,
        vision_client: VisionClient,
        storage_client: StorageClient,
        db_client: DatabaseClient,
        image_processor: ImageProcessor,
        max_concurrent: int = 10
    ):
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def moderate_image(self, task: ModerationTask) -> ModerationTaskResult:
        """Moderate single image with concurrency control"""
        async with self.semaphore:
            return await self._moderate_image_internal(task)
    
    async def moderate_images_batch(
        self, 
        tasks: List[ModerationTask]
    ) -> List[ModerationTaskResult]:
        """Moderate multiple images in parallel"""
        moderation_tasks = [self.moderate_image(task) for task in tasks]
        return await asyncio.gather(*moderation_tasks, return_exceptions=True)
```

### ModerationTask Dataclass

Clean task representation:

```python
@dataclass
class ModerationTask:
    gcs_uri: str
    image_id: str
    image_uuid: str
    image_type: ImageType
    uploaded_by: Optional[str] = None
```

### ModerationTaskResult Dataclass

Structured result with error support:

```python
@dataclass
class ModerationTaskResult:
    image_id: str
    status: str  # 'approved', 'rejected', or 'error'
    new_uri: str
    new_storage_path: str
    rejection_reason: Optional[str] = None
    vision_scores: Optional[Dict[str, Any]] = None
    error: Optional[str] = None  # Set if processing failed
```

## Workflow

### 1. Receive Pub/Sub Message
```python
@app.post("/process-upload")
async def process_upload(request: PubsubPushRequest):
    message_data = json.loads(base64.b64decode(request.message.data))
```

### 2. Parse Task
```python
task = await _parse_moderation_task(message_data)
# Returns: ModerationTask with all required fields
```

### 3. Process with Moderator
```python
result = await image_moderator.moderate_image(task)
# Handles: Vision API, GCS move, DB update, error handling
```

### 4. Return Result
```python
if result.error:
    return JSONResponse({"status": "error", "message": result.error}, status_code=500)
else:
    return JSONResponse({"status": "success", "result": {...}}, status_code=200)
```

## Configuration

### Environment Variables

```env
# Required (existing)
GCP_PROJECT=glundia-dev
DATABASE_URL=postgresql://...
QUARANTINE_BUCKET=glundia-dev-uploads-quarantine
PUBLIC_BUCKET=glundia-dev-images-public
PRIVATE_BUCKET=glundia-dev-images-private
REJECTED_BUCKET=glundia-dev-images-rejected

# New (optional)
MAX_CONCURRENT_MODERATIONS=10  # Default: 10
```

### Tuning Concurrency

**Low Concurrency (1-5):**
- Lower resource usage
- Better for cost-sensitive workloads
- Slower throughput

**Medium Concurrency (10-20):**
- **Recommended for production**
- Good balance of speed and resources
- Handles typical upload bursts well

**High Concurrency (30+):**
- Maximum throughput
- Higher Vision API costs
- Risk of rate limiting

## Testing

### Unit Tests
```bash
cd glundia-moderation-worker
pytest tests/
```

### Integration Tests (Local)
```bash
# Start dependencies
docker-compose up postgres pubsub-emulator

# Run worker
python main.py

# Send test message
curl -X POST http://localhost:8080/process-upload \
  -H "Content-Type: application/json" \
  -d @sample-message.json
```

### Load Testing
```bash
# Send 10 messages simultaneously
for i in {1..10}; do
  curl -X POST http://localhost:8080/process-upload \
    -H "Content-Type: application/json" \
    -d @sample-message-$i.json &
done
wait

# Check logs for batch processing
```

### Production Verification

```bash
# Check recent moderation logs
gcloud logging read 'resource.labels.service_name="moderation-worker"
  AND jsonPayload.message=~"Batch moderation complete"' \
  --project=glundia-dev --limit=10 --freshness=1h

# Expected output:
# Batch moderation complete: 5 images in 10.15s (approved=4, rejected=1, errors=0)

# Check individual image logs
gcloud logging read 'resource.labels.service_name="moderation-worker"
  AND jsonPayload.message=~"Completed moderation"' \
  --project=glundia-dev --limit=20 --freshness=1h
```

## Monitoring

### Key Metrics

1. **Processing Time**
   - Per image: Target <15s
   - Per batch: Should scale sub-linearly with batch size

2. **Concurrency**
   - Check how many images processed simultaneously
   - Tune MAX_CONCURRENT_MODERATIONS if needed

3. **Error Rate**
   - Should remain at 0% for technical errors
   - Rejection rate depends on content quality

4. **Vision API Quota**
   - Monitor usage in GCP Console
   - Parallel processing increases request rate

### Log Messages

**Startup:**
```
INFO - ImageModerator initialized with max_concurrent=10
```

**Per Image:**
```
INFO - Starting moderation for image abc123 (raffle_image)
DEBUG - Downloading and resizing image: gs://...
DEBUG - Vision API result for abc123: approved=True
DEBUG - Approved image abc123 will move to: gs://glundia-dev-images-public/raffle_images/...
DEBUG - Moved image abc123 to: gs://...
DEBUG - Updated database for image abc123
INFO - Completed moderation for abc123: approved (elapsed: 8.32s)
```

**Per Batch:**
```
INFO - Batch moderation complete: 5 images in 10.15s (approved=4, rejected=1, errors=0)
```

**Errors:**
```
ERROR - Error moderating image abc123: Vision API timeout
ERROR - Task 2 raised exception: ConnectionError
```

## Troubleshooting

### High Latency
```bash
# Check Vision API performance
# Look for "elapsed:" in logs
gcloud logging read 'resource.labels.service_name="moderation-worker"
  AND jsonPayload.message=~"elapsed:"' \
  --project=glundia-dev --limit=50

# If Vision API is slow:
# 1. Check GCP status page
# 2. Verify image resize is working (should process resized images)
# 3. Check network latency from Cloud Run
```

### Concurrent Limit Too High
```bash
# Symptoms: OOM errors, Cloud Run instance crashes

# Solution: Lower MAX_CONCURRENT_MODERATIONS
gcloud run services update moderation-worker \
  --region=us-central1 \
  --update-env-vars MAX_CONCURRENT_MODERATIONS=5
```

### Concurrent Limit Too Low
```bash
# Symptoms: High latency, messages backing up in Pub/Sub

# Solution: Increase MAX_CONCURRENT_MODERATIONS
gcloud run services update moderation-worker \
  --region=us-central1 \
  --update-env-vars MAX_CONCURRENT_MODERATIONS=20
```

## Rollback Plan

If issues arise, revert to sequential processing:

```bash
cd glundia-moderation-worker
git revert 7a20043
git push origin develop

# GitHub Actions will auto-deploy
```

## Future Enhancements

1. **Dynamic Concurrency** - Adjust based on system load
2. **Priority Queues** - Process profile pictures before raffle images
3. **Caching** - Cache Vision API results for duplicate images
4. **Metrics Export** - Export processing metrics to Cloud Monitoring
5. **Batch Pub/Sub Delivery** - Configure subscription for batch delivery

## Related Changes

- **API Publishing**: See `glundia-api/PARALLEL_MODERATION.md`
- **Database Schema**: No changes required
- **Infrastructure**: No Terraform changes required
