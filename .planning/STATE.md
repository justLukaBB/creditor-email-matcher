# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-04)

**Core value:** Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

**Current focus:** Phase 6 - Matching Engine Reconstruction

## Current Position

Phase: 6 of 10 (Matching Engine Reconstruction)
Plan: 5 of 5 complete
Status: Phase complete
Last activity: 2026-02-05 — Completed 06-05-PLAN.md (Pipeline Integration)

Progress: [██████░░░░] 60%

## Performance Metrics

**Velocity:**
- Total plans completed: 26
- Average duration: 3.2 minutes
- Total execution time: 1.6 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 4 | 21 min | 5.25 min |
| 2 | 4 | 13 min | 3.25 min |
| 3 | 6 | 21 min | 3.5 min |
| 4 | 4 | 13.5 min | 3.4 min |
| 5 | 5 | 17.7 min | 3.5 min |
| 6 | 5 | 14.5 min | 2.9 min |

**Recent Trend:**
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
- 04-01: 3.5 minutes (German spell checker with pyspellchecker)
- 04-02: 3.5 minutes (German amount parser with babel)
- 04-03: 1.6 minutes (German Claude Vision prompts)
- 04-04: 4.5 minutes (German extractor integration)
- 05-01: 4.0 minutes (Multi-agent pipeline foundation with JSONB checkpoints)
- 05-02: 3.1 minutes (Agent 1 Intent Classification)
- 05-03: 3.5 minutes (Agent 3 Consolidation with conflict detection)
- 05-04: 4.8 minutes (Manual review queue infrastructure)
- 05-05: 2.3 minutes (Multi-agent pipeline integration)
- 06-02: 2.6 minutes (Signal scorers and explainability with RapidFuzz 3.x)
- 06-03: 2.4 minutes (Threshold management and matching strategies)
- 06-04: 2.8 minutes (MatchingEngineV2 core orchestrator)
- 06-05: 4.1 minutes (Pipeline integration with review queue routing)
- Trend: Schema/model updates ~3 min, API/integration work ~5 min, text processing ~3.5 min, prompt updates ~1.5 min, extractor integration ~4.5 min, validation infrastructure ~4 min, agent implementation ~3.5 min, pipeline integration ~3 min, signal scoring ~2.5 min, matching engine ~2.9 min

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

**New from 04-01:**
- GermanTextPreprocessor with Unicode NFKC normalization for consistent Umlaut representation
- Dictionary-validated OCR correction: restores Umlauts from digraphs (ue->ü, oe->ö, ae->ä) only when valid German word
- Conservative approach: better to miss correction than introduce errors
- Digit substitutions (3->e, 0->o, 1->l) only for name/address fields via separate correct_name_field() method
- GermanValidator for postal codes (5 digits), names (Umlauts, prefixes), addresses (street + number)
- pyspellchecker>=0.8.4 dependency added for German dictionary validation

**New from 04-02:**
- parse_german_amount() with babel locale-aware parsing (de_DE first, en_US fallback)
- Format detection using decimal separator patterns (,XX = German, .XX = US)
- Handles ambiguous formats by analyzing rightmost separator position
- extract_amount_from_text() regex helper for finding amounts in German text
- Currency symbol stripping (EUR, Euro, €) before parsing

**New from 04-03:**
- German Claude Vision prompts with German examples (USER DECISION)
- German synonym support: Schulden, offener Betrag, Restschuld, Forderungshoehe, Gesamtsumme
- Realistic German creditor response patterns in prompts
- ASCII-safe characters (ae, oe, ue) in prompts to avoid encoding issues
- EXTRACTION_PROMPT (PDF) and IMAGE_EXTRACTION_PROMPT (images) both German

**New from 04-04:**
- EmailBodyExtractor uses GermanTextPreprocessor.preprocess() before extraction
- EmailBodyExtractor uses parse_german_amount() instead of manual string replacement
- EmailBodyExtractor uses correct_name_field() for OCR correction on names
- EmailBodyExtractor validates names via GermanValidator.validate_name() (REQ-GERMAN-04)
- DOCXExtractor and XLSXExtractor apply same preprocessing and validation
- Names failing validation included with LOW confidence instead of rejection (permissive extraction)
- All German modules exported from extraction package __init__.py for clean imports

