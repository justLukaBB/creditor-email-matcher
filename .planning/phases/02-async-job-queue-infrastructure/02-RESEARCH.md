# Phase 2: Async Job Queue Infrastructure - Research

**Researched:** 2026-02-04
**Domain:** Python async task queues, Dramatiq + Redis, FastAPI integration, job state tracking
**Confidence:** HIGH

## Summary

Dramatiq 2.0.1 (released January 18, 2026) with Redis broker is the established solution for async task processing in Python. It provides automatic exponential backoff retries (15s min to 7 days max, default 20 attempts), built-in idempotency requirements, and clean FastAPI integration patterns. The standard architecture runs two separate processes from the same codebase: web process (uvicorn) and worker process (dramatiq CLI).

For the 512MB Render environment, critical considerations include: Dramatiq workers average 40MB memory per worker, suggesting 2-3 workers are feasible; memory management requires either manual gc.collect() calls or a custom max-tasks-per-child implementation (not built-in); and job state tracking needs PostgreSQL tables with FOR UPDATE SKIP LOCKED for concurrent worker access.

The webhook synchronous validation → queue → async processing pattern is well-established, with validation (signature, dedup, schema) happening before PostgreSQL write, then immediate 200 OK response, then Dramatiq actor invocation.

**Primary recommendation:** Use Dramatiq 2.0.1 with Redis broker, run workers in same Render service as separate process (not thread), implement PostgreSQL state machine with FOR UPDATE SKIP LOCKED, configure 2-3 worker processes with 1-2 threads each for 512MB limit, and add explicit gc.collect() in actors for memory stability.

## Standard Stack

The established libraries/tools for Python async job queues with FastAPI:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| dramatiq | 2.0.1+ | Async task queue | Industry standard for Python 3.10+, simpler than Celery, built-in retry logic with exponential backoff |
| dramatiq[redis] | 2.0.1+ | Redis broker integration | Native Redis support, includes redis-py dependency, simpler deployment than RabbitMQ |
| redis | 5.x-6.x | Python Redis client | Used by dramatiq[redis], handles connection pooling automatically |
| structlog | 24.1.0+ | Structured logging | Already in codebase (Phase 1), critical for debugging async job failures |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| apscheduler | 3.10.0+ | Scheduled jobs | Already in codebase (reconciliation), keep separate from Dramatiq periodic tasks per user decision |
| psycopg[binary] | 3.3.0+ | PostgreSQL adapter | Already in codebase, needed for job state table access from workers |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Dramatiq + Redis | Celery + Redis | Celery has higher memory footprint (100MB+ per worker vs 40MB), more complex configuration, but offers more enterprise features (canvas primitives, result backends) |
| Dramatiq + Redis | RQ (Redis Queue) | RQ is simpler but lacks automatic retries, exponential backoff, and production-grade reliability features |
| Dramatiq + Redis | Procrastinate | Procrastinate uses PostgreSQL as queue (no Redis needed) but less mature ecosystem and requires different monitoring approach |
| Dramatiq + Redis | dramatiq-pg | Uses PostgreSQL as broker (eliminates Redis), but less battle-tested than Redis broker, LISTEN/NOTIFY complexity |

**Installation:**
```bash
pip install 'dramatiq[redis]>=2.0.1'
```

Note: Already have redis client via requirements.txt; dramatiq[redis] ensures version compatibility.

## Architecture Patterns

### Recommended Project Structure
```
app/
├── actors/              # Dramatiq actors (background tasks)
│   ├── __init__.py      # Broker setup, actor imports
│   └── email_processor.py  # Email processing actor
├── models/              # SQLAlchemy models (existing)
│   ├── incoming_email.py   # Add status/timestamps for job tracking
│   └── job.py           # Optional: dedicated job table (if needed)
├── routers/             # FastAPI routers (existing)
│   ├── webhook.py       # Webhook validation + enqueue
│   └── jobs.py          # Job status API endpoints
├── services/            # Business logic (existing)
│   └── dual_write.py    # Used by actor, not webhook
├── main.py              # FastAPI app (existing)
└── worker.py            # Worker entrypoint (import actors, configure broker)
```

### Pattern 1: Webhook Validation → Enqueue → Async Processing
**What:** Synchronous validation and PostgreSQL write in webhook handler, then enqueue Dramatiq message, then return 200 OK immediately. Actual processing happens in background actor.

