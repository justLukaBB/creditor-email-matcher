# Phase 9: Production Hardening & Monitoring - Research

**Researched:** 2026-02-06
**Domain:** Production operations - structured logging, metrics, circuit breakers, error tracking, integration testing
**Confidence:** HIGH

## Summary

Production hardening for async Python services requires coordinating structured logging with correlation IDs, circuit breakers for external dependencies, metrics collection without additional infrastructure, error tracking with rich context, and comprehensive integration testing that validates the entire pipeline. The user has locked in specific architectural decisions: JSON logging to stdout, PostgreSQL for metrics storage, email notifications for circuit breaker events, and minimal operational overhead.

The standard approach uses python-json-logger for JSON structured logging, asgi-correlation-id for request tracking through async contexts, pybreaker for circuit breakers with custom email listeners, Sentry SDK for error tracking, and Dramatiq's built-in StubBroker for integration testing. The existing codebase already demonstrates the rollup pattern with PromptPerformanceMetrics → PromptPerformanceDaily, which should be extended to operational metrics.

Key insight: Python's contextvars (not threading.local) is essential for correlation ID propagation in async environments. Circuit breaker state changes must be captured via listeners to trigger email alerts. Integration tests must use StubBroker and transactional rollback to ensure isolation.

**Primary recommendation:** Use asgi-correlation-id middleware for automatic correlation ID injection, pybreaker with custom CircuitBreakerListener for email alerts, extend existing metrics rollup pattern for operational data, and leverage Dramatiq's StubBroker for end-to-end integration tests.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| python-json-logger | 2.0.7+ (or nhairs fork) | JSON structured logging | Industry standard for machine-parseable logs, works with all log aggregators |
| asgi-correlation-id | latest | Request ID propagation | Built for ASGI/FastAPI, uses contextvars for async-safe correlation tracking |
| pybreaker | 1.4.1 | Circuit breaker pattern | Mature implementation with listener pattern for notifications, async support |
| sentry-sdk | latest | Error tracking with context | Official SDK with automatic FastAPI integration, rich context support |
| pytest + pytest-asyncio | 8.x + 0.23.0+ | Async integration testing | Standard testing stack, handles async fixtures and assertions |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Dramatiq StubBroker | 2.0+ (built-in) | Actor testing without Redis | Unit and integration testing of async job processing |
| contextvars | stdlib | Async-safe context storage | Correlation ID propagation across async boundaries |
| logging.handlers | stdlib | Stream routing (stdout/stderr) | Production log routing by severity level |
| TimescaleDB extension | optional | Time-series optimization | If metrics queries become slow (not needed initially) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pybreaker | circuitbreaker or aiobreaker | circuitbreaker simpler but no distributed state; aiobreaker more async-native but less mature |
| asgi-correlation-id | fastapi-request-context | fastapi-request-context broader scope but more complex setup |
| python-json-logger | structlog | structlog more powerful but steeper learning curve, may be overkill for this use case |
| PostgreSQL metrics | Prometheus + external infra | Prometheus industry standard but requires additional deployment, user explicitly chose no additional infra |

**Installation:**
```bash
# Core monitoring stack
pip install python-json-logger>=2.0.7  # or nhairs-python-json-logger for maintained fork
pip install asgi-correlation-id
pip install pybreaker>=1.4.1
pip install sentry-sdk[fastapi]

# Testing (already installed)
pip install pytest>=8.0.0
pip install pytest-asyncio>=0.23.0
```

## Architecture Patterns

### Recommended Project Structure
```
app/
├── services/
│   ├── monitoring/
│   │   ├── __init__.py
│   │   ├── logging.py          # JSON formatter + correlation ID setup
│   │   ├── circuit_breakers.py # Breaker instances + email listener
│   │   ├── metrics.py           # Metrics collection service
│   │   └── error_tracking.py   # Sentry context helpers
│   └── ...
├── models/
│   ├── operational_metrics.py   # Queue depth, processing time, error rates
│   └── ...
├── middleware/
│   └── correlation_id.py        # Correlation ID middleware setup
└── ...
tests/
├── integration/
│   ├── conftest.py              # Fixtures for stub broker, test DB
│   ├── test_e2e_pipeline.py     # Full webhook → match flow
│   └── fixtures/                # Test email samples
└── ...
```

