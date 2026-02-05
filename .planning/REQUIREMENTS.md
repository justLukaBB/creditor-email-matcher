# Requirements — Creditor Email Matcher v2

**Milestone:** v2.0 — Production-Ready Multi-Agent Email Analysis
**Created:** 2026-02-04
**Status:** Active

---

## Requirement Categories

### REQ-INFRA: Infrastructure & Foundation

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-INFRA-01 | PostgreSQL as single source of truth with saga pattern for dual-database writes | MUST | 1 |
| REQ-INFRA-02 | Idempotency keys on all database write operations to prevent duplicates | MUST | 1 |
| REQ-INFRA-03 | Hourly reconciliation job comparing PostgreSQL and MongoDB for consistency | MUST | 1 |
| REQ-INFRA-04 | Dramatiq + Redis job queue for async email processing | MUST | 2 |
| REQ-INFRA-05 | Job state machine in PostgreSQL (RECEIVED → QUEUED → PROCESSING → EXTRACTING → MATCHING → WRITING → COMPLETED) | MUST | 2 |
| REQ-INFRA-06 | Retry logic with exponential backoff for transient failures (Claude API, DB writes) | MUST | 2 |
| REQ-INFRA-07 | Worker memory management: max-tasks-per-child=50, explicit gc.collect() after PDF tasks | MUST | 2 |
| REQ-INFRA-08 | GCS integration for attachment storage with temp file cleanup after processing | MUST | 3 |
| REQ-INFRA-09 | Redis connection pooling with limits at 80% of Upstash allocation | SHOULD | 2 |

### REQ-EXTRACT: Content Extraction

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-EXTRACT-01 | PDF text extraction via PyMuPDF with fallback to Claude Vision for scanned/complex PDFs | MUST | 3 |
| REQ-EXTRACT-02 | Page-by-page PDF processing with token budget (max 10 pages or 100K tokens per job) | MUST | 3 |
| REQ-EXTRACT-03 | DOCX extraction via python-docx | MUST | 3 |
| REQ-EXTRACT-04 | XLSX extraction via openpyxl | SHOULD | 3 |
| REQ-EXTRACT-05 | Image extraction via Claude Vision (JPG, PNG) | MUST | 3 |
| REQ-EXTRACT-06 | Key entity extraction: client_name, creditor_name, debt_amount, reference_numbers | MUST | 3 |
| REQ-EXTRACT-07 | Extended extraction: Forderungsaufschlüsselung (Hauptforderung, Zinsen, Kosten), Bankdaten (IBAN/BIC), Ratenzahlung | SHOULD | 3 |
| REQ-EXTRACT-08 | Per-field confidence scores on all extracted entities | MUST | 3 |
| REQ-EXTRACT-09 | Cost circuit breaker: halt processing if daily Claude API token threshold exceeded | SHOULD | 3 |

### REQ-GERMAN: German Language & Locale

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-GERMAN-01 | Unicode NFKC normalization preprocessing for all text | MUST | 4 |
| REQ-GERMAN-02 | German-specific Claude prompts with German examples for extraction | MUST | 4 |
| REQ-GERMAN-03 | Locale-aware number parsing (de_DE: 1.234,56 EUR) | MUST | 4 |
| REQ-GERMAN-04 | Validation regexes for German names, addresses, postal codes | MUST | 4 |
| REQ-GERMAN-05 | OCR post-processing for common Umlaut errors (ii→ü, ss→ß) | SHOULD | 4 |
| REQ-GERMAN-06 | IBAN/BIC format validation with checksum verification | SHOULD | 4 |

### REQ-PIPELINE: Multi-Agent Pipeline

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-PIPELINE-01 | Agent 1 (Email Processing): Parse email body, classify intent, download attachments, route to extraction strategy | MUST | 5 |
| REQ-PIPELINE-02 | Intent classification: debt_statement, payment_plan, rejection, inquiry, auto_reply, spam | MUST | 5 |
| REQ-PIPELINE-03 | Agent 2 (Content Extraction): Process each source (body + attachments) with per-source structured results | MUST | 5 |
| REQ-PIPELINE-04 | Agent 3 (Consolidation): Merge data from all sources, resolve conflicts, compute final confidence | MUST | 5 |
| REQ-PIPELINE-05 | Validation layer after each agent: schema validation + confidence threshold checks | MUST | 5 |
| REQ-PIPELINE-06 | Agent 2 refuses to process if Agent 1 confidence < 0.7 (routes to manual review) | MUST | 5 |
| REQ-PIPELINE-07 | Checkpoint system: save intermediate results per agent for replay/debugging | SHOULD | 5 |
| REQ-PIPELINE-08 | Conflict detection: flag when new data contradicts existing database records | SHOULD | 5 |

