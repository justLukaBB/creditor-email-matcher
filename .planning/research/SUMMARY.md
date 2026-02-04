# Project Research Summary

**Project:** Creditor Email Matcher v2
**Domain:** Multi-Agent Email/Document Processing Automation (Legal Tech)
**Researched:** 2026-02-04
**Confidence:** MEDIUM

## Executive Summary

The Creditor Email Matcher v2 upgrade transforms a monolithic webhook handler into a robust multi-agent pipeline for processing 200+ daily creditor emails with multi-format attachments. Research across stack, features, architecture, and pitfalls reveals a clear path: prioritize dual-database consistency fixes, implement async job queuing with Dramatiq, add multi-format document extraction (PyMuPDF + Claude Vision), and rebuild the bypassed matching engine with explainability.

The recommended approach uses Dramatiq over Celery (simpler, Render-optimized), PostgreSQL as source of truth with MongoDB as secondary write for mandanten-portal compatibility, and a three-agent pipeline (Email Processor → Content Extractor → Consolidator) with validation layers to prevent error propagation. Critical infrastructure includes database-backed prompt versioning, Upstash Redis for job queuing, and circuit breakers for Claude API failures.

Key risks center on operational complexity: PDF token explosion can spike costs 10-100x, memory leaks on Render's 512MB instances cause OOM kills, dual-database consistency requires saga patterns, and German text processing (Umlauts, locale parsing) needs specialized handling. Mitigation requires page-by-page PDF processing with token budgets, max-tasks-per-child worker restarts, PostgreSQL-first writes with reconciliation jobs, and German-specific validation regexes. The biggest underestimated risk is treating this as a simple "add PDF support" upgrade when it actually requires distributed systems patterns for production reliability.

## Key Findings

### Recommended Stack

**Core decision: Dramatiq over Celery** for async processing. Dramatiq offers simpler deployment, lower memory footprint (crucial for Render), cleaner API, and built-in retries. Celery only needed if requiring complex scheduling or existing team expertise. Upstash Redis recommended for managed broker (free tier supports 10K commands/day, sufficient for ~200 emails with 40-50 commands per job).

**Document processing hybrid strategy:** PyMuPDF for fast text extraction, fallback to Claude Vision for scanned/complex PDFs. Office documents via python-docx (DOCX) and openpyxl (XLSX). OCR fallback using pytesseract for simple scans (cost optimization), Claude Vision for complex layouts. This layered approach balances speed, cost, and accuracy.

**Prompt management via PostgreSQL:** Database-backed versioning schema tracks prompt versions, performance metrics, and A/B test results. Supports instant rollback, audit trails, and runtime switching without redeployment. Alternative (Git-based files) rejected due to query limitations and deployment friction.

**Core technologies:**
- **Dramatiq + Redis**: Async job queue — simpler than Celery, better Render compatibility, thread-based workers for I/O-bound tasks
- **PyMuPDF (fitz)**: PDF extraction — fastest text extraction, table detection, metadata access
- **Claude Vision API**: Scanned PDF fallback — handles complex layouts, tables, handwriting
- **python-docx + openpyxl**: Office docs — reliable DOCX/XLSX extraction without JVM overhead
- **PostgreSQL**: Source of truth — processing state, audit trail, prompt versioning, ACID transactions
- **Upstash Redis**: Job broker — managed Redis, generous free tier, automatic scaling
- **Jinja2 + Pydantic**: Prompt templates — variable interpolation and validation before Claude API calls

### Expected Features

**Must have (table stakes):**
- **Multi-format attachment processing** (PDF, DOCX, XLSX, images) — currently only email body supported, attachments hold critical data
- **Intent classification** (Forderungsaufstellung, Ratenzahlungsvereinbarung, Ablehnung, Rückfrage) — route documents to correct workflow
- **Key entity extraction** (client name, creditor name, reference numbers, amounts) — without this, manual review required
- **Confidence scoring** (per-field) — enables human review queue for low-confidence extractions
- **Batch processing with async queue** — 200+ emails/day cannot be synchronous
- **Human review queue** — low-confidence items flagged for manual validation (legal domain requires human accountability)

