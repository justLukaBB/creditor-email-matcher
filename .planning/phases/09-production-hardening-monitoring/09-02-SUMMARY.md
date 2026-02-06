---
phase: 09-production-hardening-monitoring
plan: 02
subsystem: monitoring
tags: [circuit-breaker, fault-tolerance, email-alerts, pybreaker, resilience]

dependency_graph:
  requires: ["09-01"]
  provides: ["circuit_breaker_infrastructure"]
  affects: ["09-05"]

tech_stack:
  added:
    - pybreaker>=1.4.1
  patterns:
    - circuit-breaker-pattern
    - lazy-initialization
    - email-notification-listener

key_files:
  created:
    - app/services/monitoring/circuit_breakers.py
  modified:
    - requirements.txt
    - app/config.py
    - app/services/monitoring/__init__.py

decisions:
  - id: CIRCUIT_BREAKER_THRESHOLDS
    decision: "5 consecutive failures trigger circuit open, 60 second auto-recovery"
    rationale: "Balance between quick failure detection and avoiding false positives from transient errors"
    alternatives: ["3 failures/30s (too sensitive)", "10 failures/120s (too slow)"]

metrics:
  duration: "4m 24s"
  completed: "2026-02-06"
---

# Phase 09 Plan 02: Circuit Breakers with Email Alerts Summary

**One-liner:** Pybreaker-based circuit breakers for Claude API, MongoDB, and GCS with email notifications on failure isolation

## Overview

Implemented circuit breaker pattern for all external service dependencies to prevent cascading failures and provide automatic recovery. When services experience consecutive failures, the circuit opens to stop sending requests, protecting the application from timeout exhaustion and resource waste. Administrators receive email alerts when circuits open for immediate investigation.

## What Was Built

### 1. Circuit Breaker Configuration (Task 1)

**Added to requirements.txt:**
- pybreaker>=1.4.1 for circuit breaker implementation

**Added to app/config.py Settings:**
- `circuit_breaker_fail_max: int = 5` - consecutive failures before opening circuit
- `circuit_breaker_reset_timeout: int = 60` - seconds before auto-recovery attempt
- `circuit_breaker_alert_email: Optional[str]` - alert recipient (falls back to admin_email)

All configurable via environment variables: `CIRCUIT_BREAKER_FAIL_MAX`, `CIRCUIT_BREAKER_RESET_TIMEOUT`, `CIRCUIT_BREAKER_ALERT_EMAIL`.

**USER DECISION locked:** 5 failures / 60 seconds chosen to balance quick detection with tolerance for transient errors.

### 2. Circuit Breaker Implementation (Task 2)

**Created app/services/monitoring/circuit_breakers.py:**

**CircuitBreakerEmailListener class:**
- Extends `pybreaker.CircuitBreakerListener`
- `state_change()` method logs all state transitions at WARNING level
- Sends email alert when circuit enters `STATE_OPEN`
- Email includes: service name, failure count, reset timeout, investigation checklist
- Uses SMTP settings from app.config (smtp_host, smtp_port, smtp_username, smtp_password)
- Graceful email failure handling - logs error but doesn't crash

**Circuit breaker instances:**
- `claude_api` - protects LLM API calls
- `mongodb` - protects document database operations
- `google_cloud_storage` - protects attachment storage operations

**Lazy initialization pattern:**
- Module-level `_email_listener`, `_claude_breaker`, `_mongodb_breaker`, `_gcs_breaker` variables
- `get_breaker(service_name)` function initializes on first access
- Convenience functions: `get_claude_breaker()`, `get_mongodb_breaker()`, `get_gcs_breaker()`
- Prevents import-time SMTP connections and configuration errors

### 3. Circuit Breaker Decorator (Task 2/3)

**Added to circuit_breakers.py:**

**`with_circuit_breaker(service_name)` decorator:**
```python
@with_circuit_breaker("claude")
def call_claude_api(payload):
    # API call implementation
    pass
```

- Uses `functools.wraps` to preserve function metadata
- Wraps function with `breaker.call()` for automatic failure tracking
- Raises `CircuitBreakerError` when circuit is open (service unavailable)

**Exception re-export:**
- `CircuitBreakerError` from pybreaker available for caller exception handling
- Allows callers to distinguish circuit breaker failures from service failures

**Updated app/services/monitoring/__init__.py:**
- Exported all breaker functions and classes
- Centralized access point for monitoring infrastructure

## Technical Decisions

### Circuit Breaker Thresholds

**Decision:** 5 consecutive failures, 60 second reset timeout

**Rationale:**
- 5 failures provides confidence that service is truly down (not transient blip)
- 60 seconds allows temporary issues to resolve without immediate retry storm
- Automatic recovery attempts prevent manual intervention for transient outages

**Alternatives considered:**
- 3 failures / 30s: Too sensitive, would trigger on network hiccups
- 10 failures / 120s: Too slow, wastes resources on failing requests

### Lazy Initialization

**Decision:** Initialize breakers on first use, not at import time

**Rationale:**
- Avoids import-time configuration errors (missing SMTP settings, admin email)
- Prevents SMTP connection attempts during module import
- Allows application to start even if email notification isn't configured
- Enables testing without full SMTP configuration

**Implementation:**
- Module-level `_breaker` variables default to `None`
- `get_breaker()` checks for `None` and initializes on first access
- Thread-safe due to Python GIL (no explicit locking needed for initialization)

### Email Alert Content

**Decision:** Include actionable investigation checklist in alert email

**Rationale:**
- On-call engineers need clear next steps during incidents
- Service name, failure count, timeout provide context for triage
- Investigation checklist reduces mean-time-to-recovery (MTTR)