### REQ-MATCH: Matching Engine

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-MATCH-01 | Rebuilt matching engine with fuzzy matching (RapidFuzz) on names and reference numbers | MUST | 6 |
| REQ-MATCH-02 | creditor_inquiries integration: use sent email data to narrow match candidates | MUST | 6 |
| REQ-MATCH-03 | Explainability layer: log match reasoning (matched because name_similarity=0.92, aktenzeichen=exact) | MUST | 6 |
| REQ-MATCH-04 | Configurable thresholds per creditor category without redeployment | SHOULD | 6 |
| REQ-MATCH-05 | Multiple matching strategies: exact, fuzzy, reference-based | MUST | 6 |
| REQ-MATCH-06 | Human-in-the-loop for ambiguous matches (multiple candidates above threshold) | MUST | 6 |

### REQ-CONFIDENCE: Confidence Scoring

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-CONFIDENCE-01 | Separate confidence dimensions: extraction_confidence, match_confidence | MUST | 7 |
| REQ-CONFIDENCE-02 | Overall confidence = min(all_stages) — weakest link principle | MUST | 7 |
| REQ-CONFIDENCE-03 | Confidence-based routing: High (>0.85) auto-update, Medium (0.6-0.85) update+notify, Low (<0.6) manual review | MUST | 7 |
| REQ-CONFIDENCE-04 | Different confidence thresholds for native PDFs vs scanned documents | SHOULD | 7 |

### REQ-PROMPT: Prompt Management

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-PROMPT-01 | PostgreSQL prompt_templates table with version tracking | MUST | 8 |
| REQ-PROMPT-02 | Every extraction logs the prompt version used | MUST | 8 |
| REQ-PROMPT-03 | Jinja2 template engine for variable interpolation in prompts | MUST | 8 |
| REQ-PROMPT-04 | Prompt performance tracking (tokens used, execution time, success rate) | SHOULD | 8 |
| REQ-PROMPT-05 | A/B testing: deploy new prompt to subset of traffic before full rollout | COULD | 8 |

### REQ-OPS: Operations & Monitoring

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-OPS-01 | Structured logging with correlation IDs (request_id propagates webhook → final write) | MUST | 9 |
| REQ-OPS-02 | Metrics: queue depth, processing duration, token usage, confidence distribution, error rates | MUST | 9 |
| REQ-OPS-03 | Circuit breakers for Claude API, MongoDB, GCS (open after N consecutive failures) | MUST | 9 |
| REQ-OPS-04 | Sentry error tracking with context (email_id, job_id, agent) | SHOULD | 9 |
| REQ-OPS-05 | Email notifications on auto-match (existing v1 feature preserved) | MUST | 9 |
| REQ-OPS-06 | Processing reports per email: what was extracted, what's missing, confidence per field | SHOULD | 9 |

### REQ-MIGRATE: Migration & Compatibility

| ID | Requirement | Priority | Phase |
|----|-------------|----------|-------|
| REQ-MIGRATE-01 | MongoDB schema backward compatibility: only add fields, never change existing ones | MUST | All |
| REQ-MIGRATE-02 | Shadow mode: v2 processes same emails as v1 without writes for accuracy validation | MUST | 10 |
| REQ-MIGRATE-03 | Gradual traffic cutover: 10% → 50% → 100% with rollback capability | MUST | 10 |
| REQ-MIGRATE-04 | v1 remains as fallback for 30 days after full cutover | SHOULD | 10 |
| REQ-MIGRATE-05 | Zendesk webhook schema updated to include attachment URLs | MUST | 2 |