### Pattern 1: Correlation ID Propagation with contextvars
**What:** Use Python's contextvars to propagate correlation IDs across async boundaries (HTTP request → Dramatiq actor → downstream service calls)

**When to use:** All production services with async processing

**Example:**
```python
# Source: https://github.com/snok/asgi-correlation-id
from fastapi import FastAPI
from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id

app = FastAPI()
app.add_middleware(CorrelationIdMiddleware)

# In any code (including Dramatiq actors)
def log_with_context():
    current_id = correlation_id.get()
    logger.info("Processing", extra={"correlation_id": current_id})
```

### Pattern 2: Circuit Breaker with Email Notifications
**What:** Wrap external service calls with circuit breakers that send email alerts when opening

**When to use:** Claude API, MongoDB, GCS - any external dependency with potential cascading failure

**Example:**
```python
# Source: https://github.com/danielfm/pybreaker
import pybreaker
from app.services.email_notifier import send_alert

class EmailNotificationListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):
        if new_state == pybreaker.STATE_OPEN:
            send_alert(
                subject=f"Circuit Breaker Opened: {cb.name}",
                body=f"Service {cb.name} has failed {cb.fail_counter} times. "
                     f"Circuit opened for {cb.recovery_timeout}s."
            )

# Create breaker with listener
claude_breaker = pybreaker.CircuitBreaker(
    name="claude_api",
    fail_max=5,
    reset_timeout=60,
    listeners=[EmailNotificationListener()]
)

@claude_breaker
async def call_claude_api(payload):
    # API call here
    pass
```

### Pattern 3: Metrics Rollup (Raw → Daily Aggregates)
**What:** Store high-resolution metrics with TTL, aggregate to daily summaries for long-term retention

**When to use:** Operational metrics (queue depth, processing time, error rates) - already established pattern in Phase 8

**Example:**
```python
# Source: Existing pattern from app/models/prompt_metrics.py
# Raw metrics table (30-day retention)
class OperationalMetrics(Base):
    __tablename__ = "operational_metrics"

    id = Column(Integer, primary_key=True)
    metric_type = Column(String(50), nullable=False, index=True)  # queue_depth, processing_time, error_rate
    metric_value = Column(Float, nullable=False)
    labels = Column(JSON, nullable=True)  # {"queue": "default", "actor": "email_processor"}
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

# Daily rollup (permanent retention)
class OperationalMetricsDaily(Base):
    __tablename__ = "operational_metrics_daily"

    id = Column(Integer, primary_key=True)
    metric_type = Column(String(50), nullable=False)
    date = Column(Date, nullable=False)

    # Aggregates
    avg_value = Column(Float, nullable=False)
    min_value = Column(Float, nullable=False)
    max_value = Column(Float, nullable=False)
    p95_value = Column(Float, nullable=True)
    sample_count = Column(Integer, nullable=False)

    __table_args__ = (
        Index('idx_ops_daily_unique', 'metric_type', 'date', unique=True),
    )
```

### Pattern 4: Integration Testing with StubBroker
**What:** Use Dramatiq's StubBroker + pytest fixtures for testing full pipeline without Redis

**When to use:** Integration tests covering webhook → actor → database

**Example:**
```python
# Source: https://dramatiq.io/guide.html
import pytest
from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker

@pytest.fixture
def stub_broker():
    """Provides clean broker state per test"""
    from app.worker import broker  # StubBroker instance
    broker.flush_all()
    return broker

@pytest.fixture
def stub_worker(stub_broker):
    """Starts worker, ensures cleanup"""
    worker = Worker(stub_broker, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()

def test_email_processing_pipeline(stub_broker, stub_worker, test_db):
    # Enqueue actor
    process_email.send(email_id=123)

    # Wait for completion
    stub_broker.join(process_email.queue_name)
    stub_worker.join()

    # Assert database state
    assert test_db.query(CreditorInquiry).count() == 1
```

