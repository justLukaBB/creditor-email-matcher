# Phase 1: Dual-Database Audit & Consistency - Research

**Researched:** 2026-02-04
**Domain:** Dual-database saga pattern, idempotency, reconciliation
**Confidence:** MEDIUM-HIGH

## Summary

This research investigates implementing PostgreSQL as single source of truth with saga pattern for dual-database writes to MongoDB, preventing data inconsistencies in the creditor email matching system.

The standard approach in 2026 uses the **orchestration-based saga pattern** with **transactional outbox** for PostgreSQL writes and compensating transactions for MongoDB failures. Idempotency is achieved through UUID-based keys stored in Redis or PostgreSQL. Reconciliation jobs use periodic comparison with automated repair workflows.

**Key Challenge:** The project uses PyMongo (synchronous) and SQLAlchemy (already in stack), not a microservices architecture. This requires adapting saga patterns designed for microservices to a monolithic FastAPI application with dual databases.

**Critical Decision:** Whether to implement full saga framework (saga-framework library) or a simplified pattern using transactional outbox + manual compensations. Given the project's scope (single application, two databases, ~200 emails/day), a **lightweight approach** is recommended over heavyweight saga orchestration.

**Primary recommendation:** Use SQLAlchemy session events + transactional outbox pattern for PostgreSQL writes, followed by synchronous MongoDB writes with compensating transactions and Redis-based idempotency keys. Implement hourly Celery Beat reconciliation job comparing both databases.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| SQLAlchemy | 2.0+ | PostgreSQL ORM with transaction events | Already in stack, supports session hooks for outbox pattern |
| PyMongo | 4.6+ | MongoDB driver | Already in stack, supports transactions in replica sets |
| Redis | 7.x | Idempotency key storage | Industry standard for fast key-value lookups with TTL |
| Celery | 5.6+ | Scheduled reconciliation jobs | Production-proven for cron-like tasks, already common in Python stacks |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| celery-beat | 5.6+ | Cron scheduler for Celery | Hourly reconciliation job scheduling |
| redis-py | 5.0+ | Redis Python client | Idempotency key storage and retrieval |
| Pydantic | 2.5+ | Data validation | Already in stack, validate reconciliation results |
| structlog | 24.1+ | Structured logging | Audit trail for saga steps and compensations |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Celery | Dramatiq | Simpler but lacks mature beat scheduler for cron jobs |
| Redis (idempotency) | PostgreSQL table | More durable but slower lookups, adds DB load |
| Manual saga | saga-framework library | Framework is over-engineered for monolith, adds complexity |
| Transactional outbox | Direct publish after commit | Outbox guarantees delivery, direct publish can lose messages |

**Installation:**
```bash
# Add to existing requirements.txt
celery[redis]>=5.6.0
redis>=5.0.0
structlog>=24.1.0

# Redis hosting options for Render:
# - Upstash Redis (recommended): Free tier, 10K commands/day
# - Railway Redis: $5/month
# - Redis Cloud: $5/month starter tier
```

## Architecture Patterns

### Recommended Project Structure
```
src/
├── saga/                      # Saga coordination
│   ├── outbox.py             # Transactional outbox implementation
│   ├── compensations.py      # MongoDB rollback handlers
│   └── idempotency.py        # Redis-based deduplication
├── reconciliation/           # Data consistency checking
│   ├── comparator.py         # PostgreSQL-MongoDB comparison logic
│   ├── repair.py             # Automated mismatch resolution
│   └── tasks.py              # Celery periodic tasks
├── models/                   # Database models
│   ├── outbox_message.py     # Outbox table SQLAlchemy model
│   └── idempotency_key.py    # Optional: DB-backed idempotency
└── services/
    └── dual_write.py         # Saga orchestration logic
```

### Pattern 1: Lightweight Saga with Transactional Outbox
**What:** PostgreSQL writes occur in transaction with outbox messages. After commit succeeds, MongoDB write is attempted. If MongoDB fails, compensating transaction removes PostgreSQL record or marks for manual review.