**New from 05-01:**
- JSONB agent_checkpoints column stores multi-agent pipeline intermediate results
- Three agent namespaces: agent_1_intent, agent_2_extraction, agent_3_consolidation
- EmailIntent enum with 6 types: debt_statement, payment_plan, rejection, inquiry, auto_reply, spam
- Partial validation preserves valid fields, nulls failed fields, sets needs_review flag
- 0.7 confidence threshold for needs_review flag (USER DECISION: fail-open, don't block pipeline)
- Auto-add timestamp and validation_status to all checkpoints
- flag_modified() for SQLAlchemy JSONB change detection
- Skip-on-retry pattern: has_valid_checkpoint enables idempotent agent execution

**New from 05-02:**
- Agent 1 Intent Classification with rule-based fast path (header checks, noreply addresses, subject regex)
- Claude Haiku fallback for complex intent classification
- Skip_extraction flag for auto_reply and spam intents
- Intent classification checkpoint saved before extraction

**New from 05-03:**
- Agent 3 Consolidation with database conflict detection
- 10% amount difference threshold for conflict detection (USER DECISION)
- Majority voting resolver with confidence based on voting strength
- MongoDB lookup by ticket ID first, then client name fallback
- needs_review flag for conflicts OR confidence < 0.7
- Case-insensitive name conflict comparison
- Conflict detection as flagging mechanism, not blocking

**New from 05-04:**
- ManualReviewQueue model with claim tracking and resolution workflow
- FOR UPDATE SKIP LOCKED for claim concurrency control
- Priority mapping by review reason (manual_escalation=1, validation_failed=2, conflict=3, low_confidence=5)
- Duplicate detection in enqueue_for_review (skip if unresolved item exists for same email_id)
- Partial indexes on resolved_at for efficient pending/claimed queries
- REST API with 6 endpoints: list, stats, claim, claim-next, resolve, email-detail
- Review queue service helpers: enqueue_for_review, bulk_enqueue_for_review, enqueue_low_confidence_items

**New from 05-05:**
- Agent 2 (content_extractor) accepts intent_result parameter from Agent 1
- Skip-on-retry pattern: Agent 2 checks for existing agent_2_extraction checkpoint before processing
- Skip-extraction routing: auto_reply and spam intents skip extraction and complete as not_creditor_reply
- Confidence threshold enforcement: Agent 1 confidence < 0.7 sets needs_review flag
- Email_processor refactored to orchestrate 3-agent pipeline (Agent 1 -> Agent 2 -> Agent 3)
- Automatic manual review queue enrollment when needs_review=True (conflict_detected or low_confidence reason)
- extracted_data includes pipeline_metadata with intent, conflicts, validation_status
- Complete multi-agent pipeline with checkpoints saved at each stage

**New from 06-02:**
- Signal scorers use RapidFuzz 3.x with explicit processor=utils.default_process parameter
- score_client_name tries multiple algorithms (token_sort, partial, token_set) and returns best score
- score_reference_numbers handles OCR errors with fuzzy matching (partial_ratio, token_sort_ratio)
- Reference matching uses score_cutoff=80 for OCR error tolerance (handles 1->I, 0->O substitutions)
- ExplainabilityBuilder v2.0 produces JSONB-ready match explanations
- Signal scorers return (score, details) tuple for explainability
- Explainability includes signal scores, weighted scores, algorithm used, gap detection, filters applied

**New from 06-03:**
- ThresholdManager queries MatchingThreshold table with category → default → hardcoded fallback
- Hardcoded fallback defaults: 0.70 min_match, 0.15 gap_threshold, 40% name / 60% reference weights
- ExactMatchStrategy returns 1.0 only if both name AND reference match exactly (case-insensitive)
- FuzzyMatchStrategy uses signal scorers with weighted average, zeros score if either signal is 0
- CombinedStrategy tries exact first (performance), falls back to fuzzy (robustness)
- All strategies enforce "both signals required" rule from CONTEXT.MD
- StrategyResult dataclass: score, component_scores, signal_details, strategy_used

**New from 06-04:**
- MatchingEngineV2 filters candidates by 30-day creditor_inquiries window (key optimization)
- Gap threshold determines auto_matched vs ambiguous status (gap = top_score - second_score)
- No auto-match without recent creditor_inquiries record (no_recent_inquiry status)
- All match results include explainability JSONB regardless of status
- save_match_results uses db.flush() not commit (caller controls transaction)
- MatchingResult statuses: auto_matched, ambiguous, below_threshold, no_candidates, no_recent_inquiry
- Ambiguous matches route to manual review with top 3 candidates

**New from 06-05:**
- Email processor uses MatchingEngineV2 after multi-agent pipeline (Agent 1-3 → MatchingEngineV2)
- enqueue_ambiguous_match service for matching-specific review enqueueing
- Auto-matched: set matched_inquiry_id, proceed to DualDatabaseWriter, send notification
- Ambiguous/below_threshold/no_recent_inquiry: enqueue to ManualReviewQueue
- Match confidence stored as percentage (0-100) from matching score
- Top-3 candidates with signal breakdown in review_details JSONB
- Priority mapping: ambiguous_match=3, no_recent_inquiry=4, below_threshold=5
- Application-level matching config: match_lookback_days=30, match_threshold_high=0.85, match_threshold_medium=0.70

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

**Phase 6 Complete:** All 5 plans executed successfully.
- 06-01 Complete: Database models (MatchingThreshold, CreditorInquiry, MatchResult)
- 06-02 Complete: Signal scorers (name, reference) with RapidFuzz 3.x and ExplainabilityBuilder
- 06-03 Complete: ThresholdManager and matching strategies (exact, fuzzy, combined)
- 06-04 Complete: MatchingEngineV2 core orchestrator with creditor_inquiries filtering and gap threshold
- 06-05 Complete: Pipeline integration with review queue routing
- Next: Phase 7 (Manual Review UI) or Phase 10 (Production Deployment)

**Phase 6 Production Blockers:**
- No migration for new models (MatchingThreshold, CreditorInquiry, MatchResult)
- creditor_inquiries table requires historical data population (backfill or manual entry)
- Manual review UI needed for reviewers to process ambiguous matches (Phase 7)

**Production Deployment Required:** Phases 1-6 code complete but not deployed. Need to:
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
Stopped at: Completed 06-05-PLAN.md (Pipeline Integration) - Phase 6 complete
Resume file: None

---

**Next action:** Phase 6 complete. Ready for Phase 7 (Manual Review UI) or Phase 10 (Production Deployment).