### Pattern 5: Structured Logging with Custom Fields
**What:** Extend JSON formatter to inject correlation IDs and other context into every log entry

**When to use:** All production logging

**Example:**
```python
# Source: https://github.com/madzak/python-json-logger
import logging
from pythonjsonlogger import jsonlogger
from asgi_correlation_id.context import correlation_id

class CorrelationJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)

        # Add correlation ID
        log_record['correlation_id'] = correlation_id.get() or 'none'

        # Add custom fields
        log_record['service'] = 'creditor-answer-analysis'
        log_record['environment'] = os.getenv('ENV', 'development')

# Configure logging
handler = logging.StreamHandler()
handler.setFormatter(CorrelationJsonFormatter())
logging.root.addHandler(handler)
logging.root.setLevel(logging.INFO)
```

### Anti-Patterns to Avoid
- **threading.local() for correlation IDs:** Causes context leakage in async/await code. Use contextvars instead.
- **Circuit breakers without timeout:** Forgetting reset_timeout means circuits stay open forever, requiring manual intervention.
- **Metrics without retention policy:** Raw metrics tables grow unbounded. Always define TTL and rollup strategy.
- **Integration tests with real Redis:** Makes tests slow and non-deterministic. Use StubBroker.
- **Sentry without context:** Generic error messages are useless. Always add email_id, job_id, agent tags.
- **Mixing stdout/stderr in production:** All logs to stdout makes filtering harder. Route INFO→stdout, ERROR→stderr.
- **Hardcoded correlation ID keys:** FastAPI background tasks run after middleware. Must manually propagate correlation_id.get() into task context.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Correlation ID middleware | Custom FastAPI middleware with UUID generation | asgi-correlation-id | Handles header parsing, validation, propagation to response headers, logging integration - edge cases solved |
| Circuit breaker state machine | Custom failure counter + timer logic | pybreaker or circuitbreaker | State transitions (closed→open→half-open), exponential backoff, thread-safe counters, listener pattern built-in |
| JSON log formatting | Manual json.dumps() in log messages | python-json-logger | Handles exception formatting, timestamp normalization, extra field merging, circular reference detection |
| Async test fixtures | Custom setup/teardown in each test | pytest-asyncio + pytest fixtures | Async fixture lifecycle, proper cleanup on failure, fixture dependency resolution |
| Time-series rollup queries | Custom SQL with GROUP BY and date buckets | Established rollup pattern (see prompt_metrics.py) | Percentile calculations, idempotent re-aggregation, unique constraints prevent duplicates |
| Error context propagation | Manual try/except with context building | Sentry SDK breadcrumbs + set_context | Automatic stack traces, local variables, breadcrumb trail, user context, tags for filtering |

**Key insight:** Observability infrastructure has well-trodden patterns. The complexity is in edge cases (async context leakage, circuit breaker race conditions, metrics retention policies, test isolation). Use battle-tested libraries.

## Common Pitfalls

### Pitfall 1: Correlation ID Lost in Background Tasks
**What goes wrong:** FastAPI background tasks run after middleware completes. The correlation_id context var is reset, logs show "none" for correlation ID.

**Why it happens:** ASGI middleware lifespan ends when HTTP response is sent. Background tasks execute in a new context.

**How to avoid:** Manually capture correlation_id.get() before enqueueing task, pass as parameter, set in actor:
```python
from asgi_correlation_id.context import correlation_id

# In endpoint
current_id = correlation_id.get()
process_email.send(email_id=123, correlation_id=current_id)

# In actor
@dramatiq.actor
def process_email(email_id: int, correlation_id: str):
    correlation_id.set(correlation_id)  # Re-set for this execution context
    # Now all logs in this actor have the correlation ID
```

**Warning signs:** Logs from Dramatiq actors missing correlation_id field, or showing different ID than originating request.

### Pitfall 2: Circuit Breaker Opens But No Alert Sent
**What goes wrong:** Circuit breaker opens after threshold failures, but admin doesn't know until checking logs manually.