**Should have (competitive differentiators):**
- **Smart matching to existing records** — fuzzy matching on names/references prevents duplicate entries, critical for data quality
- **Multi-document consolidation** — one case may span multiple emails, system should recognize and merge intelligently
- **German legal document understanding** — specialized NER for German terminology (Ratenzahlungsvereinbarung vs Ratenplan)
- **Creditor-specific template recognition** — different creditors format differently, template library improves accuracy
- **Conflict detection** — flag when new data contradicts existing records (previous email: 5000 EUR, new email: 5500 EUR)
- **IBAN/BIC validation** — format checking catches OCR errors in payment details

**Defer (v2+):**
- **Multi-language support** — current volume is German-only, add when English correspondence appears
- **Context-aware extraction** — using email subject/sender to improve PDF extraction adds complexity for marginal gains
- **Advanced ML models** — start with rule-based + simple extraction, upgrade when hitting accuracy ceiling
- **Payment plan table extraction** — complex but deferred until basic pipeline proven

### Architecture Approach

The v2 architecture replaces "webhook → Claude → MongoDB" with a layered agent pipeline coordinated via Dramatiq. Key shift: PostgreSQL becomes source of truth with state machine tracking (RECEIVED → QUEUED → PROCESSING → EXTRACTING → MATCHING → WRITING → COMPLETED), MongoDB updates as secondary write for mandanten-portal compatibility. If MongoDB fails, system continues (PostgreSQL has all data), reconciliation job repairs inconsistencies hourly.

Three-agent pipeline: (1) Email Processing Agent classifies intent and routes to extraction strategy, (2) Content Extraction Agent processes email body + each attachment by type with per-field confidence scores, (3) Consolidation Agent merges data, resolves conflicts, matches to existing records via refactored matching engine. Critical pattern: agents write results to PostgreSQL, next agent reads. Simple, debuggable, resilient to crashes. Validation layers after each agent prevent error propagation (Agent 2 refuses to process if Agent 1 confidence <0.7).

**Major components:**
1. **API Layer (FastAPI)** — Webhook handling, validation, deduplication, job enqueueing. Returns 200 immediately (Zendesk retries on non-200). No business logic in API layer.
2. **Agent Layer (Dramatiq actors)** — All email analysis, extraction, matching logic. Each agent validates input before processing, logs structured output with confidence.
3. **Extractors** — Specialized per-format: PyMuPDF for PDFs, python-docx for DOCX, openpyxl for XLSX, Claude Vision for images. Hybrid strategy: try fast extraction, fallback to Claude Vision.
4. **Matching Engine** — Rebuilt with explainability layer (logs why each match was made), configurable strategies per creditor type, threshold configuration without redeployment.
5. **Infrastructure Layer** — PostgreSQL sessions (SQLAlchemy), MongoDB client (PyMongo), Redis pool, Claude API wrapper with retries and circuit breaker.

### Critical Pitfalls

1. **PDF Token Explosion** — Multi-page PDFs can consume 500K+ tokens, causing rate limits and unpredictable costs. Prevention: page-by-page processing with token budgets, hard limits (max 10 pages or 100K tokens per job), pre-process to estimate page count, cost circuit breakers. Phase impact: Phase 2 blocker.

2. **Dual-Database Consistency Hell** — MongoDB and PostgreSQL drift out of sync due to partial failures, no distributed transaction coordinator. Prevention: Saga pattern with compensating transactions, PostgreSQL as single source of truth, write-ahead logging, idempotency keys, nightly reconciliation job. Phase impact: Phase 1 critical fix.

