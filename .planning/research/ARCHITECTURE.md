# Architecture Patterns for Creditor Email Analysis v2

**Domain:** Multi-Agent Email/Document Processing Pipeline
**Researched:** 2026-02-04
**Confidence:** MEDIUM-HIGH (established architecture patterns, specific integration details need verification)

---

## Executive Summary

The v2 architecture replaces the monolithic webhook handler with a layered pipeline of specialized agents coordinated via a job queue. The key architectural shift: from "webhook receives email → directly writes MongoDB" to "webhook receives email → enqueues job → agents process sequentially → validated data written to databases."

---

## Architecture Overview

### Current State (v1)

```
Zendesk Webhook → FastAPI Endpoint → Claude Extraction → Direct MongoDB Write
                                                          (matching bypassed)
```

**Problems:**
- Single point of failure (no retries)
- No attachment processing
- Matching engine dead code
- Synchronous processing blocks webhook response
- No audit trail

### Target State (v2)

```
Zendesk Webhook
    ↓
FastAPI Endpoint (validate, deduplicate, store raw email)
    ↓
Job Queue (Dramatiq + Redis)
    ↓
┌─────────────────────────────────────────────────┐
│ Agent 1: Email Processing Agent                  │
│ - Parse email body (HTML → text)                 │
│ - Download attachments from Zendesk/GCS          │
│ - Classify intent (debt_statement, payment_plan,  │
│   rejection, inquiry, auto_reply, spam)          │
│ - Route to appropriate extraction strategy        │
└─────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────┐
│ Agent 2: Content Extraction Agent                │
│ - Extract entities from email body               │
│ - Process each attachment by type:               │
│   - PDF: PyMuPDF text → fallback Claude Vision   │
│   - DOCX: python-docx                            │
│   - XLSX: openpyxl                               │
│   - Images: Claude Vision                        │
│ - Per-source structured extraction               │
│ - Per-field confidence scores                    │
└─────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────┐
│ Agent 3: Consolidation Agent                     │
│ - Merge data from email body + all attachments   │
│ - Conflict resolution (body says X, PDF says Y)  │
│ - Final confidence scoring                       │
│ - Match to client/creditor via matching engine    │
│ - Route: auto-update / review / manual           │
└─────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────┐
│ Database Writer                                  │
│ - PostgreSQL: processing results, audit trail    │
│ - MongoDB: final_creditor_list updates           │
│ - Atomic writes with rollback on failure         │
└─────────────────────────────────────────────────┘
```

---

## Component Architecture

### 1. API Layer (FastAPI)

**Responsibility:** HTTP interface, webhook handling, basic validation

```
app/
├── main.py                    # FastAPI app, lifespan events
├── config.py                  # Pydantic Settings (all config centralized)
├── routers/
│   ├── webhook.py             # Zendesk webhook endpoint
│   ├── health.py              # Health check, readiness probe
│   └── admin.py               # Manual retry, status queries
├── schemas/
│   ├── webhook.py             # Zendesk webhook payload schema
│   ├── extraction.py          # Extraction result schemas
│   └── matching.py            # Match result schemas
└── dependencies.py            # Shared dependencies (DB sessions, etc.)
```

**Key decisions:**
- Webhook endpoint does minimal work: validate signature, deduplicate, store raw email, enqueue job
- No business logic in API layer
- Returns 200 immediately after enqueue (Zendesk retries on non-200)

### 2. Agent Layer (Business Logic)

**Responsibility:** All email analysis, extraction, matching logic

```
app/
├── agents/
│   ├── base.py                # BaseAgent with common patterns
│   ├── email_processor.py     # Agent 1: intent classification, routing
│   ├── content_extractor.py   # Agent 2: entity extraction per source
│   └── consolidator.py        # Agent 3: merge, match, route
├── extractors/
│   ├── base.py                # BaseExtractor interface
│   ├── email_body.py          # HTML → text → structured data
│   ├── pdf_extractor.py       # PyMuPDF + Claude Vision fallback
│   ├── docx_extractor.py      # python-docx extraction
│   ├── xlsx_extractor.py      # openpyxl extraction
│   └── image_extractor.py     # Claude Vision for images
├── matching/
│   ├── engine.py              # Refactored matching engine
│   ├── strategies.py          # Fuzzy, exact, reference-based strategies
│   └── scorer.py              # Match scoring and explanation
└── prompts/
    ├── manager.py             # Load prompts from DB, cache, version
    └── templates.py            # Jinja2 template rendering
```

