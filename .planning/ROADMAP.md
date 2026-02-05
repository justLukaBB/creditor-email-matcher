# Roadmap: Creditor Email Matcher v2

## Overview

The v2 upgrade transforms a fragile monolithic webhook handler into a production-grade multi-agent system. The journey begins by fixing dual-database consistency issues that caused the matching engine bypass, establishes async job infrastructure for 200+ daily emails, adds critical multi-format document extraction (PDFs, DOCX, images), applies German-specific processing, builds a validated three-agent pipeline, reconstructs the matching engine with explainability, calibrates confidence scoring, moves prompts to database management, hardens for production, and gradually migrates traffic with shadow mode validation. This is not a simple "add PDF support" upgrade -- it requires distributed systems patterns for reliability at scale.

## Phases

**Phase Numbering:**
- Integer phases (1-10): Planned v2.0 milestone work
- Decimal phases (e.g., 3.1): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Dual-Database Audit & Consistency** - Fix PostgreSQL/MongoDB saga pattern to enable reliable matching
- [x] **Phase 2: Async Job Queue Infrastructure** - Dramatiq + Redis for reliable batch processing
- [x] **Phase 3: Multi-Format Document Extraction** - PyMuPDF + Claude Vision for PDFs, DOCX, XLSX, images
- [x] **Phase 4: German Document Extraction & Validation** - Umlaut handling, locale parsing, German prompts
- [x] **Phase 5: Multi-Agent Pipeline with Validation** - 3-agent architecture with validation layers
- [x] **Phase 6: Matching Engine Reconstruction** - Rebuild bypassed engine with explainability
- [ ] **Phase 7: Confidence Scoring & Calibration** - Separate dimensions, threshold tuning, routing
- [ ] **Phase 8: Database-Backed Prompt Management** - Versioned prompts with performance tracking
- [ ] **Phase 9: Production Hardening & Monitoring** - Structured logging, metrics, circuit breakers
- [ ] **Phase 10: Gradual Migration & Cutover** - Shadow mode validation, traffic routing, rollback capability

## Phase Details

### Phase 1: Dual-Database Audit & Consistency
**Goal**: PostgreSQL becomes single source of truth with saga pattern for dual-database writes, preventing the data inconsistencies that caused matching engine bypass.

**Depends on**: Nothing (first phase -- addresses root cause)

**Requirements**: REQ-INFRA-01, REQ-INFRA-02, REQ-INFRA-03, REQ-MIGRATE-01

**Success Criteria** (what must be TRUE):
  1. PostgreSQL is authoritative source for all email processing state and extracted data
  2. MongoDB writes succeed or are retried via compensating transactions without data loss
  3. Reconciliation job detects and repairs PostgreSQL-MongoDB inconsistencies hourly
  4. Idempotency keys prevent duplicate writes when operations retry
  5. Audit shows existing mismatches quantified with recovery plan

**Plans**: 4 plans

Plans:
- [x] 01-01-PLAN.md -- Database models and Alembic migration for saga infrastructure
- [x] 01-02-PLAN.md -- DualDatabaseWriter saga pattern and webhook refactor
- [x] 01-03-PLAN.md -- Hourly reconciliation service with APScheduler
- [x] 01-04-PLAN.md -- Data consistency audit script and verification

---

### Phase 2: Async Job Queue Infrastructure
**Goal**: Dramatiq + Redis job queue enables reliable async processing of 200+ emails/day with retry logic, replacing synchronous webhook handling that times out on attachments.

**Depends on**: Phase 1 (database consistency must be fixed before adding async complexity)

**Requirements**: REQ-INFRA-04, REQ-INFRA-05, REQ-INFRA-06, REQ-INFRA-07, REQ-INFRA-09, REQ-MIGRATE-05

**Success Criteria** (what must be TRUE):
  1. Email processing jobs survive worker crashes without data loss
  2. Jobs transition through state machine (RECEIVED -> QUEUED -> PROCESSING -> COMPLETED) with PostgreSQL tracking
  3. Transient failures (Claude API rate limits, DB timeouts) retry with exponential backoff
  4. Worker memory remains stable on Render 512MB instances via max-tasks-per-child=50 and gc.collect()
  5. Zendesk webhook schema updated to include attachment URLs for download

**Plans**: 4 plans

Plans:
- [x] 02-01-PLAN.md -- Dramatiq broker setup, config, worker entrypoint
- [x] 02-02-PLAN.md -- IncomingEmail state machine columns, webhook schema, migration
- [x] 02-03-PLAN.md -- Email processor actor and webhook refactor to enqueue
- [x] 02-04-PLAN.md -- Job status API, failure notifications, Procfile integration