3. **Memory Leaks on Render** — PDF processing libraries don't release memory, Celery workers hit OOM on 512MB instances. Prevention: max-tasks-per-child=50 (force worker restart), explicit gc.collect() after PDF tasks, stream processing (don't load entire PDF), monitor memory before/after tasks. Phase impact: Phase 3 production blocker.

4. **Multi-Agent Error Cascades** — Agent 1 mistake amplifies through pipeline, final result confidently wrong. Prevention: validation layer after each agent, confidence thresholds (Agent 2 refuses if Agent 1 <0.7), checkpoint system for replay, cross-validation before final write. Phase impact: Phase 5 architectural foundation.

5. **German Text Parsing Fragility** — OCR struggles with Umlauts (Müller → Muller), German number formats (1.234,56 EUR misinterpreted), compound addresses split incorrectly. Prevention: Unicode normalization (NFKC), German-specific prompts with examples, locale-aware parsing (de_DE), validation regexes for German patterns, fuzzy matching with Levenshtein distance. Phase impact: Phase 4 domain-specific blocker.

## Implications for Roadmap

Based on combined research, the critical path is: fix database consistency → async infrastructure → attachment processing → German extraction → multi-agent validation → matching engine → confidence calibration → prompt management → production hardening.

### Phase 1: Dual-Database Audit & Consistency Fixes
**Rationale:** Current v1 system bypasses matching engine and writes directly to MongoDB, likely due to PostgreSQL/MongoDB consistency issues. This must be fixed before adding complexity. Without this, v2 multi-agent pipeline will amplify existing data quality problems.

**Delivers:**
- Saga pattern for dual-database writes (PostgreSQL-first, MongoDB secondary)
- Reconciliation job to detect and repair inconsistencies
- Idempotency keys to prevent duplicate writes
- Audit of existing data mismatches with recovery plan

**Addresses:** Critical Pitfall #2 (Dual-Database Consistency Hell)

**Avoids:** Building v2 on broken foundation, accumulating more inconsistent data

**Research flag:** Skip research-phase (established distributed systems patterns)

---

### Phase 2: Async Job Queue Infrastructure
**Rationale:** Current synchronous processing cannot scale to 200+ emails/day with multi-attachment processing (30-120s per email). Must add Dramatiq + Redis before attachment support to avoid webhook timeouts.

**Delivers:**
- Dramatiq worker setup on Render with proper resource limits
- Redis connection pooling with Upstash integration
- Job state machine in PostgreSQL (RECEIVED → QUEUED → PROCESSING → COMPLETED)
- Retry logic with exponential backoff for transient failures
- Basic monitoring (queue depth, processing duration, error rates)

**Uses:** Dramatiq, Upstash Redis, PostgreSQL state tracking

**Addresses:** Table stakes features (batch processing, error handling), Critical Pitfall #3 (memory leaks via max-tasks-per-child configuration)

**Avoids:** Webhook timeouts, lost tasks, poor UX during processing

**Research flag:** Skip research-phase (Dramatiq patterns well-documented)

---

### Phase 3: Multi-Format Document Extraction
**Rationale:** PDFs hold Forderungsaufstellungen (claim statements) — the most critical data. Without PDF support, system cannot handle primary document type. Once async infrastructure exists, add extraction capabilities.

**Delivers:**
- PDF extraction pipeline: PyMuPDF text → fallback Claude Vision for scans
- DOCX extraction via python-docx
- XLSX extraction via openpyxl
- Image extraction via Claude Vision
- Page-by-page processing with token budgets (prevent token explosion)
- Hybrid strategy selection per document quality

**Uses:** PyMuPDF, pdf2image, python-docx, openpyxl, Claude Vision API

**Addresses:** Table stakes feature (PDF processing), Critical Pitfall #1 (PDF token explosion)

**Implements:** Content Extraction Agent (Agent 2) from architecture

**Avoids:** Massive token costs, rate limit errors, processing failures on large documents