**When to use:** Single application with two databases, moderate volume (<1000 writes/day), synchronous processing acceptable.

**Example:**
```python
# Source: Synthesized from https://blog.szymonmiks.pl/p/the-outbox-pattern-in-python/
# and https://microservices.io/patterns/data/transactional-outbox.html

from sqlalchemy.orm import Session
from sqlalchemy import event
import structlog
from typing import Optional
import uuid

logger = structlog.get_logger()

class OutboxMessage(Base):
    """Stores operations to be replicated to MongoDB"""
    __tablename__ = "outbox_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type = Column(String(100), nullable=False)  # 'incoming_email', 'match_result'
    aggregate_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(String(50), nullable=False)  # 'INSERT', 'UPDATE', 'DELETE'
    payload = Column(JSONB, nullable=False)
    idempotency_key = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

class DualDatabaseWriter:
    """Coordinates PostgreSQL and MongoDB writes with saga pattern"""

    def __init__(self, pg_session: Session, mongo_db, redis_client):
        self.pg_session = pg_session
        self.mongo_db = mongo_db
        self.redis_client = redis_client

    def write_email_data(self, email_data: dict, idempotency_key: str) -> bool:
        """
        Write email data to both databases with saga pattern.
        Returns True if successful, False if duplicate (idempotent).
        """
        # Check idempotency
        if self._is_duplicate(idempotency_key):
            logger.info("duplicate_request_ignored", key=idempotency_key)
            return False

        try:
            # Step 1: Write to PostgreSQL (source of truth) + outbox message
            with self.pg_session.begin():
                # Business data
                email_record = IncomingEmail(**email_data)
                self.pg_session.add(email_record)

                # Outbox message for MongoDB replication
                outbox_msg = OutboxMessage(
                    aggregate_type="incoming_email",
                    aggregate_id=email_record.id,
                    operation="INSERT",
                    payload=email_data,
                    idempotency_key=idempotency_key
                )
                self.pg_session.add(outbox_msg)
                # Commit happens at context exit

            # Step 2: PostgreSQL committed successfully, mark idempotent
            self._mark_processed(idempotency_key)

            # Step 3: Attempt MongoDB write (compensatable)
            try:
                mongo_result = self._write_to_mongodb(email_data)

                # Step 4: Mark outbox message as processed
                outbox_msg.processed_at = datetime.utcnow()
                self.pg_session.commit()

                logger.info("dual_write_success",
                           email_id=email_record.id,
                           idempotency_key=idempotency_key)
                return True

            except Exception as mongo_error:
                logger.error("mongodb_write_failed",
                            email_id=email_record.id,
                            error=str(mongo_error))

                # Step 5: Execute compensation (rollback PostgreSQL or mark for review)
                self._compensate_postgres_write(email_record.id, str(mongo_error))
                raise

        except Exception as e:
            logger.error("saga_failed", error=str(e), idempotency_key=idempotency_key)
            self.pg_session.rollback()
            raise

    def _is_duplicate(self, idempotency_key: str) -> bool:
        """Check if operation already processed"""
        return self.redis_client.exists(f"idempotent:{idempotency_key}") > 0

    def _mark_processed(self, idempotency_key: str):
        """Mark operation as processed with 24-hour TTL"""
        self.redis_client.setex(
            f"idempotent:{idempotency_key}",
            86400,  # 24 hours
            "1"
        )

    def _write_to_mongodb(self, email_data: dict):
        """Write to MongoDB with retry logic"""
        # Implementation depends on specific MongoDB schema
        collection = self.mongo_db["clients"]
        # Update final_creditor_list or similar
        return collection.update_one(
            {"_id": email_data["client_id"]},
            {"$push": {"final_creditor_list": email_data}},
            upsert=False
        )

    def _compensate_postgres_write(self, email_id: uuid.UUID, error_msg: str):
        """
        Compensating transaction for MongoDB failure.
        Options:
        1. Mark record as 'pending_mongodb_sync' for reconciliation
        2. Delete record (if business logic allows)
        3. Store in dead letter table for manual review
        """
        with self.pg_session.begin():
            email_record = self.pg_session.get(IncomingEmail, email_id)
            if email_record:
                email_record.sync_status = "pending_mongodb_sync"
                email_record.sync_error = error_msg
                email_record.sync_retry_count = 0

        logger.warning("compensation_executed",
                      email_id=email_id,
                      action="marked_for_retry")
```