**Key patterns:**
- Each agent is a Dramatiq actor (decorated function)
- Agents communicate via job results in PostgreSQL, not direct calls
- Each agent validates its input before processing
- Each agent logs structured output with confidence scores

### 3. Infrastructure Layer

**Responsibility:** Database access, external APIs, file storage

```
app/
├── db/
│   ├── postgres.py            # SQLAlchemy session management
│   ├── mongodb.py             # PyMongo client management
│   └── redis.py               # Redis connection pool
├── models/
│   ├── incoming_email.py      # PostgreSQL: raw email storage
│   ├── processing_job.py      # PostgreSQL: job state machine
│   ├── extraction_result.py   # PostgreSQL: per-source extractions
│   ├── match_result.py        # PostgreSQL: match audit trail
│   ├── prompt_template.py     # PostgreSQL: versioned prompts
│   └── creditor_inquiry.py    # PostgreSQL: from mandanten-portal
├── services/
│   ├── claude_client.py       # Anthropic API wrapper with retries
│   ├── zendesk_client.py      # Zendesk API for attachments
│   ├── gcs_client.py          # Google Cloud Storage
│   ├── email_notifier.py      # SMTP notifications
│   └── attachment_downloader.py # Download + classify attachments
└── tasks/
    ├── worker.py              # Dramatiq worker configuration
    ├── email_tasks.py         # Task definitions (enqueue points)
    └── maintenance_tasks.py   # Cleanup, reconciliation, reports
```

### 4. Cross-Cutting Concerns

```
app/
├── middleware/
│   ├── request_id.py          # Correlation ID propagation
│   └── error_handler.py       # Global exception handling
├── monitoring/
│   ├── metrics.py             # Prometheus metrics
│   ├── logging.py             # Structured logging (structlog)
│   └── health.py              # Health check logic
└── utils/
    ├── german_text.py         # Umlaut handling, locale parsing
    ├── validators.py          # IBAN, amount, reference validation
    └── retry.py               # Exponential backoff utilities
```

---

## Data Flow Architecture

### Processing Job State Machine

```
RECEIVED → QUEUED → PROCESSING → EXTRACTING → MATCHING → WRITING → COMPLETED
                                                              ↓
                                                         REVIEW_NEEDED
                                                              ↓
                                                         MANUAL_REVIEW
    Any state → FAILED (with error details and retry count)
```

**PostgreSQL `processing_jobs` table:**
- `id`: UUID
- `email_id`: FK to incoming_emails
- `status`: Enum (state machine above)
- `current_agent`: Which agent is processing
- `agent1_result`: JSONB (email processing output)
- `agent2_result`: JSONB (extraction output)
- `agent3_result`: JSONB (consolidation output)
- `confidence_scores`: JSONB (per-field)
- `matched_client_id`: UUID (if matched)
- `matched_creditor_name`: String
- `error_details`: JSONB
- `retry_count`: Integer
- `created_at`, `updated_at`, `completed_at`: Timestamps

### Dual-Database Write Strategy

**PostgreSQL is the source of truth.** MongoDB is updated as a secondary write for mandanten-portal compatibility.

```
1. Write extraction results to PostgreSQL (atomic transaction)
2. If PostgreSQL succeeds → Write to MongoDB
3. If MongoDB fails → Log error, mark for reconciliation, DO NOT rollback PostgreSQL
4. Reconciliation job runs hourly: find PostgreSQL records without MongoDB counterpart
```

**Why PostgreSQL first:**
- PostgreSQL supports transactions (ACID)
- All processing state lives in PostgreSQL
- MongoDB updates are eventual consistency for mandanten-portal
- If MongoDB is down, system continues processing (PostgreSQL has all data)

---

## Integration Patterns

### Zendesk Webhook Integration