---

### Phase 3: Multi-Format Document Extraction
**Goal**: System extracts text and structured data from PDFs (PyMuPDF + Claude Vision fallback), DOCX, XLSX, and images with token budgets to prevent cost explosions.

**Depends on**: Phase 2 (async infrastructure required for long-running PDF processing)

**Requirements**: REQ-EXTRACT-01, REQ-EXTRACT-02, REQ-EXTRACT-03, REQ-EXTRACT-04, REQ-EXTRACT-05, REQ-EXTRACT-06, REQ-EXTRACT-07, REQ-EXTRACT-08, REQ-EXTRACT-09, REQ-INFRA-08

**Success Criteria** (what must be TRUE):
  1. PDF text extraction uses PyMuPDF first, falls back to Claude Vision for scanned/complex documents
  2. Page-by-page PDF processing respects token budget (max 10 pages or 100K tokens per job)
  3. DOCX and XLSX documents extract text and tables reliably
  4. Images (JPG, PNG) extract via Claude Vision with per-field confidence scores
  5. Key entities extracted: client_name, creditor_name, debt_amount, reference_numbers with confidence
  6. Extended extraction captures: Forderungsaufschluesselung, Bankdaten, Ratenzahlung when present
  7. Cost circuit breaker halts processing if daily token threshold exceeded
  8. Attachments stored in GCS with temp file cleanup after processing

**Plans**: 6 plans

Plans:
- [x] 03-01-PLAN.md -- Extraction result models and cost control infrastructure
- [x] 03-02-PLAN.md -- GCS attachment handler and format detection
- [x] 03-03-PLAN.md -- PDF extractor with PyMuPDF and Claude Vision fallback
- [x] 03-04-PLAN.md -- Email body, DOCX, and XLSX extractors
- [x] 03-05-PLAN.md -- Image extractor and extraction consolidator
- [x] 03-06-PLAN.md -- Content extraction Dramatiq actor and integration

---

### Phase 4: German Document Extraction & Validation
**Goal**: German-specific text processing handles Umlauts, locale formats (1.234,56 EUR), and legal terminology without mismatches or parsing errors.

**Depends on**: Phase 3 (generic extraction must work before German-specific optimization)

**Requirements**: REQ-GERMAN-01, REQ-GERMAN-02, REQ-GERMAN-03, REQ-GERMAN-04, REQ-GERMAN-05, REQ-GERMAN-06

**Success Criteria** (what must be TRUE):
  1. All text preprocessing uses Unicode NFKC normalization to handle Umlauts correctly
  2. Claude extraction prompts use German examples for German document types
  3. Number parsing respects de_DE locale (1.234,56 EUR interpreted correctly)
  4. Validation regexes catch malformed German names, addresses, postal codes
  5. OCR post-processing corrects common errors (ii->ue, ss->sz)
  6. IBAN/BIC validation includes checksum verification to catch OCR errors

**NOTE:** User marked IBAN/BIC (Success Criterion 6) as out of scope during context gathering.

**Plans**: 4 plans

Plans:
- [x] 04-01-PLAN.md -- German text preprocessor with Unicode normalization and OCR correction
- [x] 04-02-PLAN.md -- German amount parser with babel locale support
- [x] 04-03-PLAN.md -- German Claude prompts for PDF and image extractors
- [x] 04-04-PLAN.md -- Pipeline integration for all text extractors

---

### Phase 5: Multi-Agent Pipeline with Validation
**Goal**: Three-agent architecture (Email Processing -> Content Extraction -> Consolidation) with validation layers prevents error propagation and enables independent agent scaling.

**Depends on**: Phase 3 (Content Extraction Agent requires extraction capabilities), Phase 4 (German handling needed for validation)

**Requirements**: REQ-PIPELINE-01, REQ-PIPELINE-02, REQ-PIPELINE-03, REQ-PIPELINE-04, REQ-PIPELINE-05, REQ-PIPELINE-06, REQ-PIPELINE-07, REQ-PIPELINE-08

**Success Criteria** (what must be TRUE):
  1. Agent 1 (Email Processing) classifies intent (debt_statement, payment_plan, rejection, inquiry, auto_reply, spam) and routes to extraction strategy
  2. Agent 2 (Content Extraction) processes each source (body + attachments) with per-source structured results
  3. Agent 3 (Consolidation) merges data from all sources, resolves conflicts, computes final confidence
  4. Validation layer after each agent enforces schema validation and confidence thresholds
  5. Agent 2 refuses to process when Agent 1 confidence < 0.7, routing to manual review
  6. Checkpoint system saves intermediate results per agent for replay and debugging
  7. Conflict detection flags when extracted data contradicts existing database records