---

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| REQ-INFRA-01 | Phase 1 | Complete |
| REQ-INFRA-02 | Phase 1 | Complete |
| REQ-INFRA-03 | Phase 1 | Complete |
| REQ-INFRA-04 | Phase 2 | Complete |
| REQ-INFRA-05 | Phase 2 | Complete |
| REQ-INFRA-06 | Phase 2 | Complete |
| REQ-INFRA-07 | Phase 2 | Complete |
| REQ-INFRA-08 | Phase 3 | Complete |
| REQ-INFRA-09 | Phase 2 | Complete |
| REQ-EXTRACT-01 | Phase 3 | Complete |
| REQ-EXTRACT-02 | Phase 3 | Complete |
| REQ-EXTRACT-03 | Phase 3 | Complete |
| REQ-EXTRACT-04 | Phase 3 | Complete |
| REQ-EXTRACT-05 | Phase 3 | Complete |
| REQ-EXTRACT-06 | Phase 3 | Complete |
| REQ-EXTRACT-07 | Phase 3 | Complete |
| REQ-EXTRACT-08 | Phase 3 | Complete |
| REQ-EXTRACT-09 | Phase 3 | Complete |
| REQ-GERMAN-01 | Phase 4 | Complete |
| REQ-GERMAN-02 | Phase 4 | Complete |
| REQ-GERMAN-03 | Phase 4 | Complete |
| REQ-GERMAN-04 | Phase 4 | Complete |
| REQ-GERMAN-05 | Phase 4 | Complete |
| REQ-GERMAN-06 | Phase 4 | Deferred (out of scope) |
| REQ-PIPELINE-01 | Phase 5 | Complete |
| REQ-PIPELINE-02 | Phase 5 | Complete |
| REQ-PIPELINE-03 | Phase 5 | Complete |
| REQ-PIPELINE-04 | Phase 5 | Complete |
| REQ-PIPELINE-05 | Phase 5 | Complete |
| REQ-PIPELINE-06 | Phase 5 | Complete |
| REQ-PIPELINE-07 | Phase 5 | Complete |
| REQ-PIPELINE-08 | Phase 5 | Complete |
| REQ-MATCH-01 | Phase 6 | Pending |
| REQ-MATCH-02 | Phase 6 | Pending |
| REQ-MATCH-03 | Phase 6 | Pending |
| REQ-MATCH-04 | Phase 6 | Pending |
| REQ-MATCH-05 | Phase 6 | Pending |
| REQ-MATCH-06 | Phase 6 | Pending |
| REQ-CONFIDENCE-01 | Phase 7 | Pending |
| REQ-CONFIDENCE-02 | Phase 7 | Pending |
| REQ-CONFIDENCE-03 | Phase 7 | Pending |
| REQ-CONFIDENCE-04 | Phase 7 | Pending |
| REQ-PROMPT-01 | Phase 8 | Pending |
| REQ-PROMPT-02 | Phase 8 | Pending |
| REQ-PROMPT-03 | Phase 8 | Pending |
| REQ-PROMPT-04 | Phase 8 | Pending |
| REQ-PROMPT-05 | Phase 8 | Pending |
| REQ-OPS-01 | Phase 9 | Pending |
| REQ-OPS-02 | Phase 9 | Pending |
| REQ-OPS-03 | Phase 9 | Pending |
| REQ-OPS-04 | Phase 9 | Pending |
| REQ-OPS-05 | Phase 9 | Pending |
| REQ-OPS-06 | Phase 9 | Pending |
| REQ-MIGRATE-01 | All Phases | Complete |
| REQ-MIGRATE-02 | Phase 10 | Pending |
| REQ-MIGRATE-03 | Phase 10 | Pending |
| REQ-MIGRATE-04 | Phase 10 | Pending |
| REQ-MIGRATE-05 | Phase 2 | Complete |

**Coverage:** 57/57 requirements mapped (100%)

---

## Summary

| Category | MUST | SHOULD | COULD | Total |
|----------|------|--------|-------|-------|
| INFRA | 7 | 2 | 0 | 9 |
| EXTRACT | 6 | 3 | 0 | 9 |
| GERMAN | 4 | 2 | 0 | 6 |
| PIPELINE | 5 | 3 | 0 | 8 |
| MATCH | 4 | 1 | 0 | 5 |
| CONFIDENCE | 3 | 1 | 0 | 4 |
| PROMPT | 3 | 1 | 1 | 5 |
| OPS | 4 | 2 | 0 | 6 |
| MIGRATE | 4 | 1 | 0 | 5 |
| **Total** | **40** | **16** | **1** | **57** |

---

*Generated from research synthesis on 2026-02-04*
*Traceability updated: 2026-02-05 — Phase 5 requirements marked Complete (REQ-PIPELINE-01 through REQ-PIPELINE-08)*