**When to use:** Webhook endpoints where sender (Zendesk) expects fast response (< 3s) but processing is slow (10-30s LLM calls).

**Example:**
```python
# app/routers/webhook.py
from fastapi import APIRouter, HTTPException, Header
from sqlalchemy.orm import Session
import dramatiq

router = APIRouter()

@router.post("/webhook")
async def receive_webhook(
    webhook_data: ZendeskWebhookEmail,
    x_zendesk_signature: str = Header(None),
    db: Session = Depends(get_db)
):
    # Step 1: Synchronous validation BEFORE queuing
    if not verify_signature(webhook_data, x_zendesk_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Step 2: Deduplication check
    existing = db.query(IncomingEmail).filter(
        IncomingEmail.zendesk_webhook_id == webhook_data.webhook_id
    ).first()
    if existing:
        return {"status": "duplicate", "email_id": existing.id}

    # Step 3: Synchronous PostgreSQL write (RECEIVED status)
    incoming_email = IncomingEmail(
        zendesk_ticket_id=webhook_data.ticket_id,
        zendesk_webhook_id=webhook_data.webhook_id,
        from_email=webhook_data.from_email,
        subject=webhook_data.subject,
        raw_body_html=webhook_data.body_html,
        raw_body_text=webhook_data.body_text,
        attachment_urls=webhook_data.attachments,  # Phase 2: Store URLs
        processing_status="received",  # Initial state
        received_at=datetime.utcnow()
    )
    db.add(incoming_email)
    db.commit()
    db.refresh(incoming_email)

    # Step 4: Update to QUEUED status
    incoming_email.processing_status = "queued"
    db.commit()

    # Step 5: Enqueue Dramatiq message
    from app.actors.email_processor import process_email
    process_email.send(email_id=incoming_email.id)

    # Step 6: Return 200 OK immediately (processing happens async)
    return {
        "status": "accepted",
        "message": "Email queued for processing",
        "email_id": incoming_email.id
    }
```

### Pattern 2: Idempotent Actor with State Machine Tracking
**What:** Dramatiq actor updates PostgreSQL state machine (QUEUED → PROCESSING → COMPLETED/FAILED) and handles exceptions with automatic retries.

**When to use:** All background processing that may retry due to transient failures (API rate limits, network issues).

**Example:**
```python
# app/actors/email_processor.py
# Source: Dramatiq best practices + FastAPI integration patterns
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from app.database import SessionLocal
from app.models import IncomingEmail
import structlog
import gc

logger = structlog.get_logger()

# Configure Redis broker (shared with all actors)
redis_broker = RedisBroker(
    url=settings.redis_url,
    namespace="creditor_matcher",
    max_connections=10  # Limit for 512MB environment
)
dramatiq.set_broker(redis_broker)

@dramatiq.actor(
    max_retries=5,  # Limit retries (default 20 is excessive)
    min_backoff=15000,  # 15 seconds
    max_backoff=300000,  # 5 minutes (not 7 days)
    queue_name="email_processing"
)
def process_email(email_id: int):
    """
    Process email asynchronously: parse, extract, match, write.

    This actor is idempotent - can be safely retried.
    Uses PostgreSQL state machine to track progress.
    """
    db = SessionLocal()

    try:
        # Step 1: Load and lock row (prevent concurrent processing)
        email = db.query(IncomingEmail).filter(
            IncomingEmail.id == email_id
        ).with_for_update(skip_locked=True).first()

        if not email:
            logger.warning("email_not_found_or_locked", email_id=email_id)
            return  # Already processed by another worker

        # Step 2: Transition to PROCESSING
        email.processing_status = "processing"
        email.started_at = datetime.utcnow()
        db.commit()

        # Step 3: Parse email
        parsed = email_parser.parse_email(
            html_body=email.raw_body_html,
            text_body=email.raw_body_text
        )

        # Step 4: Extract entities (may fail with API rate limit)
        extracted = entity_extractor.extract_entities(
            email_body=parsed["cleaned_body"],
            from_email=email.from_email,
            subject=email.subject
        )

        # Step 5: Match and write to databases (Phase 1 saga pattern)
        if extracted.is_creditor_reply:
            dual_writer = DualDatabaseWriter(db, idempotency_svc)
            result = dual_writer.update_creditor_debt(
                email_id=email_id,
                client_name=extracted.client_name,
                creditor_name=extracted.creditor_name,
                new_debt_amount=extracted.debt_amount,
                # ... other params
            )
            db.commit()  # Atomic PG write
            dual_writer.execute_mongodb_write(result["outbox_message_id"])

        # Step 6: Transition to COMPLETED
        email.processing_status = "completed"
        email.completed_at = datetime.utcnow()
        db.commit()

        logger.info("email_processed", email_id=email_id, status="completed")

        # Step 7: Explicit garbage collection (memory management)
        gc.collect()

    except Exception as e:
        logger.error("email_processing_failed", email_id=email_id, error=str(e))

        # Mark as failed in database
        email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()
        if email:
            email.processing_status = "failed"
            email.processing_error = str(e)
            db.commit()

        # Re-raise for Dramatiq retry logic
        raise

    finally:
        db.close()
```