**Plans**: 5 plans

Plans:
- [x] 05-01-PLAN.md -- Foundation: checkpoint storage, intent models, validation services
- [x] 05-02-PLAN.md -- Agent 1: Intent classifier with rule-based + LLM fallback
- [x] 05-03-PLAN.md -- Agent 3: Consolidation agent with conflict detection
- [x] 05-04-PLAN.md -- Manual review queue model and REST API
- [x] 05-05-PLAN.md -- Pipeline integration: orchestrate 3 agents with validation

---

### Phase 6: Matching Engine Reconstruction
**Goal**: Rebuilt matching engine with fuzzy matching, creditor_inquiries integration, and explainability replaces the bypassed v1 code, enabling reliable client/creditor assignment.

**Depends on**: Phase 5 (Consolidation Agent provides validated data to matching engine)

**Requirements**: REQ-MATCH-01, REQ-MATCH-02, REQ-MATCH-03, REQ-MATCH-04, REQ-MATCH-05, REQ-MATCH-06

**Success Criteria** (what must be TRUE):
  1. Matching engine uses fuzzy matching (RapidFuzz) on names and reference numbers with configurable thresholds
  2. creditor_inquiries table integration narrows match candidates to recent sent emails
  3. Explainability layer logs match reasoning (e.g., matched because name_similarity=0.92, aktenzeichen=exact)
  4. Thresholds configurable per creditor category without redeployment
  5. Multiple strategies available: exact match, fuzzy match, reference-based matching
  6. Ambiguous matches (multiple candidates above threshold) route to human review

**Plans**: 5 plans

Plans:
- [x] 06-01-PLAN.md -- Database models (MatchingThreshold, CreditorInquiry, MatchResult) and migration
- [x] 06-02-PLAN.md -- Signal scorers (RapidFuzz) and ExplainabilityBuilder
- [x] 06-03-PLAN.md -- ThresholdManager and matching strategies (exact, fuzzy, combined)
- [x] 06-04-PLAN.md -- MatchingEngineV2 core with creditor_inquiries integration
- [x] 06-05-PLAN.md -- Pipeline integration and ambiguous match routing

---

### Phase 7: Confidence Scoring & Calibration
**Goal**: Calibrated confidence scores across dimensions (extraction, matching) enable reliable automation decisions and human review routing.

**Depends on**: Phase 6 (match confidence is one dimension of overall scoring)

**Requirements**: REQ-CONFIDENCE-01, REQ-CONFIDENCE-02, REQ-CONFIDENCE-03, REQ-CONFIDENCE-04

**Success Criteria** (what must be TRUE):
  1. Confidence separated into dimensions: extraction_confidence and match_confidence
  2. Overall confidence calculated as min(all_stages) using weakest-link principle
  3. Confidence-based routing works: High (>0.85) auto-updates, Medium (0.6-0.85) updates+notifies, Low (<0.6) manual review
  4. Different thresholds apply for native PDFs vs scanned documents based on calibration data
  5. Calibration dataset (500+ labeled examples) validates threshold settings

**Plans**: 4 plans

Plans:
- [ ] 07-01-PLAN.md -- Confidence dimensions service and CalibrationSample model
- [ ] 07-02-PLAN.md -- Overall confidence calculator and routing service
- [ ] 07-03-PLAN.md -- Calibration collector and manual review integration
- [ ] 07-04-PLAN.md -- Pipeline integration with confidence-based routing

---

### Phase 8: Database-Backed Prompt Management
**Goal**: Prompts stored in PostgreSQL with version tracking enable A/B testing, instant rollback, and audit trails without redeployment.

**Depends on**: Phase 7 (prompt performance tracking requires confidence metrics)

**Requirements**: REQ-PROMPT-01, REQ-PROMPT-02, REQ-PROMPT-03, REQ-PROMPT-04, REQ-PROMPT-05

**Success Criteria** (what must be TRUE):
  1. PostgreSQL prompt_templates table stores prompts with version tracking
  2. Every extraction logs the prompt version used for audit trail
  3. Jinja2 template engine enables variable interpolation in prompts
  4. Prompt performance tracked: tokens used, execution time, success rate per version
  5. A/B testing deploys new prompts to subset of traffic before full rollout (COULD have)

**Plans**: TBD

Plans:
- [ ] 08-01: TBD during planning

---

### Phase 9: Production Hardening & Monitoring
**Goal**: Structured logging, metrics, circuit breakers, and integration tests provide operational visibility and resilience for production scale.