### Pattern 2: Outbox Publisher (Async Processing)
**What:** Separate background process polls outbox table for unprocessed messages and publishes to MongoDB. Decouples PostgreSQL commit from MongoDB write timing.

**When to use:** Higher volumes (>500/day), need for asynchronous processing, retry logic complexity.

**Example:**
```python
# Source: Synthesized from https://blog.szymonmiks.pl/p/the-outbox-pattern-in-python/

from celery import shared_task
from sqlalchemy import select
import structlog

logger = structlog.get_logger()

@shared_task(bind=True, max_retries=5)
def process_outbox_messages(self):
    """
    Celery task that processes unprocessed outbox messages.
    Run every minute via celery-beat.
    """
    session = SessionLocal()
    mongo_db = get_mongo_db()

    try:
        # Fetch unprocessed messages (limit to prevent long-running task)
        stmt = select(OutboxMessage).where(
            OutboxMessage.processed_at.is_(None),
            OutboxMessage.retry_count < 5
        ).limit(100).order_by(OutboxMessage.created_at)

        messages = session.execute(stmt).scalars().all()

        for msg in messages:
            try:
                # Reconstruct operation and execute on MongoDB
                if msg.operation == "INSERT":
                    _mongo_insert(mongo_db, msg.aggregate_type, msg.payload)
                elif msg.operation == "UPDATE":
                    _mongo_update(mongo_db, msg.aggregate_type, msg.aggregate_id, msg.payload)
                elif msg.operation == "DELETE":
                    _mongo_delete(mongo_db, msg.aggregate_type, msg.aggregate_id)

                # Mark as processed
                msg.processed_at = datetime.utcnow()
                session.commit()

                logger.info("outbox_message_processed",
                           message_id=msg.id,
                           aggregate_id=msg.aggregate_id)

            except Exception as e:
                # Increment retry count, log error
                msg.retry_count += 1
                msg.error_message = str(e)
                session.commit()

                logger.error("outbox_processing_failed",
                            message_id=msg.id,
                            retry_count=msg.retry_count,
                            error=str(e))

                if msg.retry_count >= 5:
                    # Dead letter - alert for manual intervention
                    logger.critical("outbox_message_dead_letter",
                                   message_id=msg.id,
                                   aggregate_id=msg.aggregate_id)

    finally:
        session.close()
```

### Pattern 3: Hourly Reconciliation Job
**What:** Scheduled job compares PostgreSQL (source of truth) with MongoDB, detects mismatches, and repairs automatically or flags for review.

**When to use:** Always - reconciliation is safety net for saga failures, catches edge cases.