**Research flag:** NEEDS RESEARCH-PHASE for Claude Vision API integration (verify current token limits, image size restrictions, pricing, batch processing patterns)

---

### Phase 4: German Document Extraction & Validation
**Rationale:** Generic extraction will fail on German Umlauts, locale formats, and legal terminology. Domain-specific handling required before production use.

**Delivers:**
- Unicode normalization (NFKC) preprocessing
- German-specific Claude prompts with German examples
- Locale-aware number parsing (de_DE for 1.234,56 EUR)
- Validation regexes for German names, addresses, amounts
- OCR post-processing (ii→ü, ss→ß correction)
- Test corpus of real German creditor documents with ground truth

**Addresses:** Critical Pitfall #5 (German text parsing fragility), differentiator feature (German legal document understanding)

**Avoids:** Name mismatches (Müller vs Muller), wrong amounts, legal compliance issues

**Research flag:** Skip research-phase (German text processing patterns established, but validate regex patterns during implementation)

---

### Phase 5: Multi-Agent Pipeline with Validation
**Rationale:** Three-agent architecture (Email Processor → Content Extractor → Consolidator) requires validation layers between stages to prevent error propagation. This is the architectural foundation for reliability.

**Delivers:**
- Agent 1: Email Processing Agent (intent classification, routing)
- Agent 2: Content Extraction Agent (already built in Phase 3, now integrated)
- Agent 3: Consolidation Agent (merge data, resolve conflicts)
- Validation layer after each agent (schema validation, confidence thresholds)
- Checkpoint system (save intermediate results for replay)
- Circuit breaker (halt pipeline if validation failure rate >20%)

**Implements:** Multi-agent architecture from ARCHITECTURE.md

**Addresses:** Critical Pitfall #4 (error cascades), table stakes feature (confidence scoring)

**Avoids:** Confidently wrong results, debugging nightmares, tight coupling

**Research flag:** Skip research-phase (multi-stage pipeline patterns established)

---

### Phase 6: Matching Engine Reconstruction
**Rationale:** Current matching engine is bypassed (dead code). Rebuild with explainability, configurable strategies, and validation dataset before enabling.

**Delivers:**
- Rule-based matching system with explainability layer (logs reasoning)
- Multiple strategies: exact match, fuzzy match, reference-based
- Configurable thresholds per creditor category (no redeployment needed)
- Match score breakdown (name_similarity: 0.9, address_similarity: 0.7)
- Labeled validation dataset (500+ examples) for threshold tuning
- Human-in-the-loop for ambiguous matches

**Uses:** creditor_inquiries PostgreSQL table (find sent inquiries to narrow search space)

**Addresses:** Differentiator feature (smart matching to existing records), Moderate Pitfall #9 (matching opacity)

**Avoids:** Wrong matches, false positives, inability to tune without code changes

**Research flag:** Skip research-phase (entity resolution patterns established, but create validation dataset during implementation)

---

### Phase 7: Confidence Scoring & Calibration
**Rationale:** Meaningful confidence scores enable automation decisions. Without calibration, scores are meaningless (95% confidence on wrong results defeats purpose).

**Delivers:**
- Separate confidence dimensions (OCR quality, extraction confidence, match confidence)
- Calibration dataset (500+ labeled examples with ground truth)
- Threshold tuning via precision-recall curves per document type
- Confidence = min(all_stages) (overall confidence is weakest link)
- Monthly recalibration using production feedback
- Different thresholds for native PDFs vs scans

**Addresses:** Table stakes feature (confidence scoring), Critical Pitfall #6 (confidence meaninglessness)

**Avoids:** High-confidence errors, inability to automate based on confidence, user distrust

**Research flag:** Skip research-phase (ML calibration techniques established)

---

### Phase 8: Database-Backed Prompt Management
**Rationale:** Prompts will evolve frequently. Database versioning enables A/B testing, instant rollback, and audit trails without redeployment.