### Pattern 3: Job Status API with State Machine
**What:** REST API endpoints to query job status using PostgreSQL state machine.

**When to use:** Operational visibility, debugging, manual intervention on stuck jobs.

**Example:**
```python
# app/routers/jobs.py
# Source: PostgreSQL job queue patterns 2026
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import IncomingEmail

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

@router.get("")
async def list_jobs(
    status: str = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """List recent jobs with optional status filter"""
    query = db.query(IncomingEmail).order_by(IncomingEmail.created_at.desc())

    if status:
        query = query.filter(IncomingEmail.processing_status == status)

    jobs = query.limit(limit).all()

    return {
        "jobs": [
            {
                "id": job.id,
                "status": job.processing_status,
                "from_email": job.from_email,
                "received_at": job.received_at.isoformat(),
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "error": job.processing_error
            }
            for job in jobs
        ]
    }

@router.get("/{job_id}")
async def get_job_status(job_id: int, db: Session = Depends(get_db)):
    """Get detailed status for specific job"""
    job = db.query(IncomingEmail).filter(IncomingEmail.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": job.id,
        "status": job.processing_status,
        "from_email": job.from_email,
        "subject": job.subject,
        "received_at": job.received_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "processing_time_seconds": (
            (job.completed_at - job.started_at).total_seconds()
            if job.completed_at and job.started_at else None
        ),
        "error": job.processing_error,
        "extracted_data": job.extracted_data,
        "match_status": job.match_status
    }
```

### Pattern 4: Separate Process Deployment on Render
**What:** Run web process (FastAPI) and worker process (Dramatiq) as separate processes within same Render service, sharing 512MB memory.

**When to use:** Resource-constrained environments where separate services would double infrastructure costs.

**Example:**
```python
# worker.py (worker entrypoint)
# Source: Dramatiq production deployment patterns
"""
Worker entrypoint for Dramatiq background processing.

Usage:
    dramatiq app.worker --processes 2 --threads 2
"""
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from app.config import settings

# Import all actors (registers them with broker)
from app.actors import email_processor  # noqa: F401

# Configure Redis broker
redis_broker = RedisBroker(
    url=settings.redis_url,
    namespace="creditor_matcher",
    max_connections=10,  # 80% of Render Redis limit (adjust based on plan)
    socket_timeout=5,
    socket_connect_timeout=5,
    socket_keepalive=True,
    retry_on_timeout=True
)

dramatiq.set_broker(redis_broker)

# Procfile or start command:
# web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
# worker: dramatiq app.worker --processes 2 --threads 2 --verbose
```

### Pattern 5: Email Notification on Permanent Failure
**What:** Send email notification to admin when all retries exhausted, using Dramatiq's on_failure callback.

**When to use:** Production monitoring, human intervention for unrecoverable failures.

**Example:**
```python
# app/actors/email_processor.py (extended)
from app.services.email_notifier import email_notifier

@dramatiq.actor(
    max_retries=5,
    on_failure=notify_failure  # Callback on permanent failure
)
def process_email(email_id: int):
    # ... processing logic ...
    pass

def notify_failure(message_data, exception):
    """
    Called when actor fails permanently (all retries exhausted).

    Source: Dramatiq cookbook - error handling callbacks
    """
    email_id = message_data.get("kwargs", {}).get("email_id")

    email_notifier.send_failure_notification(
        subject=f"Email Processing Failed (ID: {email_id})",
        body=f"""
        Email processing failed after all retries.

        Email ID: {email_id}
        Error: {str(exception)}

        Manual intervention required.
        """,
        to_email=settings.admin_email
    )
```