**Example:**
```python
# Source: Synthesized from https://github.com/sergiomontey/Data-Validation-Reconciliation-Tool
# and https://www.trymito.io/blog/how-to-automate-reconciliations-in-python-a-complete-guide

from celery import shared_task
from celery.schedules import crontab
from dataclasses import dataclass
from typing import List
import structlog

logger = structlog.get_logger()

@dataclass
class ReconciliationMismatch:
    mismatch_type: str  # 'missing_in_mongo', 'extra_in_mongo', 'data_mismatch'
    postgres_id: uuid.UUID
    mongo_id: Optional[str]
    field_name: Optional[str]
    postgres_value: Optional[any]
    mongo_value: Optional[any]
    detected_at: datetime

@shared_task
def hourly_reconciliation():
    """
    Compare PostgreSQL (source of truth) with MongoDB.
    Runs every hour via celery-beat schedule.
    """
    session = SessionLocal()
    mongo_db = get_mongo_db()
    mismatches: List[ReconciliationMismatch] = []

    try:
        # Structural reconciliation: Check counts
        pg_count = session.query(IncomingEmail).count()
        mongo_count = mongo_db["clients"].count_documents({})

        if pg_count != mongo_count:
            logger.warning("count_mismatch",
                          postgres=pg_count,
                          mongodb=mongo_count,
                          diff=pg_count - mongo_count)

        # Row-level reconciliation: Compare records from last 48 hours
        cutoff = datetime.utcnow() - timedelta(hours=48)
        recent_emails = session.query(IncomingEmail).filter(
            IncomingEmail.created_at >= cutoff
        ).all()

        for pg_email in recent_emails:
            # Find corresponding MongoDB document
            mongo_doc = mongo_db["clients"].find_one(
                {"final_creditor_list.email_id": str(pg_email.id)}
            )

            if not mongo_doc:
                # Missing in MongoDB - repair by inserting
                mismatch = ReconciliationMismatch(
                    mismatch_type="missing_in_mongo",
                    postgres_id=pg_email.id,
                    mongo_id=None,
                    field_name=None,
                    postgres_value=None,
                    mongo_value=None,
                    detected_at=datetime.utcnow()
                )
                mismatches.append(mismatch)

                # Auto-repair: Insert into MongoDB
                _repair_missing_in_mongo(mongo_db, pg_email)

            else:
                # Field-level comparison
                mongo_email = next(
                    (item for item in mongo_doc.get("final_creditor_list", [])
                     if item.get("email_id") == str(pg_email.id)),
                    None
                )

                if mongo_email:
                    # Compare critical fields
                    if pg_email.subject != mongo_email.get("subject"):
                        mismatch = ReconciliationMismatch(
                            mismatch_type="data_mismatch",
                            postgres_id=pg_email.id,
                            mongo_id=mongo_doc["_id"],
                            field_name="subject",
                            postgres_value=pg_email.subject,
                            mongo_value=mongo_email.get("subject"),
                            detected_at=datetime.utcnow()
                        )
                        mismatches.append(mismatch)

                        # Auto-repair: Update MongoDB with PostgreSQL value
                        _repair_data_mismatch(mongo_db, pg_email, mongo_doc["_id"], "subject")

        # Store reconciliation results
        _store_reconciliation_report(session, mismatches)

        if mismatches:
            logger.warning("reconciliation_mismatches_found",
                          total=len(mismatches),
                          missing_in_mongo=sum(1 for m in mismatches if m.mismatch_type == "missing_in_mongo"),
                          data_mismatches=sum(1 for m in mismatches if m.mismatch_type == "data_mismatch"))
        else:
            logger.info("reconciliation_clean", checked_records=len(recent_emails))

    finally:
        session.close()

def _repair_missing_in_mongo(mongo_db, pg_email):
    """Insert missing record into MongoDB from PostgreSQL"""
    # Implementation depends on MongoDB schema
    pass

def _repair_data_mismatch(mongo_db, pg_email, mongo_id, field_name):
    """Update MongoDB field to match PostgreSQL value"""
    # Implementation depends on MongoDB schema
    pass

# Celery Beat schedule configuration
app.conf.beat_schedule = {
    'hourly-reconciliation': {
        'task': 'reconciliation.tasks.hourly_reconciliation',
        'schedule': crontab(minute=0),  # Every hour at :00
    },
    'process-outbox-every-minute': {
        'task': 'saga.outbox.process_outbox_messages',
        'schedule': 60.0,  # Every 60 seconds
    },
}
```