**Why it happens:** Circuit breaker libraries provide state change hooks, but notifications must be implemented separately.

**How to avoid:** Create CircuitBreakerListener subclass that sends email in state_change() callback when new_state == STATE_OPEN. Test the listener separately.

**Warning signs:** Circuit breaker exceptions in logs, but no corresponding notification emails received.

### Pitfall 3: Integration Tests Interfere with Each Other
**What goes wrong:** Test A creates database records. Test B expects empty database. Test B fails intermittently depending on execution order.

**Why it happens:** Database transactions not rolled back between tests, or fixture scope too broad (session vs function).

**How to avoid:** Use function-scoped fixtures with transaction rollback:
```python
@pytest.fixture(scope="function")
def test_db(engine):
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()  # Discard all changes
    connection.close()
```

**Warning signs:** Tests pass individually but fail when run together. Database state leaks between tests.

### Pitfall 4: Metrics Table Bloat
**What goes wrong:** Raw metrics table grows to millions of rows, queries become slow, database storage fills up.

**Why it happens:** No retention policy defined. Rollup job aggregates data but doesn't delete raw records.

**How to avoid:**
- Define explicit retention in model docstring (e.g., "30-day retention")
- Create scheduled job to DELETE raw records older than retention period
- Add database index on recorded_at for efficient deletion
- Monitor table size with alerts

**Warning signs:** Slow metrics queries, increasing database storage usage, no cleanup job in APScheduler.

### Pitfall 5: Sentry Errors Without Context
**What goes wrong:** Sentry shows "NoneType object has no attribute X" with no way to identify which email or job triggered the error.

**Why it happens:** Context not added to Sentry scope before error occurs.

**How to avoid:** Set context at the start of each major operation:
```python
import sentry_sdk

# In actor or endpoint
sentry_sdk.set_context("job", {
    "job_id": message.message_id,
    "actor": "process_email",
    "email_id": email_id
})
sentry_sdk.set_tag("email_id", email_id)
sentry_sdk.set_user({"id": email.from_email})
```

**Warning signs:** Sentry issues with "No additional data" or generic tracebacks that can't be reproduced.

### Pitfall 6: Circuit Breaker Misconfiguration
**What goes wrong:** Circuit opens too aggressively (e.g., fail_max=1) causing false positives, or too conservatively (fail_max=50) allowing cascading failures.

**Why it happens:** Threshold and timeout not tuned to actual service characteristics.

**How to avoid:**
- Start with fail_max=5 (user decision), reset_timeout=60s
- Monitor actual failure patterns in production
- Adjust based on observed MTTR (mean time to recovery) of external services
- Test circuit breaker behavior with fault injection

**Warning signs:** Frequent circuit breaker openings with no actual service degradation, or prolonged outages despite circuit breaker.

### Pitfall 7: Logging INFO in Development, DEBUG in Production
**What goes wrong:** Opposite of intended - debug logs fill production storage, but lack detail when debugging issues locally.

**Why it happens:** Environment variable not set correctly, or logging level hard-coded.

**How to avoid:**
```python
import os

log_level = os.getenv("LOG_LEVEL", "INFO")  # INFO for production (user decision)
logging.root.setLevel(getattr(logging, log_level))
```

Set LOG_LEVEL=DEBUG locally for development.

**Warning signs:** Production logs overwhelming with debug messages, or inability to debug issues locally.

## Code Examples

Verified patterns from official sources:

### Complete Logging Setup (JSON + Correlation ID)
```python
# Source: https://github.com/madzak/python-json-logger + https://github.com/snok/asgi-correlation-id
import logging
import sys
from pythonjsonlogger import jsonlogger
from asgi_correlation_id.context import correlation_id

class CorrelationJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter with automatic correlation ID injection"""
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record['correlation_id'] = correlation_id.get() or 'none'
        log_record['service'] = 'creditor-answer-analysis'

def setup_logging():
    """Configure structured JSON logging to stdout"""
    handler = logging.StreamHandler(sys.stdout)
    formatter = CorrelationJsonFormatter(
        '%(timestamp)s %(level)s %(name)s %(message)s',
        rename_fields={
            'timestamp': 'asctime',
            'level': 'levelname'
        }
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)  # User decision
```