**Delivers:**
- PostgreSQL prompt_templates table with versioning schema
- Prompt performance tracking (tokens used, execution time, feedback score)
- Jinja2 template engine for variable interpolation
- A/B testing framework (run multiple prompt versions, compare results)
- Gradual rollout (deploy new prompt to 10% of traffic first)
- Prompt diffing and audit log

**Uses:** PostgreSQL, Jinja2, Pydantic for validation

**Addresses:** Moderate Pitfall #7 (prompt version chaos)

**Avoids:** Production behavior changes without warning, inability to rollback, debugging with unknown prompt versions

**Research flag:** Skip research-phase (database versioning patterns established)

---

### Phase 9: Production Hardening & Monitoring
**Rationale:** Operational complexity is underestimated risk. Must add monitoring, error handling, and recovery tools before full production load.

**Delivers:**
- Structured logging with correlation IDs (request_id propagates webhook → final write)
- Prometheus metrics (queue depth, processing duration, token usage, confidence distribution)
- Sentry error tracking with context (email_id, job_id, agent)
- Circuit breakers for external dependencies (Claude API, MongoDB, GCS)
- Synthetic test data generation (German creditor emails without PII)
- End-to-end integration tests (entire pipeline with fixtures)
- Reconciliation dashboard (PostgreSQL vs MongoDB consistency checks)

**Addresses:** Moderate pitfalls (error message uselessness, test data leakage), general production stability

**Avoids:** Flying blind, hours of debugging per issue, GDPR violations, same errors repeating

**Research flag:** Skip research-phase (monitoring/observability patterns established)

---

### Phase 10: Gradual Migration & Cutover
**Rationale:** v1 continues production while v2 validates in shadow mode. Gradual cutover (10% → 50% → 100%) reduces risk.

**Delivers:**
- v2 processes same emails as v1 in shadow mode (no writes)
- Comparison report (v1 vs v2 outputs for accuracy validation)
- Traffic routing configuration (10% to v2, 90% to v1)
- Monitoring for error rates and extraction quality during ramp
- v1 remains as fallback for 30 days post-cutover
- Cleanup: remove v1 code after validation period

**Addresses:** Migration risk, production stability

**Avoids:** Big-bang deployment disasters, inability to rollback

**Research flag:** Skip research-phase (blue-green deployment patterns)

---

### Phase Ordering Rationale

**Why this order:**
1. **Foundation first:** Fix data consistency before adding complexity (Phase 1)
2. **Infrastructure before features:** Async queue infrastructure (Phase 2) enables attachment processing (Phase 3)
3. **Generic before specialized:** Multi-format extraction (Phase 3) before German-specific handling (Phase 4)
4. **Extraction before matching:** Must extract data reliably (Phases 3-4) before matching to records (Phase 6)
5. **Validation at each layer:** Build validation into multi-agent pipeline (Phase 5) as extraction capabilities grow
6. **Quality before scale:** Confidence calibration (Phase 7) and prompt management (Phase 8) before full production load
7. **Operational readiness last:** Production hardening (Phase 9) after core functionality proven
8. **Risk mitigation:** Gradual migration (Phase 10) reduces deployment risk

**Dependency chains:**
- Phase 2 → Phase 3: Async infrastructure required for long-running attachment processing
- Phase 3 → Phase 4: Generic extraction must work before German-specific optimization
- Phase 3 → Phase 5: Content extraction is Agent 2, needed for multi-agent pipeline
- Phase 5 → Phase 6: Consolidation Agent (Phase 5) hands validated data to Matching Engine (Phase 6)
- Phase 6 → Phase 7: Matching confidence is one dimension of overall confidence scoring

**Architecture alignment:**
- Phases 1-2 build infrastructure layer (databases, queue, workers)
- Phases 3-4 build extractor components
- Phases 5-6 build agent layer (pipeline orchestration, matching)
- Phases 7-8 build quality systems (confidence, prompts)
- Phases 9-10 build operational layer (monitoring, migration)

