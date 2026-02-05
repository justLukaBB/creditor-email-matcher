# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-04)

**Core value:** Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

**Current focus:** Phase 3 - Multi-Format Document Extraction (COMPLETE)

## Current Position

Phase: 3 of 10 (Multi-Format Document Extraction)
Plan: 6 of 6 complete
Status: Phase complete
Last activity: 2026-02-05 — Completed 03-06-PLAN.md (Extraction orchestration)

Progress: [████░░░░░░] 40%

## Performance Metrics

**Velocity:**
- Total plans completed: 14
- Average duration: 3.9 minutes
- Total execution time: 0.9 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 4 | 21 min | 5.25 min |
| 2 | 4 | 13 min | 3.25 min |
| 3 | 6 | 21 min | 3.5 min |

**Recent Trend:**
- 01-04: 5 minutes (audit service with CLI script)
- 02-01: 3 minutes (Dramatiq broker infrastructure setup)
- 02-02: 3 minutes (Job state machine database schema)
- 02-03: 2 minutes (Email processor Dramatiq actor)
- 02-04: 5 minutes (API integration and deployment)
- 03-01: 3 minutes (Extraction result Pydantic models)
- 03-02: 3 minutes (GCS storage client and format detection)
- 03-03: 4 minutes (PDF extractor with PyMuPDF and Claude Vision)
- 03-04: 4 minutes (Email body, DOCX, XLSX extractors)
- 03-05: 3 minutes (Image extractor and consolidator)
- 03-06: 4 minutes (Extraction orchestration and email processor integration)
- Trend: Schema/model updates ~3 min, API/integration work ~5 min, extractor creation ~3-4 min

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Phase 1: PostgreSQL as single source of truth with saga pattern for dual-database writes
- Phase 2: Dramatiq + Redis over Celery for simpler deployment and lower memory footprint
- Phase 3-5: Three-agent architecture (Email Processing → Content Extraction → Consolidation)
- Phase 3: Claude Vision for PDF/image extraction (no separate OCR service)
- Phase 5: Intent-based processing with different extraction strategies per email type
- Phase 6: Matching engine reactivation rather than rebuild from scratch
- Phase 8: Prompt repository in PostgreSQL for runtime updates without deployment

**New from 01-01:**
- Integer primary keys (not UUIDs) to match existing codebase convention
- PostgreSQL-based idempotency storage in Phase 1 (Redis deferred to Phase 2)
- Nullable idempotency_key on IncomingEmail for backward compatibility
- Manual migration over autogenerate (no DB connection available)

**New from 01-02:**
- DualDatabaseWriter does NOT commit - caller controls transaction (atomic outbox + business data)
- MongoDB write happens post-commit (compensatable, PostgreSQL is source of truth)
- Idempotency key format: operation:aggregate_id:hash (SHA256 of JSON payload)
- MongoDB-only fallback mode preserved for backward compatibility
- Import mongodb_service singleton (reuse existing MongoDB client)

**New from 01-03:**
- APScheduler for hourly reconciliation (lightweight, no separate worker process)
- BackgroundScheduler (not AsyncIOScheduler) for synchronous SQLAlchemy/PyMongo
- 48-hour lookback window for reconciliation comparison
- Auto-repair strategy: PostgreSQL to MongoDB re-sync on mismatch
- Manual reconciliation trigger endpoint for operational control

**New from 01-04:**
- Exit code reflects health score (0 for healthy >= 0.95, 1 for issues)
- 30-day default lookback for audit period
- Standalone audit script works without running FastAPI app
- Recovery plan categorizes mismatches: re-sync, manual_review, stalled, no_action
- Health score calculation: (total_checked - total_issues) / total_checked

**New from 02-01:**
- Dramatiq broker auto-switches: RedisBroker (production) or StubBroker (testing) based on redis_url
- Redis namespace: creditor_matcher for key isolation
- Worker configuration: 2 processes x 1 thread for Render 512MB memory budget
- Settings extended with missing fields: environment, webhook_secret, llm_provider, SMTP settings
- Worker entrypoint (app/worker.py) imports actors package for broker setup

**New from 02-02:**
- IncomingEmail tracks job lifecycle: started_at, completed_at timestamps for async processing
- retry_count column separate from sync_retry_count (job retries vs MongoDB sync retries)
- attachment_urls JSON column stores Zendesk attachment metadata for Phase 3 processing
- Composite index (processing_status, received_at) for efficient worker polling
- ZendeskWebhookEmail schema accepts attachments field with URL, filename, content_type, size

**New from 02-03:**
- Email processor Dramatiq actor with max_retries=5 and exponential backoff (15s to 5min)
- should_retry predicate for selective retry (transient vs permanent failures)
- on_process_email_failure callback invokes notify_permanent_failure after all retries exhausted
- State machine: received -> queued -> processing -> completed/failed/not_creditor_reply
- FOR UPDATE SKIP LOCKED row locking prevents duplicate processing
- gc.collect() after each job for 512MB memory constraint

**New from 02-04:**
- Job status REST API with no authentication (relies on Render internal networking)
- Manual retry endpoint resets to "queued" status and increments retry_count
- FailureNotifier uses app.config.settings for SMTP (separate from email_notifier)
- Procfile runs web (uvicorn) + worker (dramatiq) processes for Render deployment
- All routers (webhook, jobs) registered in FastAPI app
- App version bumped to 0.3.0

