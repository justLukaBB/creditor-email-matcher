# Domain Pitfalls: Creditor Email Analysis System Upgrade

**Domain:** Email/Document Processing Automation with Multi-Agent LLM Pipeline
**Researched:** 2026-02-04
**Confidence:** MEDIUM (based on training data through Jan 2025, official docs unavailable during research)

**Note:** This research was conducted without access to current official documentation. All findings should be validated against current Anthropic, Celery, Render, and other official sources before implementation. Confidence levels reflect this limitation.

---

## Critical Pitfalls

Mistakes that cause rewrites, data corruption, or production failures.

### Pitfall 1: PDF Token Explosion with Multi-Page Documents

**What goes wrong:** Processing multi-page PDFs through Claude Vision API without pagination strategy causes massive token consumption, hitting rate limits and creating unpredictable costs. A 50-page PDF can consume 500K+ tokens.

**Why it happens:**
- Each page of a PDF becomes a separate image in the API request
- Default behavior sends all pages at once
- No mechanism to estimate token usage before submission
- Poor quality scans increase token usage (more detail = more tokens)

**Consequences:**
- Rate limit errors (429) causing cascade failures
- Unpredictable monthly costs (can spike 10-100x)
- Worker timeouts when processing large documents
- User-facing errors with no retry strategy

**Prevention:**
1. Implement page-by-page processing with token budgeting
2. Set hard limits: max 10 pages per document or 100K tokens per job
3. Pre-process PDFs to estimate page count and quality
4. Use streaming/chunking strategy for documents >5 pages
5. Implement cost circuit breakers (halt processing if daily cost threshold exceeded)

**Detection:**
- Monitor token usage per request (log every API call)
- Alert when single request >50K tokens
- Track cost per email processed
- Watch for 429 rate limit errors

**Phase impact:** Address in Phase 2 (Attachment Processing Foundation) - this is a blocking issue for production.

**Confidence:** MEDIUM - Based on general Claude API behavior patterns from training data. Verify current token limits and pricing with Anthropic docs.

---

### Pitfall 2: Dual-Database Consistency Hell

**What goes wrong:** MongoDB and PostgreSQL get out of sync. An email is marked as "processed" in PostgreSQL but extraction data is missing from MongoDB. Or vice versa: extraction exists in MongoDB but no reference in PostgreSQL. The system has no way to detect or repair these inconsistencies.

**Why it happens:**
- No distributed transaction coordinator between MongoDB and PostgreSQL
- Partial failures during multi-step operations
- Retry logic that succeeds on one DB but fails on the other
- No consistency checking or reconciliation process
- "Fire and forget" writes to both databases

**Consequences:**
- Silent data loss (user sees "processed" but no extracted data)
- Duplicate processing (retry sees "not in PostgreSQL" and reprocesses)
- Unable to trust either database as source of truth
- Debugging nightmares (which DB is correct?)
- Data corruption accumulates over time

**Prevention:**
1. **Implement Saga Pattern:** Each operation is a state machine with compensating transactions
2. **Single Source of Truth:** Make PostgreSQL authoritative, MongoDB as cache/view
3. **Write-Ahead Logging:** Log intent before writing to either DB
4. **Idempotency Keys:** Every operation has unique ID to prevent duplicate writes
5. **Reconciliation Job:** Nightly job comparing both DBs and flagging mismatches
6. **Atomic Status Updates:** Never mark "processed" until both DBs confirm write

**Detection:**
- Count mismatches: `SELECT COUNT(*) FROM postgres WHERE id NOT IN (SELECT ref_id FROM mongo)`
- Monitor failed DB writes (log every exception)
- Alert on retry count >3 for any email
- Weekly consistency report comparing DB row counts

**Phase impact:** Address in Phase 1 (Dual-Database Audit) - this is currently causing the matching engine bypass issue.

**Confidence:** HIGH - Dual-database consistency is a well-known distributed systems problem with established patterns.

---

### Pitfall 3: Celery Worker Memory Leaks on Render

**What goes wrong:** Celery workers slowly consume all available memory on Render, eventually hitting OOM (Out of Memory) kills. Workers restart, lose in-progress tasks, and the cycle repeats. On Render's free/hobby tiers (512MB RAM), this happens in hours.