**Pitfall mitigation:**
- Phase 1 fixes Critical Pitfall #2 (database consistency)
- Phase 2 addresses Critical Pitfall #3 (memory leaks) via worker configuration
- Phase 3 prevents Critical Pitfall #1 (token explosion) via budgeting
- Phase 4 solves Critical Pitfall #5 (German text parsing)
- Phase 5 prevents Critical Pitfall #4 (error cascades) via validation
- Phase 6 fixes Moderate Pitfall #9 (matching opacity)
- Phase 7 addresses Critical Pitfall #6 (confidence meaninglessness)
- Phase 8 prevents Moderate Pitfall #7 (prompt chaos)

### Research Flags

**Phases needing deeper research during planning:**
- **Phase 3 (Multi-Format Extraction):** Claude Vision API integration specifics — current token limits, image size restrictions, batch processing patterns, pricing. Research conducted without access to official docs (training data through Jan 2025). Must verify before implementation.

**Phases with standard patterns (skip research-phase):**
- **Phase 1 (Database Consistency):** Saga pattern, two-phase commit alternatives well-documented
- **Phase 2 (Async Queue):** Dramatiq patterns established, Render deployment guides available
- **Phase 4 (German Text):** Unicode normalization, locale parsing are standard libraries
- **Phase 5 (Multi-Agent):** Pipeline validation patterns established in distributed systems literature
- **Phase 6 (Matching):** Entity resolution patterns well-documented, need validation dataset creation
- **Phase 7 (Confidence):** ML calibration techniques (Brier score, precision-recall curves) established
- **Phase 8 (Prompts):** Database versioning is standard CRUD with audit trail
- **Phase 9 (Production):** Observability patterns (Prometheus, structured logging) well-documented
- **Phase 10 (Migration):** Blue-green deployment, traffic routing are standard DevOps

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | **MEDIUM** | Dramatiq/Celery comparison sound, but version numbers need verification against current releases (training data through Jan 2025). Claude Vision API limits may have changed. Upstash Redis recommendation sound but verify 2026 Render integrations. |
| Features | **MEDIUM-HIGH** | Feature landscape based on domain knowledge and project context. Table stakes vs differentiators are logical, but actual document type distribution should be measured from production logs. German legal terminology understanding is HIGH confidence. |
| Architecture | **HIGH** | Multi-agent pipeline patterns, PostgreSQL state machine, dual-database strategy are established distributed systems patterns. Render deployment constraints well-known. Agent communication via database state is proven pattern. |
| Pitfalls | **HIGH** | PDF token explosion, memory leaks, dual-database consistency, error propagation, German text parsing are well-documented problems with established solutions. Confidence levels in PITFALLS.md accurately reflect uncertainty. |

**Overall confidence:** MEDIUM

Research was conducted without access to external verification tools (WebSearch, WebFetch, Context7 unavailable). All recommendations based on training data current through January 2025. Architecture patterns and library choices are sound, but specific versions, Claude Vision API capabilities, and 2026 best practices require verification.

### Gaps to Address

**Critical verification needed before implementation:**
- **Claude Vision API specs:** Token limits for images, page processing best practices, current pricing, batch processing support. Research recommends page-by-page with 100K token limit — verify this is still recommended approach.
- **Anthropic SDK version:** Training data shows v0.21.0, but API evolving rapidly. Check current version and breaking changes.
- **Render + Dramatiq deployment:** Verify Background Worker configuration for Dramatiq, memory limits, process/thread settings for 512MB instances.
- **Upstash Redis free tier:** Confirm 10K commands/day still available, verify Dramatiq compatibility.
- **PyMuPDF current version:** Check compatibility with Python 3.11+ and current best practices for table extraction.