**New from 03-01:**
- ExtractionResult Pydantic model for structured extraction output
- FileExtractionResult for per-file extraction details
- Confidence levels: LOW, MEDIUM, HIGH enum
- Nested models: GesamtforderungComponents, ExtractedAmount

**New from 03-02:**
- GCSAttachmentHandler context manager auto-cleans temp files in finally block
- Text-to-filesize ratio 0.01 threshold for scanned PDF detection
- FileFormat enum: PDF, DOCX, XLSX, IMAGE_JPG, IMAGE_PNG, UNKNOWN
- MIME type takes priority over file extension in format detection
- Sample first 5 pages for large PDFs (performance optimization)
- Graceful failure defaults to Claude Vision (is_scanned_pdf returns True on error)

**New from 03-03:**
- PDFExtractor with PyMuPDF for digital PDFs (zero API cost)
- Claude Vision fallback for scanned PDFs with structured JSON prompt
- Token budget check before Claude Vision calls (fail fast)
- Page limit: first 5 + last 5 for documents >10 pages
- Lazy Claude client initialization (only when scanned PDF encountered)
- German currency parsing: 1.234,56 EUR -> 1234.56
- extraction_method now includes "skipped" for error handling

**New from 03-04:**
- EmailBodyExtractor with flexible regex patterns for German amount formats
- DOCXExtractor extracts from paragraphs and tables using python-docx
- XLSXExtractor with read_only=True for memory-efficient extraction
- Keyword-adjacent-cell pattern for XLSX amount detection
- All extractors return consistent SourceExtractionResult with tokens_used=0
- Confidence scoring: HIGH for German format (has comma), MEDIUM otherwise

**New from 03-05:**
- ImageExtractor for JPG/PNG using Claude Vision API with MEDIUM confidence
- Large images (>5MB) resized to 1500px max before API call
- Temp files from resize cleaned up in finally block using os.unlink
- ExtractionConsolidator with highest-amount-wins rule (USER DECISION locked)
- Default to 100 EUR when no amount found (USER DECISION locked)
- Weakest-link confidence calculation across all sources
- Amount deduplication within 1 EUR threshold
- Best name selection: HIGH confidence first, then longest name

**New from 03-06:**
- ContentExtractionService orchestrates all extractors (email body + attachments)
- extract_content Dramatiq actor with max_retries=3 and exponential backoff
- Attachment processing priority: PDF > DOCX > XLSX > images (highest info density first)
- Circuit breaker check before extraction (fail fast if daily limit exceeded)
- Email processor integrates Phase 3 extraction after parsing step
- Merged extraction schema: Phase 3 debt_amount + entity extraction is_creditor_reply
- extraction_metadata tracks sources_processed, sources_with_amount, total_tokens_used
- Backward-compatible extracted_data schema maintained for downstream compatibility

### Pending Todos

**Phase 2 Deployment Prerequisites:**
- Install dependencies: `pip install -r requirements.txt` (includes dramatiq[redis]>=2.0.1, psutil>=5.9.0)
- Set DATABASE_URL and MONGODB_URL environment variables
- Add Redis add-on on Render and set REDIS_URL environment variable (see 02-01-SUMMARY.md)
- Set SMTP environment variables for failure notifications: SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, ADMIN_EMAIL (see 02-04-SUMMARY.md)
- Run migration: `alembic upgrade head` (creates outbox_messages, idempotency_keys, reconciliation_reports tables + adds job state columns)
- Consider CREATE INDEX CONCURRENTLY for production if incoming_emails table is large (see 02-02-SUMMARY.md)
- Deploy to Render with Procfile (starts web + worker processes)

**Phase 1 Outstanding:**
- Run baseline audit: `python scripts/audit_consistency.py --lookback-days 30` to establish current consistency state
- Tune reconciliation frequency based on production metrics (currently hourly)

### Blockers/Concerns

**Phase 3 Complete:** All 6 plans executed (extraction models, GCS storage, PDF extractor, email/DOCX/XLSX extractors, image extractor + consolidator, extraction orchestration). Ready for Phase 4 (German Text Processing).

**Production Deployment Required:** Phases 1, 2, and 3 code complete but not deployed. Need to:
1. Deploy to production environment with Procfile
2. Configure REDIS_URL and SMTP environment variables
3. Run migration: `alembic upgrade head`
4. Run baseline audit against production databases
5. Verify webhook endpoint receives emails and enqueues to Dramatiq
6. Verify failure notifications work (test with failed job)

**Production Risk:** Render 512MB memory limits require careful worker configuration (max-tasks-per-child, gc.collect()) to prevent OOM kills during PDF processing.

**Migration Risk:** v1 system bypassed matching engine likely due to database consistency issues. Must validate Phase 1 fixes prevent regression before building v2 pipeline on same foundation.

## Session Continuity

Last session: 2026-02-05
Stopped at: Completed 03-06-PLAN.md execution - Extraction orchestration (Phase 3 complete)
Resume file: None

---

**Next action:** Begin Phase 4 planning (German Text Processing). Create context and research files for reference number extraction and German-specific text normalization.