**Content structure:**
1. Service identification (name, status)
2. Failure metrics (count, timeout)
3. Action items (investigate, check health, review logs, monitor)
4. Environment context (production vs staging)

## Verification Results

### Import Test
```bash
python -c "from app.services.monitoring import get_claude_breaker, get_mongodb_breaker, get_gcs_breaker, with_circuit_breaker"
# ✓ All imports successful
```

### Config Test
```bash
python -c "from app.config import settings; print(f'Circuit breaker: {settings.circuit_breaker_fail_max} failures, {settings.circuit_breaker_reset_timeout}s timeout')"
# ✓ Circuit breaker: 5 failures, 60s timeout
```

### Behavior Test
```python
from app.services.monitoring.circuit_breakers import get_claude_breaker
import pybreaker

cb = get_claude_breaker()
# Simulate 5 failures
for _ in range(5):
    try:
        cb.call(lambda: 1/0)
    except (ZeroDivisionError, pybreaker.CircuitBreakerError):
        pass

# ✓ State after 5 failures: CircuitOpenState
# ✓ Circuit breaker state change logged: "claude_api transitioned from closed to open"
```

## Integration Points

### Current Phase (09-production-hardening-monitoring)
- **09-01:** Structured logging with correlation ID - circuit breaker state changes logged with correlation context
- **09-03:** Operational metrics - circuit breaker events will be tracked in metrics (planned)
- **09-04:** Integration testing - circuit breakers testable via StubBroker pattern (planned)

### Future Integration (Plan 09-05)
Circuit breaker decorator will be applied to:
1. **Claude API calls** in extraction services:
   - `app/services/extraction/intent_classifier.py` - `classify_intent()`
   - `app/services/extraction/entity_extractor.py` - `extract_entities()`
   - `app/services/extraction/pdf_extractor.py` - `_extract_with_claude_vision()`
   - `app/services/extraction/image_extractor.py` - `extract_from_image()`

2. **MongoDB operations** in storage services:
   - `app/services/storage/mongodb_service.py` - `insert_creditor_answer()`, `update_creditor_answer()`

3. **GCS operations** in storage services:
   - `app/services/storage/gcs_handler.py` - `download_to_temp()`, `upload_from_temp()`

Integration pattern:
```python
@with_circuit_breaker("claude")
def call_claude_api(self, payload):
    # Existing API call implementation
    pass
```

Exception handling pattern:
```python
try:
    result = self.call_claude_api(payload)
except CircuitBreakerError:
    # Circuit is open - service unavailable
    logger.error("Claude API circuit breaker open - routing to manual review")
    return self._fallback_to_manual_review()
except Exception as e:
    # Service error - will count toward circuit breaker
    raise
```

## Deviations from Plan

None - plan executed exactly as written.

## Next Phase Readiness

**Phase 09 Plan 03: Operational Metrics**
- ✓ Circuit breaker infrastructure in place
- ✓ Logging integration available (correlation ID, structured JSON)
- Ready to track circuit breaker events in operational metrics

**Phase 09 Plan 04: Integration Testing**
- ✓ Circuit breaker instances testable
- ✓ Can verify circuit opens/closes correctly
- Ready to add circuit breaker tests to integration suite

**Deployment Prerequisites:**
- Install dependencies: `pip install -r requirements.txt` (includes pybreaker>=1.4.1)
- Set environment variables (optional, defaults work):
  - `CIRCUIT_BREAKER_FAIL_MAX=5` (default)
  - `CIRCUIT_BREAKER_RESET_TIMEOUT=60` (default)
  - `CIRCUIT_BREAKER_ALERT_EMAIL` (falls back to ADMIN_EMAIL)
- Set SMTP configuration if email alerts desired:
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
  - `ADMIN_EMAIL` (alert recipient)
- Restart application - circuit breakers initialize on first service call

**Operational Notes:**
- Circuit breakers are passive until first service call (lazy initialization)
- Email alerts require SMTP configuration (gracefully degraded if missing)
- Monitor circuit breaker state changes in application logs
- Circuit opens after 5 consecutive failures (configurable)
- Circuit attempts auto-recovery after 60 seconds (configurable)
- Manual recovery possible by restarting application (resets all circuits)

## Files Changed

| File | Lines | Description |
|------|-------|-------------|
| requirements.txt | +3 | Added pybreaker>=1.4.1 dependency |
| app/config.py | +6 | Added circuit breaker configuration settings |
| app/services/monitoring/circuit_breakers.py | +307 | Circuit breaker implementation with email listener |
| app/services/monitoring/__init__.py | +16 | Exported circuit breaker functions and classes |

**Total:** 332 lines added across 4 files

## Commits

| Hash | Message | Files |
|------|---------|-------|
| 046b4d0 | chore(09-02): add pybreaker dependency and circuit breaker config settings | requirements.txt, app/config.py |
| 2f7468a | feat(09-02): create circuit breakers with email notification listener | app/services/monitoring/circuit_breakers.py, app/services/monitoring/__init__.py |

## Success Criteria

- ✓ pybreaker>=1.4.1 added to requirements.txt
- ✓ Circuit breaker settings in app/config.py with USER DECISION defaults (5 failures, 60s timeout)
- ✓ CircuitBreakerEmailListener sends alert email when circuit opens (SMTP configuration required)
- ✓ Three breakers created: claude_api, mongodb, google_cloud_storage
- ✓ with_circuit_breaker decorator available for service wrapping
- ✓ Lazy initialization prevents import-time side effects

All success criteria met.