**Why it happens:**
- PDF processing libraries (PIL, PyPDF2, pdf2image) don't release memory after processing
- Claude API responses (large JSON payloads) accumulate in worker memory
- No `--max-tasks-per-child` configuration (workers never restart)
- Image preprocessing keeps decoded images in memory
- Celery result backend stores results in memory before Redis write

**Consequences:**
- Unpredictable worker crashes (looks like infrastructure failure)
- Lost tasks (no retry after OOM kill)
- Cascade failures (all workers die, queue backs up)
- Render free tier: daily restarts required
- User-facing: "system unavailable" errors during worker downtime

**Prevention:**
1. **Set max-tasks-per-child=50:** Force worker restart after 50 tasks
2. **Explicit memory cleanup:** Call `gc.collect()` after each PDF processing task
3. **Stream processing:** Don't load entire PDF into memory, process page-by-page
4. **Monitor memory:** Log memory usage before/after each task
5. **Resource limits:** Set Celery worker memory limit to 80% of Render allocation
6. **Circuit breaker:** If memory >90%, stop accepting new tasks until cleanup

**Detection:**
- Monitor worker memory usage (log `psutil.Process().memory_info().rss`)
- Alert when memory >80% of allocation
- Track OOM kill events (check Render logs for exit code 137)
- Monitor task failure rate (sudden spikes indicate worker crashes)

**Phase impact:** Address in Phase 3 (Celery Robustness) - production blocker.

**Confidence:** HIGH - Memory leaks in Celery workers processing large files is a well-documented pattern. Render's memory limits make it worse.

---

### Pitfall 4: Multi-Agent Pipeline Error Cascades

**What goes wrong:** Agent 1 (email extractor) makes a mistake. Agent 2 (attachment processor) accepts the bad data as truth. Agent 3 (matcher) uses the corrupted data to make a wrong match. The error amplifies through the pipeline, and by the end, you have a confidently wrong result with no indication anything went wrong.

**Why it happens:**
- Each agent assumes previous agent succeeded
- No validation between pipeline stages
- Confidence scores from Agent 1 aren't checked by Agent 2
- Partial extractions treated as complete extractions
- No "sanity check" layer between agents
- LLM hallucinations compound across agents

**Consequences:**
- Wrong creditor matched to wrong debtor (legal liability)
- High confidence scores on completely wrong results
- Unable to debug (which agent introduced the error?)
- User trust erodes (system "seems confident but is often wrong")
- Fixing one agent breaks the pipeline (tight coupling)

**Prevention:**
1. **Explicit Validation Layer:** After each agent, validate output schema and business rules
2. **Confidence Thresholds:** Agent 2 refuses to process if Agent 1 confidence <0.7
3. **Checkpoint System:** Save intermediate results, enable "replay from checkpoint"
4. **Cross-Validation:** If Agent 3 detects inconsistency, flag for human review
5. **Circuit Breaker:** If validation failure rate >20%, halt pipeline and alert
6. **Idempotency:** Each agent can be re-run independently without side effects

**Detection:**
- Log confidence score at each stage
- Monitor validation failure rates per agent
- Alert when final confidence <0.5 (indicates error propagation)
- Track "human override" rate (indicates systemic issues)
- Compare agent outputs to ground truth on test set

**Phase impact:** Address in Phase 5 (Multi-Agent Orchestration) - architectural foundation.

**Confidence:** HIGH - Error propagation in multi-stage pipelines is a fundamental system design issue.

---

### Pitfall 5: German Name/Address Parsing Fragility

**What goes wrong:** System fails to extract German names with Umlauts (Müller → Muller), splits compound addresses incorrectly ("Hauptstraße 123a" becomes "Hauptstra 123a"), and misparses German number formats (1.234,56 EUR interpreted as 1234.56).

**Why it happens:**
- OCR systems struggle with Umlauts in poor quality scans (ü becomes u or ii)
- LLM tokenizers trained primarily on English corpus
- Prompts use English examples (biases Claude toward English patterns)
- German address formats differ from English (street number after name)
- Comma as decimal separator conflicts with English comma parsing
- No validation against German-specific patterns (e.g., ZIP code format)