### Anti-Patterns to Avoid
- **Passing large objects in messages:** Dramatiq serializes messages to Redis. Pass IDs (int), not full objects (SQLAlchemy models). Load from database inside actor.
- **Long-running transactions:** Don't hold database connection open during LLM API calls. Commit state transitions quickly, then proceed with slow operations.
- **Trusting time limits:** Dramatiq time limits are best-effort, cannot interrupt system calls or non-GIL operations. Use timeouts in your HTTP client instead.
- **Running workers in same process as FastAPI:** FastAPI uses async event loop, Dramatiq uses thread pool. Separate processes prevent GIL contention and memory leaks.
- **Forgetting idempotency:** Actors can be retried. Always design for "running multiple times is safe" - use deduplication, check current state before mutations.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry logic with exponential backoff | Custom retry decorator with time.sleep() | Dramatiq's built-in max_retries, min_backoff, max_backoff | Handles edge cases: backoff overflow (caps at 7 days), retry_when predicate for selective retries, dead letter queue after exhaustion |
| Job state tracking in Redis | Custom Redis keys like "job:{id}:status" | PostgreSQL table with status column + FOR UPDATE SKIP LOCKED | Redis doesn't provide ACID guarantees, loses state on restart. PostgreSQL gives transactional consistency, audit trail, and prevents duplicate processing via row locking |
| Worker process management | subprocess.Popen() with manual restart logic | Dramatiq CLI with --processes and --threads flags | Handles graceful shutdown (SIGTERM), worker crashes (auto-restart), connection failures (exit code 3), signal handling (INT/TERM/HUP) |
| Message serialization | pickle or custom JSON encoder | Dramatiq's default JSON serializer | JSON is safe (no code execution risk), cross-language compatible, debuggable. Built-in encoder handles datetime, bytes, etc. |
| Connection pooling | Manual redis.ConnectionPool() management | RedisBroker's automatic pooling | Broker creates pool via ConnectionPool.from_url(), shares across workers, handles reconnection on failures |
| Dead letter queue | Manual "failed_jobs" table with cron cleanup | Dramatiq's automatic dead-lettering to {queue}_XQ | Preserves failed messages for debugging, auto-expires via dead_message_ttl, includes retry count and exception details |

**Key insight:** Async job queues are deceptively complex. Dramatiq handles concurrency edge cases (race conditions, duplicate processing), failure modes (broker disconnect, worker crash, partial failures), and operational concerns (monitoring, dead letters, graceful shutdown) that take months to get right in custom implementations.

## Common Pitfalls

### Pitfall 1: Network Latency Causes Phantom Retries
**What goes wrong:** When network latency exceeds 200ms during broker heartbeats, workers desync from Redis, causing Dramatiq to retry jobs that are still processing, leading to duplicate processing.

**Why it happens:** TCP keepalive mismatch between Redis client and server. Worker thinks connection is alive, Redis thinks worker is dead, re-enqueues message.

**How to avoid:**
- Set Redis broker heartbeat_timeout to 30 seconds (not default): `RedisBroker(heartbeat_timeout=30000)`
- Enable socket keepalive: `RedisBroker(socket_keepalive=True)`
- Use sticky connections via Redis connection pooling

**Warning signs:** Logs show "task started" twice for same message ID, database shows duplicate processing attempts, "worker offline" warnings in logs.

### Pitfall 2: Non-Idempotent Actors Cause Data Corruption
**What goes wrong:** Actors that aren't idempotent (can't be safely retried) cause duplicate database writes, incorrect calculations, or corrupted state when Dramatiq retries after transient failures.

**Why it happens:** Dramatiq retries are automatic and inevitable (worker crashes, network failures). If actor does `balance += amount` instead of `balance = calculate_balance()`, retries double-count.

**How to avoid:**
- Design actors with unique message IDs and deduplication checks
- Use PostgreSQL row locking (FOR UPDATE) to prevent concurrent processing
- Store idempotency keys in database, check before processing
- Use Phase 1's IdempotencyService for database mutations

**Warning signs:** Same email processed multiple times, incorrect debt amounts, duplicate MongoDB updates, outbox messages in "pending" state without matching MongoDB records.

### Pitfall 3: Pre-forking Web Servers Break Message Enqueueing
**What goes wrong:** When using gunicorn or uwsgi with pre-fork model, enqueueing messages fails silently or causes connection errors because Redis connections aren't fork-safe.