```
POST /webhook/zendesk
Headers:
  X-Zendesk-Webhook-Signature: <HMAC>
Body:
  {
    "ticket_id": "...",
    "from_email": "creditor@bank.de",
    "subject": "Re: Aktenzeichen 12345",
    "body_html": "<html>...",
    "body_text": "...",
    "attachments": [
      {"filename": "forderung.pdf", "content_url": "https://...", "size": 1234567}
    ]
  }
```

**Note:** Current v1 webhook schema lacks attachment fields. v2 must update schema to include Zendesk attachment URLs.

### MongoDB Integration (Shared with Mandanten-Portal)

The mandanten-portal Node.js service writes to the same MongoDB. Our writes must be compatible:

```javascript
// MongoDB clients collection structure (mandanten-portal owns this)
{
  _id: ObjectId,
  name: "Max Mustermann",
  aktenzeichen: "2024-001",
  final_creditor_list: [
    {
      creditor_name: "Bank AG",
      original_amount: 5000.00,
      current_amount: 4500.00,
      // ... v2 adds these fields:
      last_extraction_date: ISODate,
      extraction_confidence: 0.92,
      extraction_source: "email_attachment_pdf"
    }
  ]
}
```

**Compatibility constraint:** Never change existing field names/types. Only add new fields. The Node.js portal reads `final_creditor_list` — new fields are ignored by it.

### creditor_inquiries Integration

```sql
-- PostgreSQL creditor_inquiries (written by mandanten-portal Node.js)
-- Read by matching engine to find which client/creditor combinations exist
SELECT ci.client_name, ci.creditor_name, ci.aktenzeichen, ci.sent_at
FROM creditor_inquiries ci
WHERE ci.sent_at > NOW() - INTERVAL '90 days'
```

The matching engine uses `creditor_inquiries` to narrow down possible matches: if we know an email was sent to Creditor X about Client Y, then a reply from Creditor X likely relates to Client Y.

---

## Deployment Architecture (Render)

### Services

| Service | Render Type | Instances | Purpose |
|---------|-------------|-----------|---------|
| **web** | Web Service | 1 | FastAPI app, webhook endpoint |
| **worker** | Background Worker | 1-2 | Dramatiq workers processing jobs |

### External Services

| Service | Provider | Purpose |
|---------|----------|---------|
| PostgreSQL | Render managed | Processing state, audit trail |
| Redis | Upstash (external) | Job queue broker |
| MongoDB | MongoDB Atlas | Shared client data |
| GCS | Google Cloud | Attachment storage |
| Claude API | Anthropic | LLM extraction |

### Render-Specific Considerations

1. **Worker process:** Render Background Workers are ideal for Dramatiq. Use `dramatiq app.tasks.worker` as start command
2. **Memory limits:** Standard tier = 512MB. Must set `--processes 1 --threads 4` to stay within limits
3. **No persistent disk:** All file processing must use temp files, cleaned up after each job
4. **Deploy hooks:** Use Render deploy hooks for Alembic migrations
5. **Health checks:** Web service needs `/health` endpoint for Render health checks

---

## Security Architecture

### Authentication & Authorization

| Endpoint | Auth Method |
|----------|-------------|
| `/webhook/zendesk` | HMAC signature verification |
| `/admin/*` | API key in header (internal only) |
| `/health` | No auth (public) |

### Data Protection

- PII (client names, amounts) encrypted at rest in PostgreSQL
- Attachment URLs from Zendesk are temporary (expire after 24h)
- GCS objects are not publicly accessible (signed URLs only)
- Claude API calls don't log PII in Anthropic's systems (verify Anthropic data policy)
- Structured logs redact PII fields (client names, amounts)

---

## Scalability Design

### Current Scale (200 emails/day)

- 1 web dyno + 1 worker dyno handles this comfortably
- ~8 emails/hour average, peak ~30/hour
- Each email takes 30-120 seconds to fully process
- Worker can handle 4 concurrent jobs (thread-based)

### Growth Path

| Scale | Architecture Change |
|-------|-------------------|
| 500/day | Add second worker dyno |
| 1000/day | Separate extraction workers from matching workers |
| 5000/day | Horizontal scaling, dedicated Redis, connection pooling |