**Open questions for validation:**
- **Actual document type distribution:** Measure from production email logs to validate intent classification categories (estimated: Forderungsaufstellung 40%, Ratenzahlungsvereinbarung 25%, Ablehnung 20%, Rückfrage 10%, Status 5%).
- **High-volume creditors:** Identify which creditors represent 80% of volume to prioritize template recognition.
- **Acceptable error rate:** Define threshold for automatic vs manual processing (recommendation: <90% confidence → manual review).
- **Email ingestion method:** Clarify how emails arrive (Zendesk webhook schema needs attachment fields added).
- **MongoDB schema flexibility:** Confirm mandanten-portal Node.js service ignores unknown fields in final_creditor_list (allows v2 to add extraction metadata).

**Architectural assumptions to validate:**
- PostgreSQL as single source of truth is sound pattern, but confirm mandanten-portal can tolerate eventual consistency for MongoDB (recommended: hourly reconciliation acceptable).
- Three-agent pipeline assumes sequential processing (Agent 1 → Agent 2 → Agent 3). If any agent requires human intervention, need pause/resume pattern.
- Render's 512MB memory limit assumed for worker instances. If using Standard tier ($25/month), limits may differ.

**Risk areas with lower confidence:**
- Cost projections (Claude API $100-150/month for 600 docs/day) may be outdated. Verify current Claude pricing.
- German-specific regex patterns need validation against real creditor documents (build test corpus during Phase 4).
- Confidence calibration assumes 500+ labeled examples is sufficient. May need more for rare document types.

## Sources

### Stack Research (STACK.md)
**Confidence: MEDIUM** — Training data through Jan 2025, official docs unavailable during research
- Dramatiq documentation patterns (https://dramatiq.io/)
- Celery comparison based on deployment patterns
- Anthropic API patterns (verify: https://docs.anthropic.com/)
- PyMuPDF documentation (verify: https://pymupdf.readthedocs.io/)
- Render deployment constraints (verify: https://render.com/docs)
- Upstash Redis recommendation (verify: https://upstash.com/)

### Features Research (FEATURES.md)
**Confidence: MEDIUM** — Based on domain knowledge, cannot verify current sources
- German debt collection workflows (domain-specific)
- Document processing systems architecture (established patterns)
- Legal automation patterns (training data)
- Email classification systems (training data)

### Architecture Research (ARCHITECTURE.md)
**Confidence: MEDIUM-HIGH** — Established patterns, specific integration details need verification
- FastAPI best practices (established)
- Dramatiq documentation patterns (https://dramatiq.io/)
- PostgreSQL state machine patterns (established)
- Render deployment constraints (https://render.com/docs)
- Multi-agent LLM pipeline design patterns (emerging)

### Pitfalls Research (PITFALLS.md)
**Confidence: MEDIUM** — Training data through Jan 2025, official docs unavailable
- Distributed systems literature (consistency, error handling)
- Document processing best practices (OCR, PDF handling)
- ML system design patterns (confidence calibration, prompt versioning)
- German text processing challenges (Unicode, locale handling)

### Verification Checklist
Before implementation, verify:
- [ ] Latest Dramatiq version and Redis compatibility
- [ ] Current Claude Vision API image size limits and pricing
- [ ] Upstash Redis free tier limits (10K commands/day availability)
- [ ] PyMuPDF version compatible with Python 3.11+
- [ ] Anthropic Python SDK latest version and breaking changes
- [ ] Render Background Worker specifics for Dramatiq (memory, processes, threads)
- [ ] Current Claude API rate limits for production tier
- [ ] MongoDB schema compatibility with mandanten-portal Node.js service

---

**Research completed:** 2026-02-04
**Ready for roadmap:** YES

**Next step:** Use this summary to structure roadmap with 10 phases. Phase 3 requires `/gsd:research-phase` for Claude Vision API integration before detailed planning. All other phases can proceed with standard patterns.