**Why it happens:** Parent process creates Redis connection, then forks workers. Child processes inherit file descriptors but not connection state, causing Redis client confusion.

**How to avoid:**
- Use uvicorn without workers flag (single process) for Render deployment
- If using gunicorn, enable lazy apps mode: `gunicorn --preload=false`
- Initialize broker in each worker, not at module level

**Warning signs:** "ConnectionError: Connection already closed" when enqueueing, messages never appear in Redis queue, intermittent enqueueing failures.

### Pitfall 4: Memory Leaks on Long-Running Workers
**What goes wrong:** Workers gradually consume more memory over hours/days, eventually hitting 512MB limit and triggering OOM kills, causing job failures and downtime.

**Why it happens:** Python's garbage collector doesn't always free memory aggressively. Libraries (OpenAI client, BeautifulSoup) can leak memory. CPython's memory allocator fragments and doesn't release to OS.

**How to avoid:**
- Call gc.collect() explicitly after processing each message
- Implement custom max-tasks-per-child middleware (not built-in, see GitHub PR #236)
- Monitor memory with structlog: `logger.info("memory_mb", memory=psutil.Process().memory_info().rss / 1024 / 1024)`
- Restart workers periodically (every 6 hours) via scheduler or health check

**Warning signs:** Worker memory increases over time (check `ps aux`), OOM kills in logs, Render restarts service, jobs fail with "Killed" messages.

### Pitfall 5: Exponential Backoff Overflows
**What goes wrong:** Default max_retries=20 with exponential backoff causes attempts to wait days (2^20 seconds = ~12 days), effectively creating a black hole for failed jobs.

**Why it happens:** Default Dramatiq settings assume rare transient failures. Persistent failures (bad API key, schema mismatch) shouldn't retry 20 times.

**How to avoid:**
- Set max_retries=5 for most actors (sufficient for transient failures)
- Cap max_backoff at 3600000 (1 hour), not 7 days
- Use retry_when predicate to skip retries for permanent failures (401, 400 errors)
- Divert to dead letter queue after max_retries via on_failure callback

**Warning signs:** Jobs stuck in "queued" state for days, Redis memory grows (unprocessed messages), no processing activity but queue depth increases.

### Pitfall 6: Race Condition in State Machine Transitions
**What goes wrong:** Two workers pick up same job, both transition to "processing", both execute logic, causing duplicate LLM API calls, duplicate MongoDB writes, wasted money.

**Why it happens:** Without row locking, PostgreSQL query returns same row to multiple workers before either commits status update.

**How to avoid:**
- Use `FOR UPDATE SKIP LOCKED` when selecting jobs: `db.query(Job).filter(...).with_for_update(skip_locked=True).first()`
- Workers skip rows locked by others, no blocking or duplicate processing
- Commit status update quickly, then proceed with slow operations

**Warning signs:** Same job ID in logs from multiple workers, duplicate API calls in provider logs, race condition errors in database logs.

### Pitfall 7: Shared 512MB Memory Between Web + Workers
**What goes wrong:** Render service has 512MB total. If FastAPI process uses 200MB and workers use 3 × 120MB, total exceeds limit, causing OOM kills.

**Why it happens:** Both processes run in same container, share memory limit. Dramatiq documentation assumes dedicated worker machines, not shared hosting.

**How to avoid:**
- Monitor total memory: `ps aux | grep -E '(uvicorn|dramatiq)' | awk '{sum+=$6} END {print sum/1024 " MB"}'`
- Configure conservatively: 2 worker processes × 1-2 threads = 2-4 concurrent jobs
- Measure FastAPI baseline memory (likely 100-150MB)
- Reserve 150MB for FastAPI, allocate remaining 350MB to workers (2 workers × 175MB each)
- Set alerts in Render for memory usage > 80%

**Warning signs:** Render dashboard shows memory spikes, service restarts with OOM errors, both web and worker processes killed simultaneously.

## Code Examples

Verified patterns from official sources:

### Actor Definition with Custom Retry Logic
```python
# Source: https://dramatiq.io/guide.html
import dramatiq
from anthropic import Anthropic, RateLimitError

def should_retry_llm_call(retries_so_far, exception):
    """Only retry on transient failures, not permanent errors"""
    # Retry on rate limits and timeouts
    if isinstance(exception, (RateLimitError, TimeoutError)):
        return retries_so_far < 5

    # Don't retry on bad requests (400, 401) - these are permanent
    if isinstance(exception, anthropic.BadRequestError):
        return False

    # Default: retry up to max_retries
    return retries_so_far < 3

@dramatiq.actor(
    max_retries=5,
    min_backoff=15000,  # 15 seconds
    max_backoff=300000,  # 5 minutes
    retry_when=should_retry_llm_call,
    queue_name="llm_processing"
)
def extract_entities_async(email_id: int):
    """Extract entities from email using Claude API"""
    # Actor logic...
    pass
```

### Redis Broker Configuration with Connection Limits
```python
# Source: https://dramatiq.io/reference.html
# Source: https://docs.upstash.com/redis/troubleshooting/max_concurrent_connections
from dramatiq.brokers.redis import RedisBroker
import dramatiq

redis_broker = RedisBroker(
    url=settings.redis_url,  # From environment
    namespace="creditor_matcher",  # Prefix all keys

    # Connection pooling (80% of Render Redis Standard = 800 connections)
    max_connections=10,  # Per worker process
    socket_timeout=5,
    socket_connect_timeout=5,
    socket_keepalive=True,
    retry_on_timeout=True,

    # Dramatiq-specific settings
    heartbeat_timeout=30000,  # 30 seconds (prevent phantom retries)
    dead_message_ttl=86400000  # 24 hours (1 day, not 7 days default)
)

dramatiq.set_broker(redis_broker)
```

### Job State Selection with FOR UPDATE SKIP LOCKED
```python
# Source: https://www.inferable.ai/blog/posts/postgres-skip-locked
# Source: PostgreSQL job queue patterns 2026
from sqlalchemy.orm import Session
from app.models import IncomingEmail

def get_next_job(db: Session) -> IncomingEmail | None:
    """
    Get next pending job, locking row to prevent duplicate processing.

    FOR UPDATE SKIP LOCKED ensures:
    - Row is locked for this transaction
    - Other workers skip this row (no blocking)
    - No duplicate processing
    """
    job = db.query(IncomingEmail).filter(
        IncomingEmail.processing_status == "queued"
    ).order_by(
        IncomingEmail.received_at.asc()  # FIFO processing
    ).with_for_update(
        skip_locked=True  # Don't wait for locked rows
    ).first()

    return job
```

### Memory Management with Explicit GC
```python
# Source: GitHub PR #236 discussion + production patterns
import gc
import psutil
import structlog

logger = structlog.get_logger()

@dramatiq.actor(max_retries=5)
def process_email(email_id: int):
    """Process email with explicit memory management"""
    try:
        # Log memory before processing
        memory_before = psutil.Process().memory_info().rss / 1024 / 1024
        logger.info("processing_start", email_id=email_id, memory_mb=memory_before)

        # ... processing logic ...

        # Log memory after processing
        memory_after = psutil.Process().memory_info().rss / 1024 / 1024
        logger.info("processing_complete", email_id=email_id, memory_mb=memory_after)

    finally:
        # Force garbage collection
        gc.collect()

        memory_after_gc = psutil.Process().memory_info().rss / 1024 / 1024
        logger.info("gc_complete", memory_mb=memory_after_gc)
```

### Dramatiq Worker Startup Script
```bash
#!/bin/bash
# Source: Dramatiq cookbook - production deployment
# worker_start.sh

# Exit on error
set -e

# Wait for Redis to be ready
echo "Waiting for Redis..."
until redis-cli -u $REDIS_URL ping 2>/dev/null; do
  echo "Redis not ready, retrying in 2s..."
  sleep 2
done

echo "Redis ready, starting Dramatiq workers..."

# Start Dramatiq workers
# --processes 2: 2 worker processes
# --threads 2: 2 threads per process = 4 concurrent jobs
# --verbose: detailed logging
dramatiq app.worker \
  --processes 2 \
  --threads 2 \
  --verbose \
  --watch /app  # Reload on code changes (dev only)
```

### Alembic Migration for Job State Machine
```python
# Source: PostgreSQL job queue state machine patterns
# migrations/versions/xxx_add_job_state_machine.py
"""Add job state machine fields to incoming_emails

Adds: started_at, completed_at timestamps for tracking
Updates: processing_status enum to include all states
"""

def upgrade():
    # Add timestamp fields
    op.add_column('incoming_emails',
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('incoming_emails',
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True)
    )

    # Add index for job status queries
    op.create_index(
        'ix_incoming_emails_status_received',
        'incoming_emails',
        ['processing_status', 'received_at']
    )

    # Note: processing_status already exists, just document valid values:
    # - received: webhook received, validated, stored
    # - queued: enqueued to Dramatiq
    # - processing: worker picked up job
    # - completed: successfully finished
    # - failed: permanently failed (all retries exhausted)

def downgrade():
    op.drop_index('ix_incoming_emails_status_received', 'incoming_emails')
    op.drop_column('incoming_emails', 'completed_at')
    op.drop_column('incoming_emails', 'started_at')
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Celery + Redis | Dramatiq + Redis | 2018-2020 | Dramatiq simpler configuration, lower memory (40MB vs 100MB), cleaner API. Celery still preferred for complex workflows (chains, chords). |
| FastAPI BackgroundTasks | Dramatiq actors | 2020+ | BackgroundTasks runs in same process (memory leak risk), no retry logic, no persistence. Use BackgroundTasks for fire-and-forget (<1s), Dramatiq for reliable processing. |
| Redis as job state store | PostgreSQL state machine | 2024+ | Redis loses state on restart, no ACID guarantees, hard to query/audit. PostgreSQL provides transactional consistency, audit trail, complex queries (analytics). |
| Prometheus metrics built-in | Prometheus optional | Dramatiq 2.0.0 (Nov 2024) | Breaking change: must install `dramatiq[prometheus]` separately. Reduces default dependencies. |
| Python 3.9 support | Python 3.10+ required | Dramatiq 2.0.0 (Nov 2024) | Leverages 3.10+ type hints, pattern matching, performance improvements. |
| Separate Render services (web + worker) | Same service, separate processes | 2025+ cost optimization | Render charges per service. Running both processes in one service (512MB shared) cuts costs 50% but requires memory monitoring. |

**Deprecated/outdated:**
- **URLRabbitmqBroker:** Removed in Dramatiq 2.0.0. Use `RedisBroker(url=...)` instead.
- **backend parameter optional for ResultMiddleware:** Now mandatory in Dramatiq 2.0.0.
- **Dramatiq built-in Prometheus:** Now requires `dramatiq[prometheus]` extra.
- **Running workers without --processes flag:** Default behavior changed. Now must explicitly specify for production.

## Open Questions

Things that couldn't be fully resolved:

1. **Exact Render Redis connection limits**
   - What we know: Render offers Redis add-on, likely Upstash-backed. Upstash Standard allows 1,000 concurrent connections. Dramatiq creates connection pool per worker process.
   - What's unclear: Render-specific Redis plan limits (connections, memory), whether Redis is shared across services.
   - Recommendation: Start with max_connections=10 per worker (conservative), monitor with Redis INFO command, scale up if no errors.

2. **Optimal worker configuration for 512MB**
   - What we know: Dramatiq workers average 40MB per process. FastAPI (uvicorn) baseline ~100-150MB. 512MB total.
   - What's unclear: Memory usage with LLM client libraries (anthropic, openai), BeautifulSoup HTML parsing, MongoDB client. Will 2 workers fit?
   - Recommendation: Start with 2 processes × 1 thread (2 concurrent), measure actual memory usage via `ps aux`, adjust to 2 processes × 2 threads if headroom exists.

3. **max-tasks-per-child implementation strategy**
   - What we know: Not built-in in Dramatiq. Community PR exists (GitHub #236) but not merged. Maintainer suggests subprocess pool instead.
   - What's unclear: Whether explicit gc.collect() is sufficient for memory stability, or if process recycling is required.
   - Recommendation: Start with explicit gc.collect() in actors. If memory still leaks, implement custom middleware based on PR #236 or periodic worker restarts via health check.

4. **Dramatiq vs APScheduler coexistence**
   - What we know: User decided to keep APScheduler for reconciliation (hourly cron), not migrate to Dramatiq periodic tasks.
   - What's unclear: Whether APScheduler job can safely use Dramatiq actors (schedule reconciliation, then reconciliation enqueues Dramatiq jobs).
   - Recommendation: Keep APScheduler for scheduling only. APScheduler job can enqueue Dramatiq messages, but shouldn't do heavy work directly.

5. **Email notification on failure - implementation approach**
   - What we know: User wants email on permanent failure. Dramatiq supports on_failure callback. Existing SMTP setup in codebase (app.services.email_notifier).
   - What's unclear: Should on_failure callback send email directly, or enqueue another Dramatiq actor to send email (avoiding blocking)?
   - Recommendation: on_failure callback enqueues send_failure_email actor (separate queue, no retries). Keeps failure notification fast, avoids nested failure scenarios.

## Sources

### Primary (HIGH confidence)
- [Dramatiq 2.0.0 Official Documentation](https://dramatiq.io/) - Core library documentation
- [Dramatiq User Guide](https://dramatiq.io/guide.html) - Retry configuration, worker setup
- [Dramatiq Advanced Topics](https://dramatiq.io/advanced.html) - Production deployment patterns
- [Dramatiq Best Practices](https://dramatiq.io/best_practices.html) - Idempotency, message design
- [Dramatiq Cookbook](https://dramatiq.io/cookbook.html) - Integration patterns, error handling
- [Dramatiq PyPI](https://pypi.org/project/dramatiq/) - Version 2.0.1, January 18, 2026
- [Dramatiq GitHub Releases](https://github.com/Bogdanp/dramatiq/releases) - Changelog, breaking changes
- [Dramatiq Redis Broker Source](https://dramatiq.io/_modules/dramatiq/brokers/redis.html) - Connection pool configuration
- [PostgreSQL SKIP LOCKED](https://www.inferable.ai/blog/posts/postgres-skip-locked) - Concurrent job processing pattern

### Secondary (MEDIUM confidence)
- [François Voron: FastAPI + Dramatiq Data Ingestion](https://www.francoisvoron.com/blog/create-deploy-reliable-data-ingestion-service-fastapi-sqlmodel-dramatiq) - Webhook → queue → async pattern
- [Dramatiq Python Actors: Middleware Retries Throttling 2026](https://johal.in/dramatiq-python-actors-middleware-retries-throttling-2026/) - Production pitfalls, retry tuning
- [Upstash: Max Concurrent Connections](https://docs.upstash.com/redis/troubleshooting/max_concurrent_connections) - Redis connection limits
- [Medium: Implementing Efficient Queue Systems in PostgreSQL](https://medium.com/@epam.macys/implementing-efficient-queue-systems-in-postgresql-c219ccd56327) - State machine patterns
- [GitHub PR #236: MaxTasksPerChild middleware](https://github.com/Bogdanp/dramatiq/pull/236) - Memory management discussion
- [dramatiq-taskstate PyPI](https://pypi.org/project/dramatiq-taskstate/) - PostgreSQL state tracking middleware

### Tertiary (LOW confidence - marked for validation)
- WebSearch: "FastAPI webhook async job queue pattern synchronous validation 2026" - General pattern confirmation, needs verification with official FastAPI docs
- WebSearch: "job state machine PostgreSQL RECEIVED QUEUED PROCESSING COMPLETED pattern 2026" - Community patterns, should verify with production examples

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Dramatiq 2.0.1 version confirmed via PyPI (2026-01-18), Redis broker in official docs, Python 3.10+ requirement verified
- Architecture: HIGH - Patterns verified against official Dramatiq docs (user guide, cookbook), FastAPI integration confirmed via multiple production examples, PostgreSQL SKIP LOCKED pattern from authoritative sources
- Pitfalls: MEDIUM-HIGH - Network latency issue documented in 2026 blog post, idempotency and memory issues verified in official best practices, pre-fork issue in troubleshooting docs. Confidence reduced slightly because some pitfalls from community sources, not official docs.
- Memory management: MEDIUM - Worker memory (40MB) from recent source, but max-tasks-per-child not officially supported, recommendations based on GitHub discussion and community practices
- Deployment patterns: HIGH - Separate process architecture verified in official docs and multiple production examples, CLI flags documented, Render-specific adjustments based on platform constraints

**Research date:** 2026-02-04
**Valid until:** 2026-03-04 (30 days - Dramatiq is stable, but monitoring for minor releases and community pattern updates)

**Note on current state:** Dramatiq 2.0.1 is the latest stable release as of January 2026. Library is mature with 5+ years production use. Redis broker is battle-tested. FastAPI integration patterns are well-established in community. Main uncertainty is Render-specific resource constraints (512MB shared memory), which requires runtime monitoring and tuning.