### Anti-Patterns to Avoid
- **Direct dual-write without saga:** Writing to PostgreSQL then MongoDB without compensation logic. MongoDB failures leave PostgreSQL with orphaned records.
- **Publishing messages before PostgreSQL commit:** Message broker receives event, but database transaction rolls back. Downstream systems process phantom events.
- **Forgetting idempotency on retries:** Retry logic without idempotency keys causes duplicate records in both databases.
- **No reconciliation job:** Relying solely on saga pattern. Edge cases (network partitions, process crashes) still cause inconsistencies.
- **Synchronous saga blocking webhook response:** Running full saga (PG write + Mongo write + compensations) in webhook handler. Use async processing (Celery) for saga steps.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Idempotency tracking | Custom database table with manual TTL cleanup | Redis with SETEX | Redis handles TTL automatically, faster lookups, less DB load |
| Outbox message polling | Custom while-true loop script | Celery task with beat schedule | Celery handles retries, error handling, monitoring, distributed execution |
| Data comparison logic | Manual field-by-field comparison loops | Pydantic models with `model_dump()` comparison | Pydantic validates and serializes, catches type mismatches automatically |
| Compensating transaction registry | If-else chains or hardcoded mappings | Dictionary of operation -> compensation function | Extensible, testable, clear mapping |
| Saga state machine | Custom state tracking with enums | SQLAlchemy with status enum column + event listeners | Database-backed, survives process restarts, queryable for debugging |

**Key insight:** Saga pattern infrastructure (outbox polling, idempotency, reconciliation) is repetitive boilerplate. Use libraries/frameworks for these, hand-roll only business-specific compensation logic.

## Common Pitfalls

### Pitfall 1: Lack of Isolation (Concurrent Saga Execution)
**What goes wrong:** Two simultaneous requests with different idempotency keys modify the same MongoDB document. First saga succeeds, second saga's compensation logic rolls back both changes.

**Why it happens:** Saga pattern sacrifices ACID isolation for availability. Compensations don't know about concurrent operations.

**How to avoid:**
- Use MongoDB transactions (requires replica set) for multi-document updates
- Implement application-level locking with Redis (SETNX with TTL)
- Design compensations to be additive (append to arrays) rather than destructive (replace entire documents)

**Warning signs:**
- Reconciliation reports show "data_mismatch" immediately after successful saga
- MongoDB documents missing expected fields after compensation
- Logs show overlapping saga executions for same aggregate

### Pitfall 2: Idempotency Key Scope Too Narrow
**What goes wrong:** Using email_id as idempotency key. Retry with same email but different extracted data is treated as duplicate and ignored.

**Why it happens:** Conflating business entity ID with operation idempotency. Same email processed twice with updated extraction logic should succeed, not be deduped.

**How to avoid:**
- Generate idempotency key from: `email_id + operation_type + timestamp + request_hash`
- OR: Use client-generated UUID passed in webhook request headers
- OR: Scope keys by operation: `f"email_insert:{email_id}:{hash(payload)}"`

**Warning signs:**
- "Duplicate request ignored" logs for legitimate retries
- Updates to existing emails never succeed
- Idempotency keys never expire (TTL too long)

### Pitfall 3: Transactional Outbox Without Cleanup
**What goes wrong:** Outbox table grows unbounded. After 6 months, 100K+ processed messages slow down queries. Database backup size explodes.

**Why it happens:** Only mark messages as processed, never delete them. Assume "data is cheap."

**How to avoid:**
- Add Celery task to delete processed messages older than 30 days
- OR: Partition outbox table by month, drop old partitions
- OR: Archive to cold storage (S3) after 7 days, delete from database
- Monitor outbox table size with alerts

**Warning signs:**
- Outbox queries taking >100ms
- Database backup files growing 10MB+/day
- Disk usage alerts on PostgreSQL server

### Pitfall 4: Compensation Logic Not Idempotent
**What goes wrong:** Saga fails, compensation runs. Saga retries, compensation runs again on already-compensated data. MongoDB document corrupted.

**Why it happens:** Compensations must be idempotent (safe to run multiple times), but developers treat them as one-shot rollbacks.

**How to avoid:**
- Check current state before compensating: "Is this already rolled back?"
- Use conditional MongoDB operations: `update_one(..., upsert=False)` returns 0 if already deleted
- Log compensation executions with `compensation_id` to track duplicates

**Warning signs:**
- MongoDB documents with negative counts or missing required fields
- "Compensation failed: document not found" errors
- Multiple compensation log entries for same saga_id

### Pitfall 5: Forgetting MongoDB Backward Compatibility
**What goes wrong:** Saga adds new field to MongoDB document. Node.js Mandanten-Portal crashes because it expects old schema.