**Consequences:**
- Creditor names don't match database (Müller GmbH vs Muller GmbH)
- Address matching fails (can't find creditor by address)
- Financial amounts wrong by orders of magnitude (1.234,56 → 1234.56 or 1.23)
- Legal compliance issues (wrong creditor name in official documents)
- User correction overhead (manual fixes for every German document)

**Prevention:**
1. **Unicode normalization:** Preprocess all text with NFKC normalization
2. **German-specific prompts:** Include German examples in Claude prompts
3. **Locale-aware parsing:** Use `locale.setlocale(locale.LC_ALL, 'de_DE')` for numbers
4. **Validation regexes:** Check extracted names/addresses against German patterns
5. **OCR post-processing:** Map common OCR errors (ii→ü, ss→ß)
6. **Fuzzy matching:** Use Levenshtein distance for name matching (threshold 0.85)
7. **Test data:** Build test corpus of German documents with ground truth

**Detection:**
- Monitor extraction confidence for German documents vs English
- Flag emails with Umlauts where extraction has ASCII-only results
- Alert on amount extractions with suspicious values (>€100K or <€1)
- Track user correction frequency for German documents

**Phase impact:** Address in Phase 4 (German Document Extraction) - domain-specific blocker.

**Confidence:** HIGH - German text processing challenges are well-documented, especially Umlauts and number formats.

---

### Pitfall 6: Confidence Score Meaninglessness

**What goes wrong:** The system reports "95% confidence" on extractions that are completely wrong, and "60% confidence" on perfect extractions. Users stop trusting the confidence scores and ignore them entirely, defeating the purpose of having a confidence-based review queue.

**Why it happens:**
- Conflating multiple confidence sources (OCR confidence ≠ LLM confidence ≠ match confidence)
- No calibration against ground truth data
- Confidence calculated from Claude's response format, not actual accuracy
- "Confidence score" is actually just "how sure the prompt response looks"
- Threshold tuning without measuring precision/recall tradeoffs
- Different document types need different thresholds (PDFs vs scans)

**Consequences:**
- Human reviewers waste time on high-confidence errors
- Low-confidence correct extractions sit in queue forever
- Unable to automate decisions based on confidence
- Threshold changes are random guesses
- System appears "smart" but behaves randomly

**Prevention:**
1. **Separate confidence dimensions:** OCR quality, extraction confidence, match confidence (report all three)
2. **Calibration dataset:** 500+ labeled examples with ground truth
3. **Threshold tuning:** Use precision-recall curve to find optimal thresholds per document type
4. **Confidence validation:** Compare confidence to actual accuracy on validation set
5. **Confidence = min(all_stages):** Overall confidence is minimum of all pipeline stages
6. **Periodic recalibration:** Monthly review of confidence accuracy using production data
7. **Document type stratification:** Different thresholds for native PDFs vs scans

**Detection:**
- Track confidence vs accuracy scatter plot (should be correlated)
- Alert when high-confidence (>0.9) errors occur
- Monitor human override rate per confidence bucket
- Calculate Brier score (measures calibration quality)

**Phase impact:** Address in Phase 7 (Confidence Scoring System) - critical for automation decisions.

**Confidence:** HIGH - Confidence calibration is a well-studied ML problem with established metrics.

---

## Moderate Pitfalls

Mistakes that cause delays, technical debt, or operational overhead.

### Pitfall 7: Prompt Version Chaos

**What goes wrong:** Prompts are embedded in code as strings. When someone "improves" a prompt, behavior changes across the entire system with no rollback mechanism. Different environments run different prompt versions. Debugging is impossible because you can't tell which prompt version was used for a specific extraction.

**Why it happens:**
- Prompts live in Python strings, not versioned artifacts
- No prompt testing before deployment
- Changes deployed immediately to production
- No audit trail of prompt changes
- Engineers iterate on prompts without coordination

**Consequences:**
- Production behavior changes unexpectedly
- Can't reproduce bugs (prompt changed since bug occurred)
- Rollback requires redeploying code
- No A/B testing capability
- Blame game when quality degrades ("who changed the prompt?")

**Prevention:**
1. **Prompt registry:** Store prompts in database/config with version IDs
2. **Version tagging:** Every extraction logs prompt version used
3. **Prompt testing:** Test suite runs on every prompt change
4. **Gradual rollout:** New prompts deployed to 10% of traffic first
5. **Prompt diffing:** Track exact changes between versions
6. **Environment parity:** Dev/staging/prod use same prompt registry

**Detection:**
- Log prompt version with every API call
- Monitor quality metrics per prompt version
- Alert when extraction quality drops >10% after prompt change
- Track prompt changes in audit log

**Phase impact:** Address in Phase 8 (Prompt Management) - operational quality issue.

**Confidence:** MEDIUM - Prompt versioning patterns based on MLOps best practices, but less established than traditional CI/CD.

---

### Pitfall 8: Attachment Processing Timeouts

**What goes wrong:** Processing a 20MB PDF with 50 pages times out after 30 seconds. Celery marks the task as failed and retries. The retry also times out. After 3 retries, the email is marked as "failed" and sits in a dead letter queue forever.

**Why it happens:**
- Fixed timeout (30s) regardless of document size/complexity
- Processing is synchronous (waits for Claude API response)
- No progress tracking (can't resume partial processing)
- Retry logic doesn't account for timeout root cause
- Large PDFs take 2-5 minutes to process (OCR + Claude API)

**Consequences:**
- Large documents never process successfully
- Users receive "processing failed" errors for valid emails
- Retry storms (same document retried indefinitely)
- Manual intervention required for every large document
- Queue backlog of legitimately large documents

**Prevention:**
1. **Dynamic timeouts:** Scale timeout based on page count (30s per page)
2. **Chunked processing:** Split large PDFs into 5-page chunks, process independently
3. **Async processing:** Don't block on Claude API, use callback pattern
4. **Progress checkpoints:** Save "processed pages 1-10" state, resume from checkpoint
5. **Size limits:** Reject documents >25MB or >50 pages with clear error
6. **Timeout escalation:** First timeout → double timeout, second timeout → manual queue

**Detection:**
- Monitor task duration distribution (p50, p95, p99)
- Alert when task duration >2x median
- Track timeout rate per document size bucket
- Log correlation between file size and timeout rate

**Phase impact:** Address in Phase 2 (Attachment Processing Foundation) - scalability issue.

**Confidence:** HIGH - Timeout issues with large document processing are well-documented patterns.

---

### Pitfall 9: Matching Algorithm Opacity

**What goes wrong:** The matching engine is bypassed (current state), and when re-enabled, it will use hardcoded rules that are opaque to operators. When a wrong match occurs, there's no way to understand why. When a correct match is missed, there's no way to fix it without code changes.

**Why it happens:**
- Matching logic embedded in procedural code
- No explainability layer ("matched because X, Y, Z")
- Hardcoded thresholds (0.8 similarity = match)
- No configurable matching strategies per creditor type
- String similarity metrics without business logic validation

**Consequences:**
- Can't tune matching without redeploying code
- False positives go undetected (wrong match with high confidence)
- False negatives accumulate (correct matches missed)
- Unable to explain to users why a match was made
- Special cases require code changes (can't add business rules)

**Prevention:**
1. **Rule-based system:** Express matching logic as configurable rules
2. **Explainability layer:** Log every matching decision with reasoning
3. **Match scores breakdown:** Report (name_similarity: 0.9, address_similarity: 0.7, ...)
4. **Threshold configuration:** Make thresholds configurable per creditor category
5. **Business rule validation:** After algorithmic match, check business constraints
6. **Human-in-the-loop:** Flag matches below threshold for review
7. **A/B testing framework:** Test new matching strategies on historical data

**Detection:**
- Log every match decision with full context
- Track false positive rate (human overrides)
- Monitor missed matches (emails marked "no match found")
- Calculate precision/recall on labeled validation set

**Phase impact:** Address in Phase 6 (Matching Engine Reconstruction) - core functionality.

**Confidence:** HIGH - Matching system design patterns are well-established in entity resolution literature.

---

### Pitfall 10: Redis Connection Pool Exhaustion

**What goes wrong:** On Render, Redis has connection limits (typically 20-30 on free tier). Under load, Celery workers exhaust the connection pool. New tasks can't acquire Redis connections and fail with "connection timeout" errors.

**Why it happens:**
- Each Celery worker opens multiple Redis connections (task queue, result backend, locks)
- Workers don't release connections properly after tasks complete
- No connection pooling configuration
- Render Redis free tier has low connection limits
- Concurrent email bursts (50 emails arrive at once)

**Consequences:**
- Tasks fail with cryptic "Redis connection timeout" errors
- System appears "down" during peak load
- Manual intervention required (restart workers to reset connections)
- Lost tasks (failed to acquire connection = task never runs)
- Poor user experience during email bursts

**Prevention:**
1. **Connection pooling:** Configure Celery with explicit Redis connection pool
2. **Connection limits:** Set `redis_max_connections` to 80% of Render limit
3. **Connection timeout:** Set reasonable timeout (5s) to fail fast
4. **Circuit breaker:** If Redis connections fail 3x, pause worker and alert
5. **Render tier upgrade:** Production should use paid tier (unlimited connections)
6. **Connection monitoring:** Log active Redis connections per worker
7. **Graceful degradation:** If Redis unavailable, store tasks locally and replay

**Detection:**
- Monitor active Redis connections (`INFO clients` command)
- Alert when connections >80% of limit
- Track "connection timeout" error rate
- Log connection pool state before each task

**Phase impact:** Address in Phase 3 (Celery Robustness) - production stability.

**Confidence:** HIGH - Redis connection pooling issues are well-documented, especially on free tiers.

---

## Minor Pitfalls

Mistakes that cause annoyance or minor technical debt but are fixable.

### Pitfall 11: Test Data Leakage

**What goes wrong:** Development/testing uses production data (real creditor emails with PII). Test prompts accidentally get sent to production. No clear separation between test and prod environments.

**Why it happens:**
- No synthetic test data generation
- Copying production MongoDB to local for testing
- Shared Claude API key across environments
- Testing in production ("just one email to test...")
- Insufficient environment variable separation

**Consequences:**
- GDPR/privacy violations (PII in logs, development machines)
- Test prompts pollute production (weird extractions)
- Can't share code/bugs externally (contains real data)
- Legal liability if test data leaks

**Prevention:**
1. **Synthetic data:** Generate fake German creditor emails for testing
2. **Environment separation:** Separate Claude API keys for dev/staging/prod
3. **Data masking:** Automatic PII redaction in non-prod environments
4. **Test fixtures:** Curated test corpus of anonymized documents
5. **Production flags:** Code checks `ENV == 'production'` before using prod DB

**Detection:**
- Monitor API key usage by environment
- Alert if prod API key used from non-prod IP
- Audit log access to production data

**Phase impact:** Address in Phase 9 (Testing Infrastructure) - compliance issue.

**Confidence:** HIGH - Environment separation is standard DevOps practice.

---

### Pitfall 12: Error Message Uselessness

**What goes wrong:** User gets error "Processing failed" with no context. Logs show "Exception in task 12345" with no traceback. Debugging requires SSH into Render, checking multiple log files, and guessing what went wrong.

**Why it happens:**
- Exception handling swallows details (`except Exception: log("failed")`)
- No structured logging (no request IDs, user IDs, email IDs)
- Logs scattered across Celery workers, web server, Redis
- No error aggregation or correlation
- User-facing errors don't match internal errors

**Consequences:**
- Users can't self-diagnose issues
- Support team can't help (no actionable information)
- Debugging takes hours per issue
- Same errors repeat (can't identify patterns)

**Prevention:**
1. **Structured logging:** JSON logs with request_id, email_id, user_id, task_id
2. **Error codes:** Specific codes (ERR_PDF_TOO_LARGE, ERR_CLAUDE_TIMEOUT)
3. **Contextual errors:** Include relevant info in exceptions
4. **Log aggregation:** Use Render log drains to centralize logs
5. **User-facing explanations:** Map internal errors to helpful messages

**Detection:**
- Monitor "failed" status without error codes
- Alert on uncaught exceptions
- Track time-to-resolution per error type

**Phase impact:** Address in Phase 3 (Celery Robustness) - operational efficiency.

**Confidence:** HIGH - Logging best practices are well-established.

---

### Pitfall 13: Missing Idempotency

**What goes wrong:** Email arrives, triggers processing task. User refreshes page, triggers processing again. Same email processed twice, creates duplicate extractions in MongoDB, matches to two different creditors.

**Why it happens:**
- No idempotency keys on tasks
- No deduplication logic
- Web UI triggers task on every page load
- Retry logic doesn't check if task already succeeded
- No "processing" state (only "pending" or "done")

**Consequences:**
- Duplicate extractions confuse users
- Double billing if charged per extraction
- Matching algorithm breaks (multiple entries)
- Queue backlog from redundant processing

**Prevention:**
1. **Idempotency keys:** Use email_id as task ID
2. **Status checking:** Before queueing task, check if already processing/done
3. **Atomic state transitions:** Use database transaction to set "processing" status
4. **Retry safety:** Retries check if original task succeeded
5. **UI state management:** Disable "process" button after click

**Detection:**
- Monitor duplicate email_ids in task queue
- Alert on multiple extractions for same email
- Track task ID collision rate

**Phase impact:** Address in Phase 3 (Celery Robustness) - data quality issue.

**Confidence:** HIGH - Idempotency is a fundamental distributed systems pattern.

---

## Phase-Specific Warnings

| Phase | Topic | Likely Pitfall | Mitigation | Priority |
|-------|-------|----------------|------------|----------|
| 1 | Dual-Database Audit | Discovering 1000s of inconsistent records with no recovery plan | Build reconciliation tool before audit | CRITICAL |
| 2 | Attachment Processing | Memory exhaustion from loading full PDFs | Implement streaming + max-tasks-per-child first | CRITICAL |
| 2 | PDF Processing | Token cost explosion on first production run | Set hard limits + cost monitoring before launch | CRITICAL |
| 3 | Celery Workers | Redis connection exhaustion under load | Load test with 100 concurrent emails before prod | HIGH |
| 3 | Error Handling | Inconsistent error handling across codebase | Define error handling patterns in architecture doc | HIGH |
| 4 | German Extraction | Testing only with clean PDFs, failing on real scans | Build test corpus with real-world scan quality | HIGH |
| 4 | Locale Handling | Hardcoding German assumptions, breaking for other locales | Design for i18n from start, even if German-only now | MEDIUM |
| 5 | Multi-Agent Pipeline | Tight coupling between agents makes changes risky | Use message queue between agents, not direct calls | HIGH |
| 5 | Error Propagation | First agent error corrupts entire pipeline | Validation layer after each agent is non-negotiable | CRITICAL |
| 6 | Matching Engine | No way to measure matching quality without ground truth | Create labeled validation set (500+ examples) first | HIGH |
| 6 | False Positives | Optimizing for recall, accepting too many false positives | Define acceptable precision/recall tradeoff upfront | MEDIUM |
| 7 | Confidence Scoring | Confidence not calibrated against real accuracy | Build calibration dataset before implementing scoring | HIGH |
| 7 | Threshold Tuning | Arbitrary thresholds without data-driven rationale | Use ROC curves and business cost analysis | MEDIUM |
| 8 | Prompt Management | Prompts scatter across codebase, hard to maintain | Centralize prompts from day 1 | MEDIUM |
| 8 | Prompt Testing | No automated testing of prompt quality | Build prompt test suite with examples | HIGH |
| 9 | Testing | No integration tests, only unit tests | Test entire pipeline end-to-end | HIGH |
| 9 | Test Data | Using production data for testing | Generate synthetic test data | MEDIUM |

---

## Domain-Specific Anti-Patterns

Common mistakes in email/document processing automation systems:

### Anti-Pattern 1: Optimizing for Happy Path Only
**What it looks like:** System works great on clean, native PDFs. Fails on 80% of real-world documents (scans, photos, faxes).

**Why it's bad:** Real creditor responses are messy. Scanned documents, smartphone photos, faxed letters, all need to work.

**Instead:** Test with worst-case documents first. If it works on terrible scans, it'll work on clean PDFs.

---

### Anti-Pattern 2: Treating LLM Output as Truth
**What it looks like:** Whatever Claude extracts is written directly to database without validation.

**Why it's bad:** LLMs hallucinate. Confidently. Wrong extractions look as plausible as correct ones.

**Instead:** Validate every extraction against business rules and domain constraints before trusting it.

---

### Anti-Pattern 3: Synchronous Processing of Long Tasks
**What it looks like:** Web request waits for PDF processing to complete before responding. User sees "Loading..." for 3 minutes.

**Why it's bad:** Timeouts, poor UX, server resources blocked. Web dynos on Render have 30s timeout.

**Instead:** Queue task, return immediately, poll for results or use webhooks.

---

### Anti-Pattern 4: One-Size-Fits-All Prompts
**What it looks like:** Same extraction prompt for all document types (native PDF, scan, image, fax).

**Why it's bad:** Different document types need different instructions. Scans need OCR guidance, native PDFs need layout parsing.

**Instead:** Document type detection + specialized prompts per type.

---

### Anti-Pattern 5: No Circuit Breakers
**What it looks like:** If Claude API is down, system queues 1000s of tasks that all fail, exhausting retry budget and creating backlog.

**Why it's bad:** Cascade failures. One external dependency down brings entire system down.

**Instead:** Circuit breaker pattern: after 5 consecutive failures, stop trying for 5 minutes. Alert humans.

---

## Current System Red Flags

Based on project context, these existing patterns will cause issues:

| Red Flag | Why It's a Problem | Fix Priority |
|----------|-------------------|--------------|
| No tests | Can't refactor safely, bugs in production | HIGH |
| Hardcoded values | Can't tune without deployment, environment-specific bugs | HIGH |
| Inconsistent error handling | Can't debug, can't monitor, errors swallowed | CRITICAL |
| Matching engine bypassed | Core functionality broken, direct MongoDB writes | CRITICAL |
| No monitoring | Flying blind, can't detect issues until users complain | HIGH |
| 200+ emails/day on Render | Render free tier will not handle this | CRITICAL |
| No confidence scores | Can't prioritize review queue, can't automate decisions | HIGH |
| Dual-database writes | Consistency issues already present, will get worse | CRITICAL |

---

## Research Confidence Assessment

| Pitfall Category | Confidence Level | Rationale |
|-----------------|------------------|-----------|
| Claude Vision API | MEDIUM | Based on general API patterns, official docs unavailable |
| Celery Deployment | HIGH | Well-documented deployment patterns, Render limitations known |
| Multi-Agent Pipelines | HIGH | Established distributed systems patterns |
| German Text Processing | HIGH | Well-documented NLP challenges, Unicode handling |
| Dual-Database Consistency | HIGH | Classic distributed systems problem |
| Confidence Scoring | HIGH | Established ML calibration techniques |
| Prompt Engineering | MEDIUM | Emerging practices, less standardized |
| Attachment Processing | HIGH | Common file processing patterns |
| Matching/Reconciliation | HIGH | Established entity resolution patterns |
| Infrastructure (Render/Redis) | HIGH | Platform-specific but well-documented |

---

## Sources and Verification Needed

**Unable to verify during research (official docs unavailable):**
- Current Claude API token limits for Vision
- Anthropic's PDF processing best practices documentation
- Current Celery + Render deployment guides
- Official MongoDB + PostgreSQL consistency patterns

**Should be verified before implementation:**
1. Anthropic Vision API docs: https://docs.anthropic.com/en/docs/build-with-claude/vision
2. Celery deployment guide: https://docs.celeryq.dev/en/stable/userguide/deployment.html
3. Render memory limits and pricing: https://render.com/docs
4. Redis connection limits by Render tier: https://render.com/docs/redis

**Based on established patterns from:**
- Distributed systems literature (consistency, error handling)
- Document processing best practices (OCR, PDF handling)
- ML system design patterns (confidence calibration, prompt versioning)
- German text processing challenges (Unicode, locale handling)

---

## Key Takeaways for Roadmap

1. **Phase 1 must solve dual-database consistency** - this is the root cause of matching engine bypass
2. **Phase 2 attachment processing needs memory management from day 1** - Render will OOM without it
3. **Phase 4 German extraction needs real-world test corpus** - don't test on clean PDFs only
4. **Phase 5 multi-agent orchestration needs validation layers** - error propagation will kill quality
5. **Phase 7 confidence scoring must be calibrated** - meaningless scores defeat the purpose
6. **Phase 8 prompt management is not optional** - versioning chaos will happen otherwise

**Critical path:** Fix database consistency → robust attachment processing → validated multi-agent pipeline → calibrated confidence scoring

**Biggest risk:** Underestimating operational complexity (monitoring, error handling, debugging tools)