**Depends on**: Phase 8 (all core functionality complete before hardening)

**Requirements**: REQ-OPS-01, REQ-OPS-02, REQ-OPS-03, REQ-OPS-04, REQ-OPS-05, REQ-OPS-06

**Success Criteria** (what must be TRUE):
  1. Structured logging with correlation IDs propagates request_id from webhook through final write
  2. Metrics collected: queue depth, processing duration, token usage, confidence distribution, error rates
  3. Circuit breakers for Claude API, MongoDB, GCS open after N consecutive failures
  4. Sentry error tracking includes context (email_id, job_id, agent)
  5. Email notifications sent on auto-match (preserves v1 feature)
  6. Processing reports show per-email: what extracted, what's missing, confidence per field
  7. Integration tests cover end-to-end pipeline with fixtures

**Plans**: TBD

Plans:
- [ ] 09-01: TBD during planning

---

### Phase 10: Gradual Migration & Cutover
**Goal**: Shadow mode validates v2 accuracy against v1, gradual traffic cutover (10% -> 50% -> 100%) reduces risk, v1 remains fallback for 30 days.

**Depends on**: Phase 9 (production readiness verified before traffic migration)

**Requirements**: REQ-MIGRATE-02, REQ-MIGRATE-03, REQ-MIGRATE-04

**Success Criteria** (what must be TRUE):
  1. v2 processes same emails as v1 in shadow mode without writes, generating comparison report
  2. Shadow mode validates v2 extraction accuracy matches or exceeds v1 before cutover
  3. Traffic routing configuration supports gradual rollout (10% to v2, 90% to v1)
  4. Monitoring tracks error rates and extraction quality during ramp
  5. v1 remains deployed as fallback for 30 days after full cutover
  6. Rollback capability tested and documented

**Plans**: TBD

Plans:
- [ ] 10-01: TBD during planning

---

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10

| Phase | Status | Completed |
|-------|--------|-----------|
| 1. Dual-Database Audit & Consistency | Complete | 2026-02-04 |
| 2. Async Job Queue Infrastructure | Complete | 2026-02-04 |
| 3. Multi-Format Document Extraction | Complete | 2026-02-05 |
| 4. German Document Extraction & Validation | Complete | 2026-02-05 |
| 5. Multi-Agent Pipeline with Validation | Complete | 2026-02-05 |
| 6. Matching Engine Reconstruction | Complete | 2026-02-05 |
| 7. Confidence Scoring & Calibration | Not started | - |
| 8. Database-Backed Prompt Management | Not started | - |
| 9. Production Hardening & Monitoring | Not started | - |
| 10. Gradual Migration & Cutover | Not started | - |

---

## Dependency Diagram

```
Phase 1: Dual-Database Audit & Consistency
   |
   v
Phase 2: Async Job Queue Infrastructure
   |
   v
Phase 3: Multi-Format Document Extraction ------+
   |                                            |
   v                                            v
Phase 4: German Document Extraction ---------> Phase 5: Multi-Agent Pipeline
                                                  |
                                                  v
                                               Phase 6: Matching Engine Reconstruction
                                                  |
                                                  v
                                               Phase 7: Confidence Scoring & Calibration
                                                  |
                                                  v
                                               Phase 8: Database-Backed Prompt Management
                                                  |
                                                  v
                                               Phase 9: Production Hardening & Monitoring
                                                  |
                                                  v
                                               Phase 10: Gradual Migration & Cutover
```

**Critical Path:** 1 -> 2 -> 3 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 (Phase 4 runs parallel to Phase 5 dependency)

---

## Milestone Success Criteria

**v2.0 is complete when:**
1. All 57 requirements (40 MUST, 16 SHOULD, 1 COULD) are satisfied or deferred with rationale
2. 200+ daily emails process reliably with multi-format attachments (PDF, DOCX, XLSX, images)
3. German documents extract correctly with Umlaut handling and locale-aware parsing
4. Matching engine assigns emails to correct client/creditor pairs with >90% accuracy
5. Confidence-based routing automates high-confidence cases, flags low-confidence for review
6. PostgreSQL and MongoDB remain consistent (hourly reconciliation shows <1% drift)
7. Production monitoring provides visibility into queue depth, processing duration, token costs
8. Shadow mode validation shows v2 matches or exceeds v1 accuracy on 500+ email sample
9. Traffic fully migrated to v2 with <2% error rate sustained for 7 days
10. v1 fallback capability verified and documented

---

*Last updated: 2026-02-05 -- Phase 7 planned (4 plans in 3 waves)*