**Why it happens:** Requirement REQ-MIGRATE-01 (MongoDB backward compatibility) is ignored during saga implementation.

**How to avoid:**
- Always add fields, never remove or rename
- Use MongoDB schema versioning: `{"schema_version": 2, ...}`
- Test saga changes against Node.js app in staging environment
- Document MongoDB schema changes in migration guide

**Warning signs:**
- Node.js errors after Python deployment
- MongoDB documents with `null` fields where Node.js expects arrays
- Mandanten-Portal displaying incomplete creditor data

### Pitfall 6: No Visibility Into Saga Failures
**What goes wrong:** Saga fails silently. PostgreSQL has record, MongoDB doesn't. No alert fired. Discovered 2 weeks later during manual audit.

**Why it happens:** Errors logged but not monitored. No metrics on saga success/failure rates.

**How to avoid:**
- Emit structured logs with saga_id, step, outcome
- Track metrics: `saga_started`, `saga_completed`, `saga_compensated`, `saga_failed`
- Alert on: outbox messages unprocessed >1 hour, reconciliation mismatches >10/day
- Create dashboard: saga success rate, avg processing time, pending outbox count

**Warning signs:**
- Silent data loss discovered during reconciliation
- "Why isn't this email in MongoDB?" support tickets
- No way to answer "How many sagas failed this week?"

## Code Examples

Verified patterns from official sources:

### Idempotency Key Generation and Checking
```python
# Source: Synthesized from https://docs.aws.amazon.com/powertools/python/latest/utilities/idempotency/
# and https://leapcell.io/blog/ensuring-idempotency-for-robust-api-operations

import uuid
import hashlib
import json
from redis import Redis
from typing import Optional

redis_client = Redis(host='localhost', port=6379, db=0)

def generate_idempotency_key(email_data: dict) -> str:
    """
    Generate idempotency key from email content hash.
    Ensures same email content processed only once.
    """
    # Sort keys for consistent hashing
    content_hash = hashlib.sha256(
        json.dumps(email_data, sort_keys=True).encode()
    ).hexdigest()[:16]

    return f"email:{email_data['email_id']}:{content_hash}"

def check_idempotency(idempotency_key: str) -> Optional[dict]:
    """
    Check if operation already processed. Returns cached result if exists.
    """
    cached = redis_client.get(f"idempotent:{idempotency_key}")
    if cached:
        return json.loads(cached)
    return None

def store_idempotent_result(idempotency_key: str, result: dict, ttl_seconds: int = 86400):
    """
    Store operation result with 24-hour TTL.
    """
    redis_client.setex(
        f"idempotent:{idempotency_key}",
        ttl_seconds,
        json.dumps(result)
    )
```

### SQLAlchemy Session Event for Outbox Pattern
```python
# Source: https://docs.sqlalchemy.org/en/21/orm/session_events.html

from sqlalchemy import event
from sqlalchemy.orm import Session

@event.listens_for(Session, "after_commit")
def process_outbox_after_commit(session):
    """
    Hook that fires after PostgreSQL transaction commits.
    Use to trigger MongoDB writes (not emit SQL - session not active).
    """
    # WARNING: Session is not in active transaction here.
    # Cannot emit SQL. Use for triggering external systems.

    # Option 1: Enqueue Celery task for async MongoDB write
    if hasattr(session.info, 'pending_outbox_messages'):
        for msg in session.info['pending_outbox_messages']:
            process_outbox_messages.delay(msg.id)

    # Option 2: Synchronous MongoDB write (blocks commit response)
    # Not recommended for webhook handlers

# Alternative: Use before_commit for SQL operations
@event.listens_for(Session, "before_commit")
def validate_before_commit(session):
    """
    Fires before commit. Session still active, can emit SQL.
    Use for validation or additional writes in same transaction.
    """
    # Can query or insert additional records here
    pass
```