### Backpressure Handling

When queue depth exceeds threshold:
1. Worker continues processing at steady rate
2. New emails still accepted (webhook always returns 200)
3. Alert sent if queue depth > 100
4. No auto-scaling (manual intervention preferred at this scale)

---

## Error Handling Strategy

### Retry Policy

| Error Type | Retries | Backoff | Action After Max Retries |
|------------|---------|---------|--------------------------|
| Claude API 429 | 5 | Exponential (30s, 60s, 120s, 240s, 480s) | Mark failed, alert |
| Claude API 500 | 3 | Exponential (10s, 30s, 90s) | Mark failed, alert |
| MongoDB write failure | 3 | Linear (5s) | Continue (PostgreSQL is source of truth) |
| PostgreSQL write failure | 3 | Linear (5s) | Mark failed, DO NOT continue |
| Attachment download failure | 2 | Linear (10s) | Process without attachment, flag |
| Worker OOM | 0 | N/A | Task auto-requeued by Dramatiq |

### Circuit Breaker

```
Claude API: After 5 consecutive failures → open circuit for 5 minutes
MongoDB: After 3 consecutive failures → open circuit for 2 minutes
GCS: After 3 consecutive failures → open circuit for 5 minutes
```

---

## Monitoring Architecture

### Key Metrics (Prometheus)

| Metric | Type | Alert Threshold |
|--------|------|----------------|
| `emails_received_total` | Counter | <10/hour during business hours |
| `emails_processed_total` | Counter | Diverging from received |
| `processing_duration_seconds` | Histogram | p95 > 120s |
| `queue_depth` | Gauge | >100 |
| `claude_api_tokens_total` | Counter | >500K/day |
| `extraction_confidence` | Histogram | Mean <0.7 |
| `match_success_rate` | Gauge | <0.8 |
| `worker_memory_bytes` | Gauge | >400MB |

### Structured Logging

Every log entry includes:
- `request_id`: Correlation ID from webhook to final write
- `email_id`: PostgreSQL incoming_email ID
- `job_id`: Processing job ID
- `agent`: Which agent is logging
- `duration_ms`: Operation duration
- `level`: INFO/WARN/ERROR

---

## Migration Strategy from v1 to v2

### Phase 1: Parallel Operation
- v2 runs alongside v1
- v1 continues handling production traffic
- v2 processes same emails in shadow mode (no writes)
- Compare v1 and v2 outputs for accuracy validation

### Phase 2: Gradual Cutover
- Route 10% of traffic to v2
- Monitor error rates and extraction quality
- Increase to 50%, then 100%

### Phase 3: v1 Deprecation
- v1 code remains as fallback for 30 days
- Remove v1 after validation period

---

## Key Architectural Decisions

| Decision | Options Considered | Chosen | Rationale |
|----------|-------------------|--------|-----------|
| Agent communication | Direct calls / Message queue / Database state | **Database state** | Agents write results to PostgreSQL, next agent reads. Simple, debuggable, resilient to crashes |
| Source of truth | PostgreSQL / MongoDB / Both | **PostgreSQL** | Transactions, schema validation, audit trail. MongoDB is secondary for portal compatibility |
| Attachment storage | PostgreSQL BYTEA / GCS / Local disk | **GCS** | Already in use, scalable, cheap. Store reference in PostgreSQL |
| Worker framework | Celery / Dramatiq / ARQ | **Dramatiq** | Simpler, lower memory, better for Render constraints |
| Prompt storage | Files / PostgreSQL / MongoDB | **PostgreSQL** | Versioning, querying, already primary DB |
| Config management | .env / Pydantic Settings / Consul | **Pydantic Settings** | Already in use, typed, validated |

---

## Sources

- FastAPI best practices (established patterns)
- Dramatiq documentation patterns (https://dramatiq.io/)
- PostgreSQL state machine patterns
- Render deployment constraints (platform documentation)
- Multi-agent LLM pipeline design patterns

**Verification needed:**
- Current Anthropic API capabilities for PDF native processing (may have changed since training cutoff)
- Render Background Worker specifics for Dramatiq
- Upstash Redis compatibility with Dramatiq broker