### Circuit Breaker with Email Alerts
```python
# Source: https://github.com/danielfm/pybreaker
import pybreaker
from app.services.email_notifier import EmailNotifier

class CircuitBreakerEmailListener(pybreaker.CircuitBreakerListener):
    """Sends email alert when circuit breaker opens"""

    def __init__(self, email_notifier: EmailNotifier):
        self.email_notifier = email_notifier

    def state_change(self, cb, old_state, new_state):
        if new_state == pybreaker.STATE_OPEN:
            self.email_notifier.send_alert(
                subject=f"ALERT: Circuit Breaker Opened - {cb.name}",
                body=f"""
                Circuit breaker '{cb.name}' has opened after {cb.fail_counter} consecutive failures.

                The circuit will remain open for {cb.reset_timeout} seconds to allow recovery.

                Investigate the downstream service immediately.
                """
            )

# Create breakers (user decision: 5 failures, 60s timeout)
email_listener = CircuitBreakerEmailListener(email_notifier)

claude_breaker = pybreaker.CircuitBreaker(
    name="claude_api",
    fail_max=5,
    reset_timeout=60,
    listeners=[email_listener]
)

mongodb_breaker = pybreaker.CircuitBreaker(
    name="mongodb",
    fail_max=5,
    reset_timeout=60,
    listeners=[email_listener]
)

gcs_breaker = pybreaker.CircuitBreaker(
    name="google_cloud_storage",
    fail_max=5,
    reset_timeout=60,
    listeners=[email_listener]
)
```

### Metrics Collection Service
```python
# Source: Existing pattern from app/models/prompt_metrics.py
from sqlalchemy.orm import Session
from app.models.operational_metrics import OperationalMetrics
import time

class MetricsCollector:
    """Collects operational metrics to PostgreSQL"""

    def __init__(self, db: Session):
        self.db = db

    def record_queue_depth(self, queue_name: str, depth: int):
        """Record current queue depth"""
        metric = OperationalMetrics(
            metric_type="queue_depth",
            metric_value=float(depth),
            labels={"queue": queue_name}
        )
        self.db.add(metric)
        self.db.commit()

    def record_processing_time(self, actor_name: str, duration_ms: int):
        """Record actor processing duration"""
        metric = OperationalMetrics(
            metric_type="processing_time",
            metric_value=float(duration_ms),
            labels={"actor": actor_name}
        )
        self.db.add(metric)
        self.db.commit()

    def record_error(self, actor_name: str, error_type: str):
        """Record actor failure"""
        metric = OperationalMetrics(
            metric_type="error_rate",
            metric_value=1.0,
            labels={"actor": actor_name, "error_type": error_type}
        )
        self.db.add(metric)
        self.db.commit()

# Usage in actor
@dramatiq.actor
def process_email(email_id: int):
    start_time = time.time()
    metrics = MetricsCollector(db)

    try:
        # Processing logic
        pass
    except Exception as e:
        metrics.record_error("process_email", type(e).__name__)
        raise
    finally:
        duration_ms = int((time.time() - start_time) * 1000)
        metrics.record_processing_time("process_email", duration_ms)
```

### Integration Test with StubBroker
```python
# Source: https://dramatiq.io/guide.html
import pytest
from dramatiq import Worker
from app.worker import broker  # StubBroker instance
from app.actors.email_processor import process_email

@pytest.fixture
def stub_broker():
    broker.flush_all()
    return broker

@pytest.fixture
def stub_worker(stub_broker):
    worker = Worker(stub_broker, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()

@pytest.fixture
def test_db(engine):
    """Isolated test database with transaction rollback"""
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()

def test_email_processing_creates_inquiry(stub_broker, stub_worker, test_db):
    # Create test email
    email = IncomingEmail(
        subject="Payment confirmation",
        body="Your payment of $1000 was received",
        from_email="test@example.com"
    )
    test_db.add(email)
    test_db.commit()

    # Enqueue processing
    process_email.send(email_id=email.id)

    # Wait for completion
    stub_broker.join(process_email.queue_name)
    stub_worker.join()

    # Assert results
    inquiry = test_db.query(CreditorInquiry).filter_by(email_id=email.id).first()
    assert inquiry is not None
    assert inquiry.amount == 1000.0
```