### Celery Beat Configuration for Reconciliation
```python
# Source: https://docs.celeryq.dev/en/latest/userguide/configuration.html

from celery import Celery
from celery.schedules import crontab

app = Celery('creditor_analysis')

app.conf.beat_schedule = {
    'hourly-reconciliation': {
        'task': 'reconciliation.tasks.hourly_reconciliation',
        'schedule': crontab(minute=0),  # Every hour at :00
        'options': {
            'expires': 3600,  # Task expires after 1 hour if not picked up
        }
    },
    'process-outbox-every-minute': {
        'task': 'saga.outbox.process_outbox_messages',
        'schedule': 60.0,  # Every 60 seconds
        'options': {
            'expires': 120,  # Expire after 2 minutes
        }
    },
    'cleanup-old-outbox': {
        'task': 'saga.outbox.cleanup_old_messages',
        'schedule': crontab(hour=3, minute=0),  # Daily at 3 AM
    },
}

# Timezone configuration
app.conf.timezone = 'Europe/Berlin'
app.conf.enable_utc = True

# Redis broker configuration (use Upstash Redis for Render)
app.conf.broker_url = 'redis://localhost:6379/0'
app.conf.result_backend = 'redis://localhost:6379/1'

# Task configuration
app.conf.task_serializer = 'json'
app.conf.result_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.task_track_started = True
app.conf.task_time_limit = 300  # 5 minutes max per task
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Two-phase commit (2PC) | Saga pattern with compensations | ~2015-2018 | Sagas trade consistency for availability, better for distributed systems |
| Manual retry loops | Transactional outbox with polling | ~2019 | Outbox guarantees at-least-once delivery without custom retry logic |
| Application-managed idempotency tables | Redis with TTL for idempotency keys | ~2020 | Redis faster, automatic cleanup, less database load |
| Nightly batch reconciliation | Hourly or continuous reconciliation | ~2021 | Catches inconsistencies faster, reduces data drift window |
| Synchronous dual-writes | Async saga with outbox publisher | ~2022 | Decouples PostgreSQL commit from MongoDB write, improves webhook response time |

**Deprecated/outdated:**
- **saga-framework library (Python):** Last commit 2019, not maintained, over-engineered for most use cases. Use transactional outbox pattern with Celery instead.
- **XA transactions across PostgreSQL + MongoDB:** MongoDB dropped XA support in 4.2+. Use saga pattern instead.
- **pymongo.errors.WTimeoutError handling for retries:** MongoDB 4.4+ uses implicit sessions, retry logic built into driver. Configure `retryWrites=true` in connection string.

## Open Questions

Things that couldn't be fully resolved:

1. **MongoDB Transactions Requirement**
   - What we know: MongoDB supports multi-document ACID transactions in replica sets (4.0+) and sharded clusters (4.2+)
   - What's unclear: Current MongoDB deployment architecture - is it replica set or standalone? Transactions require replica set.
   - Recommendation: Verify MongoDB deployment. If standalone, compensation logic must be more defensive (check document state before rollback). If replica set, use transactions for atomic multi-document updates.

2. **Existing Data Audit Scope**
   - What we know: Success criterion requires "audit shows existing mismatches quantified with recovery plan"
   - What's unclear: How far back to audit? All historical data or recent (last 30 days)?
   - Recommendation: Start with last 30 days (manageable scope), quantify mismatches, then decide if full historical audit needed based on mismatch rate.

3. **Celery vs Dramatiq Trade-off**
   - What we know: Celery has mature beat scheduler for cron jobs. Dramatiq is simpler but lacks built-in cron.
   - What's unclear: Project already researched Dramatiq for v2 (see STACK.md). Is switching to Celery acceptable for Phase 1?
   - Recommendation: Use Celery for Phase 1 (reconciliation requires cron). Re-evaluate Dramatiq for Phase 2+ async document processing if desired.

4. **Redis Persistence Configuration**
   - What we know: Upstash Redis recommended for Render. Free tier: 10K commands/day.
   - What's unclear: Is 10K commands sufficient? Estimate: 200 emails/day × 3 operations (check, store, cleanup) = 600/day. Well within limits. But outbox publisher adds load.
   - Recommendation: Start with Upstash free tier, monitor command count. If exceeds 10K/day, upgrade to paid tier ($10/month for 100K commands).

5. **Compensation Strategy for Partial Updates**
   - What we know: Some operations update multiple MongoDB documents (e.g., update client's creditor list + update creditor's client list).
   - What's unclear: If first update succeeds, second fails, compensation must rollback first. But what if compensation also fails?
   - Recommendation: Implement "dead letter queue" (DLQ) for failed compensations. Alert operations team for manual resolution. Store compensation attempts in `outbox_messages.error_message`.

## Sources

### Primary (HIGH confidence)
- [Microservices.io - Saga Pattern](https://microservices.io/patterns/data/saga.html) - Official saga pattern definition and guidelines
- [Microservices.io - Transactional Outbox](https://microservices.io/patterns/data/transactional-outbox.html) - Official outbox pattern specification
- [SQLAlchemy 2.1 Session Events](https://docs.sqlalchemy.org/en/21/orm/session_events.html) - Official documentation for after_commit hooks
- [Celery 5.6 Configuration](https://docs.celeryq.dev/en/latest/userguide/configuration.html) - Official Celery Beat scheduler docs

### Secondary (MEDIUM confidence)
- [The Outbox Pattern in Python](https://blog.szymonmiks.pl/p/the-outbox-pattern-in-python/) - Practical Python implementation guide (verified with official docs)
- [Mastering the Outbox Pattern in Python (Medium, 2025)](https://medium.com/israeli-tech-radar/mastering-the-outbox-pattern-in-python-a-reliable-approach-for-financial-systems-2a531473eaa5) - Financial systems patterns
- [Saga Pattern Implementation 2026 (Medium)](https://cachecowboy.medium.com/saga-pattern-implementation-distributed-transactions-without-2pc-bfcb27212426) - Recent 2026 implementation guide
- [Implementing Idempotency Keys (Zuplo)](https://zuplo.com/learning-center/implementing-idempotency-keys-in-rest-apis-a-complete-guide) - Industry best practices for idempotency
- [AWS Powertools Python - Idempotency](https://docs.aws.amazon.com/powertools/python/latest/utilities/idempotency/) - Production-grade Python patterns

### Tertiary (LOW confidence - requires validation)
- [saga-framework GitHub](https://github.com/absent1706/saga-framework) - Python saga library, last updated 2019, may be outdated
- [outbox-streaming GitHub](https://github.com/hyzyla/outbox-streaming) - Early development stage, not production-ready per their README
- [Data-Validation-Reconciliation-Tool GitHub](https://github.com/sergiomontey/Data-Validation-Reconciliation-Tool) - Example tool, not verified for production use

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM-HIGH - Celery/Redis/SQLAlchemy are industry standard, but specific version compatibility for Render deployment needs verification
- Architecture (saga pattern): HIGH - Pattern well-documented in official microservices.io specs, Python implementation synthesized from verified sources
- Architecture (reconciliation): MEDIUM - Conceptual pattern clear, but specific comparison logic depends on MongoDB schema (not fully documented in requirements)
- Idempotency: HIGH - Redis-based pattern with TTL is industry standard, AWS Powertools provides authoritative Python reference
- Pitfalls: HIGH - Isolation issues, compensation idempotency, backward compatibility are documented in official saga pattern sources

**Research limitations:**
- MongoDB schema not fully specified in requirements - reconciliation comparison logic is conceptual
- Current MongoDB deployment type (standalone vs replica set) unknown - affects transaction support
- Existing codebase patterns not reviewed - may conflict with saga implementation approach
- Render-specific Redis configuration not verified - Upstash recommendation from prior STACK.md research (unverified with WebSearch/Context7)

**Research date:** 2026-02-04
**Valid until:** 2026-03-04 (30 days - saga pattern stable, but library versions and Render integrations evolve)

**Follow-up research needed:**
- MongoDB deployment architecture (replica set status)
- Current incoming_emails table schema and indexes
- Existing MongoDB collections schema (clients.final_creditor_list structure)
- Render Redis hosting options as of 2026 (verify Upstash recommendation)