### Sentry Context in Actors
```python
# Source: https://docs.sentry.io/platforms/python/enriching-events/
import sentry_sdk
import dramatiq

@dramatiq.actor
def process_email(email_id: int, correlation_id: str):
    # Set correlation ID context
    correlation_id.set(correlation_id)

    # Add Sentry context for debugging
    sentry_sdk.set_context("job", {
        "job_id": dramatiq.get_current_message().message_id,
        "actor": "process_email",
        "email_id": email_id,
        "correlation_id": correlation_id
    })
    sentry_sdk.set_tag("email_id", email_id)
    sentry_sdk.set_tag("actor", "process_email")

    # Add breadcrumb for operations
    sentry_sdk.add_breadcrumb(
        category="processing",
        message=f"Starting email {email_id} processing",
        level="info"
    )

    try:
        # Processing logic
        pass
    except Exception as e:
        sentry_sdk.add_breadcrumb(
            category="error",
            message=f"Failed at step X",
            level="error"
        )
        raise  # Sentry automatically captures with all context
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| thread-local storage (threading.local) | contextvars (contextvars.ContextVar) | Python 3.7 (2018) | Async-safe context propagation - essential for FastAPI/asyncio |
| Prometheus + external infrastructure | PostgreSQL time-series tables | User decision 2026 | Simpler deployment, no additional services, leverages existing DB |
| Plain text logs | Structured JSON logs | Industry shift 2015+ | Machine-parseable, integrates with log aggregators, queryable |
| Manual transaction rollback in tests | pytest fixtures with context managers | pytest 3.0+ (2016) | Automatic cleanup, proper teardown on failure, fixture composition |
| Celery + RabbitMQ | Dramatiq + Redis | Project decision Phase 2 | Simpler deployment, lower memory footprint, built-in testing support |
| Sentry Python SDK < 1.0 | Sentry Python SDK 2.x | 2023 | Better async support, automatic FastAPI integration, improved context management |

**Deprecated/outdated:**
- **python-json-logger original repo:** Archived December 2024. Use nhairs/python-json-logger fork for ongoing support, or migrate to structlog for new projects.
- **PyBreaker Redis state storage:** Works but adds complexity. For single-instance deploys (Render), in-memory state sufficient.
- **TimescaleDB for small-scale metrics:** Overkill for 200 emails/day. Plain PostgreSQL with rollup pattern sufficient until scale increases 10x.

## Open Questions

Things that couldn't be fully resolved:

1. **Correlation ID granularity: email_id vs email_id + job_id + agent**
   - What we know: email_id provides request-level tracing. Adding job_id + agent provides step-level tracing within pipeline.
   - What's unclear: Whether step-level granularity is worth the complexity for debugging.
   - Recommendation: Start with email_id only. Logs already contain actor names and timestamps for step identification. Add job_id + agent if debugging proves difficult.

2. **Metrics retention: Raw data 30 days vs 7 days**
   - What we know: Prompt metrics use 30-day retention. Operational metrics may have different access patterns.
   - What's unclear: How often raw operational metrics are queried beyond daily rollups.
   - Recommendation: Match prompt metrics pattern - 30-day raw retention, daily rollups permanent. Consistent retention simplifies operations.

3. **Circuit breaker coverage: Which services beyond Claude, MongoDB, GCS?**
   - What we know: External dependencies (Claude API, MongoDB, GCS) need circuit breakers per user decision.
   - What's unclear: Whether internal services (email sending via SMTP) need circuit breakers.
   - Recommendation: Start with three external services. Add SMTP circuit breaker if email sending failures cause cascading issues (e.g., notification storms blocking actor processing).

4. **Processing report delivery: Per-email vs daily digest**
   - What we know: Reports should show "what extracted, what's missing, confidence per field" per user decision.
   - What's unclear: Whether reports stored per-email in database (queryable) or sent as daily digest email (push notification).
   - Recommendation: Store per-email in database (enables on-demand querying, audit trail). Add optional daily digest email if admin wants proactive summaries.

5. **Integration test coverage: Full end-to-end vs critical paths only**
   - What we know: Integration tests should cover "webhook through final write" per success criteria.
   - What's unclear: Whether to test every email type variant or focus on happy path + error cases.
   - Recommendation: Prioritize critical paths (new email → extraction → match → storage, duplicate email → idempotency, malformed email → error handling). Add email type variants as bugs surface.

## Sources

### Primary (HIGH confidence)
- python-json-logger: https://github.com/madzak/python-json-logger - Installation, usage, custom fields
- circuitbreaker: https://pypi.org/project/circuitbreaker/ - v2.1.3, configuration, async support
- pybreaker: https://github.com/danielfm/pybreaker - v1.4.1, listener interface, state_change callback
- asgi-correlation-id: https://github.com/snok/asgi-correlation-id - FastAPI setup, background task propagation
- Sentry Python SDK: https://docs.sentry.io/platforms/python/ - Installation, context enrichment
- Dramatiq testing: https://dramatiq.io/guide.html - StubBroker, worker fixtures, exception handling
- Python logging cookbook: https://docs.python.org/3/howto/logging-cookbook.html - Stream routing, dictConfig
- Existing codebase: app/models/prompt_metrics.py - Rollup pattern reference

### Secondary (MEDIUM confidence)
- [Python Logging Best Practices 2026](https://www.carmatec.com/blog/python-logging-best-practices-complete-guide/) - JSON logging rationale
- [Better Stack: Python Logging Best Practices](https://betterstack.com/community/guides/logging/python/python-logging-best-practices/) - Correlation ID importance, ISO 8601 timestamps
- [Python Structured Logging Guide](https://newrelic.com/blog/log/python-structured-logging) - Correlation ID with contextvars pattern
- [Dramatiq Grafana Dashboard](https://grafana.com/grafana/dashboards/3692-dramatiq/) - Prometheus middleware metrics
- [FastAPI Testing with Pytest](https://pytest-with-eric.com/pytest-advanced/pytest-fastapi-testing/) - Database transaction rollback fixtures
- [SAP: Thread-Safe Structured Logging for FastAPI](https://community.sap.com/t5/artificial-intelligence-blogs-posts/implementing-thread-safe-structured-logging-for-python-fastapi/ba-p/14292907) - contextvars vs threading.local
- [Medium: Circuit Breaker Pattern in Python](https://medium.com/@abhinav.manoj1503/circuit-breaker-pattern-in-microservices-using-flask-cf19e9ed6147) - Notification implementation approach
- [TimescaleDB Retention Policies](https://www.slingacademy.com/article/timescaledb-understanding-time-series-data-retention-policies-in-postgresql/) - Hypertables, compression, retention
- [Time Series Schema Design](https://cloud.google.com/bigtable/docs/schema-design-time-series) - Time bucketing patterns

### Tertiary (LOW confidence)
- [Data Archiving in PostgreSQL](https://dataegret.com/2025/05/data-archiving-and-retention-in-postgresql-best-practices-for-large-datasets/) - Retention strategies (not PostgreSQL-specific)
- [Context Variables structlog](https://www.structlog.org/en/stable/contextvars.html) - Alternative to python-json-logger (not using structlog)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries verified via official docs/GitHub, versions confirmed, FastAPI integration patterns documented
- Architecture: HIGH - Patterns verified in official documentation, existing codebase demonstrates rollup pattern, StubBroker testing documented
- Pitfalls: MEDIUM - Common issues documented in community sources (Medium, Better Stack) but not all verified in official docs. Based on established async Python patterns and known contextvars behavior.

**Research date:** 2026-02-06
**Valid until:** 2026-03-06 (30 days - stable domain, Python logging/monitoring patterns mature)
