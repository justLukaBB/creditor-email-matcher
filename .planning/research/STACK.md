# Technology Stack for V2 Upgrade

**Project:** Creditor Email Analysis - V2 Multi-Attachment Processing
**Researched:** 2026-02-04
**Context:** Upgrading existing FastAPI + Claude + PostgreSQL/MongoDB system on Render
**Volume:** 200+ emails/day with multi-format attachments

## Research Methodology Note

**IMPORTANT:** This research was conducted without access to external verification tools (WebSearch, WebFetch, Context7 unavailable). All recommendations are based on training data current through January 2025. Version numbers and current best practices should be verified against official documentation before implementation.

**Confidence Level:** MEDIUM - Architecture patterns and library choices are sound based on training data, but specific versions and 2026 best practices require verification.

## Executive Summary

For upgrading your system to handle multi-attachment processing with job queuing, the recommended approach prioritizes:
1. **Dramatiq over Celery** for simpler async processing on Render
2. **PyMuPDF (fitz) + Claude Vision** for robust PDF extraction
3. **python-docx + openpyxl** for Office document processing
4. **Database-backed prompt templates** using PostgreSQL with versioning schema
5. **Upstash Redis** for managed Redis on Render (or Railway Redis alternative)

This stack minimizes operational complexity while providing production-grade reliability for 200+ emails/day.

---

## Core Async Processing Stack

### Job Queue: Dramatiq (Recommended) or Celery

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| **Dramatiq** | ~1.17+ | Async task queue | **RECOMMENDED**: Simpler than Celery, better Render compatibility, built-in retries, less memory overhead |
| Redis | 7.x | Message broker | Industry standard, required for either queue |
| dramatiq-redis | ~1.7+ | Broker adapter | Official Redis adapter for Dramatiq |

**Rationale for Dramatiq over Celery:**

1. **Simpler deployment on Render:** Dramatiq has fewer moving parts (no flower, no beat scheduler by default), easier to containerize
2. **Better resource efficiency:** Lower memory footprint crucial for Render's pricing tiers
3. **Cleaner API:** More Pythonic, less boilerplate
4. **Built-in retries and dead letter queue:** Production features out of the box
5. **Thread-based workers:** Better for I/O-bound tasks like API calls and document processing

**When to choose Celery instead:**
- Need complex scheduling (celery-beat)
- Need canvas/chord workflow primitives
- Team already has Celery expertise

### Redis Hosting Options for Render

| Option | Cost | Latency | Why |
|--------|------|---------|-----|
| **Upstash Redis** | Free tier available, pay-per-request | Low (global edge) | **RECOMMENDED**: Best Render integration, generous free tier, automatic scaling |
| Railway Redis | $5/month base | Low (same region) | Good alternative, simpler pricing |
| Redis Cloud | $5/month starter | Low | Enterprise option if scaling beyond 200/day |
| Render-hosted Redis | Build yourself | Lowest | NOT recommended - adds complexity, no persistence guarantees |

**Recommendation:** Start with Upstash Redis free tier. Supports 10K commands/day (sufficient for 200 emails/day with ~40-50 commands per email job).

---

## Document Processing Stack

### PDF Extraction

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| **PyMuPDF (fitz)** | ~1.24+ | PDF text extraction | **PRIMARY**: Fastest, most reliable text extraction, table detection, metadata access |
| **pdf2image** | ~1.17+ | PDF to image conversion | For Claude Vision API processing |
| **Pillow** | ~10.x | Image processing | Resize/optimize images before Claude API |
| poppler-utils | System package | PDF rendering backend | Required by pdf2image |

**Processing Strategy:**
1. **Try PyMuPDF text extraction first** (fast, cost-effective)
2. **Fallback to Claude Vision** for scanned PDFs or complex layouts
3. **Use Claude Vision for tables** when PyMuPDF tables are malformed

### Office Document Processing

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| **python-docx** | ~1.1+ | DOCX extraction | Official Microsoft DOCX library, reliable structure parsing |
| **openpyxl** | ~3.1+ | XLSX extraction | Best for modern Excel files (.xlsx), good performance |
| **python-pptx** | ~0.6+ | PPTX extraction | If PowerPoint support needed (less common for creditors) |

**Alternative considered:** Apache Tika (rejected - JVM overhead, deployment complexity on Render)

### Image Processing

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| **Pillow** | ~10.x | Image normalization | Resize, convert formats, optimize for Claude Vision |
| **pytesseract** | ~0.3+ | OCR fallback | Backup for simple scanned documents (cheaper than Claude Vision) |

**OCR Strategy:**
- Use pytesseract for simple black-and-white scans (invoices, payment confirmations)
- Use Claude Vision for complex layouts, handwriting, multi-language documents

---

## Claude API Integration

### Vision API Best Practices (Training Data - Verify Current)

**Configuration for Document Extraction:**

```python
# Recommended approach based on January 2025 patterns
import anthropic
from anthropic import Anthropic

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# For PDF pages converted to images
message = client.messages.create(
    model="claude-3-5-sonnet-20241022",  # Latest as of training cutoff
    max_tokens=4096,  # Sufficient for extracted content
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",  # or image/png
                        "data": base64_image_data,
                    },
                },
                {
                    "type": "text",
                    "text": "Extract all text and structured data from this document..."
                }
            ],
        }
    ],
)
```

**Key Considerations:**

1. **Image Format:** Convert PDFs to JPEG at 150-300 DPI (balance quality vs. payload size)
2. **Max Image Size:** 5MB per image (as of training cutoff - verify current limits)
3. **Multi-page PDFs:** Process each page separately, then aggregate
4. **Token Optimization:** Use shorter prompts for extraction phase, detailed analysis in second phase
5. **Caching:** Enable prompt caching for repeated extraction patterns

**Rate Limiting:**
- Claude API supports 50 requests/minute on standard tier (as of training data)
- For 200 emails/day with ~3 attachments avg = 600 documents/day = ~1 per 2.4 minutes (well within limits)
- Implement exponential backoff for 429 responses

---

## Prompt Management Stack

### Database-Backed Prompt Repository

**Recommended Architecture:** PostgreSQL with versioning schema

```sql
-- Schema pattern for prompt versioning
CREATE TABLE prompt_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    intent_type VARCHAR(100) NOT NULL,  -- 'payment_confirmation', 'demand_letter', etc.
    version INTEGER NOT NULL,
    template_text TEXT NOT NULL,
    variables JSONB,  -- Expected variables for interpolation
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    created_by VARCHAR(255),
    performance_metrics JSONB,  -- Track accuracy, token usage
    UNIQUE(name, version)
);

CREATE INDEX idx_prompt_intent_active ON prompt_templates(intent_type, is_active);

-- Track prompt performance
CREATE TABLE prompt_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_template_id UUID REFERENCES prompt_templates(id),
    email_id UUID,  -- Link to processed email
    tokens_used INTEGER,
    execution_time_ms INTEGER,
    success BOOLEAN,
    feedback_score INTEGER,  -- Manual review feedback
    created_at TIMESTAMP DEFAULT NOW()
);
```

**Supporting Libraries:**

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| **SQLAlchemy** | ~2.0+ | ORM | Already in stack, good for versioning queries |
| **Jinja2** | ~3.1+ | Template engine | Variable interpolation in prompts |
| **Pydantic** | ~2.x | Validation | Validate prompt variables before execution |

**Pattern Benefits:**
1. **Version Control:** Track which prompt version produced which results
2. **A/B Testing:** Run multiple versions, compare performance
3. **Rollback:** Instantly revert to previous version if new prompt underperforms
4. **Analytics:** Query performance metrics across prompt versions
5. **Audit Trail:** Know who changed what and when

**Alternative Considered:** Filesystem with Git (rejected - harder to query, no runtime versioning, deployment complexity)

---

## Supporting Infrastructure

### File Storage Strategy

| Storage | Purpose | Why |
|---------|---------|-----|
| **Google Cloud Storage** (existing) | Original attachments | Already in stack, good retention policy support |
| **Temporary local disk** | Processing workspace | Extract attachments, convert PDFs, then delete |
| **PostgreSQL** | Extracted text, metadata | Fast querying, already in stack |

**Pattern:**
1. Email arrives → save attachments to GCS
2. Download to worker's temp directory for processing
3. Extract text/data → save to PostgreSQL
4. Delete temp files
5. Keep original in GCS for audit/reprocessing

### Monitoring & Observability

| Tool | Purpose | Cost |
|------|---------|------|
| **Sentry** | Error tracking | Free tier: 5K errors/month |
| **Prometheus + Grafana** | Metrics dashboard | Self-hosted on Render |
| **structlog** | Structured logging | Free, Python library |

**Key Metrics to Track:**
- Queue depth (Dramatiq backlog)
- Processing time per document type
- Claude API token usage and costs
- Error rates by document type
- Attachment format distribution

---

## Complete Installation Manifest

### Core Dependencies

```toml
# pyproject.toml or requirements.txt

# Async Processing
dramatiq[redis]==1.17.0  # Verify latest stable
redis==5.0.1
dramatiq-dashboard==0.13.1  # Optional: web UI for queue monitoring

# Document Processing
PyMuPDF==1.24.0  # Verify latest
pdf2image==1.17.0
python-docx==1.1.0
openpyxl==3.1.2
python-pptx==0.6.23  # If needed
Pillow==10.2.0
pytesseract==0.3.10  # OCR fallback

# Claude API
anthropic==0.21.0  # Verify latest - API evolving rapidly

# Prompt Management
Jinja2==3.1.3
pydantic==2.6.0

# Already in stack (confirm versions)
fastapi==0.109.0
sqlalchemy==2.0.25
psycopg2-binary==2.9.9
motor==3.3.2  # MongoDB async driver
google-cloud-storage==2.14.0

# Monitoring
sentry-sdk[fastapi]==1.40.0
structlog==24.1.0
```

### System Dependencies (for Render build)

```dockerfile
# Dockerfile additions for Render
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*
```

---

## Alternatives Considered & Rejected

### Job Queue Alternatives

| Alternative | Why Not |
|-------------|---------|
| **RQ (Redis Queue)** | Too simple - no retries, no priority, harder to monitor |
| **Huey** | Less mature, smaller community, fewer production examples |
| **AWS SQS** | Vendor lock-in, adds AWS complexity to GCP-based stack |
| **Google Cloud Tasks** | Requires GCP project changes, more expensive for low volume |
| **ARQ** | Async/await native but less mature than Dramatiq, fewer production patterns |

**Verdict:** Dramatiq hits sweet spot of simplicity and production-readiness for Render.

### PDF Processing Alternatives

| Alternative | Why Not |
|-------------|---------|
| **PDFMiner** | Slower than PyMuPDF, less maintained |
| **pdfplumber** | Good but heavier, built on pdfminer.six |
| **PyPDF2/PyPDF4** | Limited text extraction quality, struggles with complex PDFs |
| **Apache Tika** | JVM overhead, deployment complexity on Render |
| **Camelot** | Table extraction only, not general-purpose |

**Verdict:** PyMuPDF for speed + Claude Vision for quality = best hybrid approach.

### Prompt Management Alternatives

| Alternative | Why Not |
|-------------|---------|
| **Git-based files** | Hard to query, no runtime versioning, deployment friction |
| **LangChain Hub** | External dependency, overkill for simple templates, adds complexity |
| **PromptLayer/Helicone** | Paid services, vendor lock-in, not needed at 200/day scale |
| **MongoDB collections** | Already using PostgreSQL, adds query complexity |

**Verdict:** PostgreSQL schema gives full control with familiar tools.

---

## Architecture Pattern: Processing Pipeline

### Recommended Flow

```
1. Email Received (FastAPI endpoint)
   ↓
2. Create Job Record (PostgreSQL)
   ↓
3. Enqueue Processing Task (Dramatiq → Redis)
   ↓
4. Worker Processes:
   a. Download attachments from GCS
   b. Detect file types
   c. Extract text:
      - PDF: PyMuPDF → fallback Claude Vision
      - DOCX: python-docx
      - Images: Claude Vision
   d. Intent Detection (Claude API with cached prompt)
   e. Structured Data Extraction (Claude API)
   ↓
5. Save Results (PostgreSQL)
   ↓
6. Update Job Status
   ↓
7. Cleanup temp files
```

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| **FastAPI App** | API endpoints, job creation | PostgreSQL, Redis (enqueue) |
| **Dramatiq Workers** | Document processing, Claude API calls | Redis (dequeue), PostgreSQL, GCS, Claude API |
| **Prompt Manager** | Load/version prompts | PostgreSQL |
| **Document Extractor** | File → text conversion | GCS, local filesystem |
| **Intent Classifier** | Determine document type | Claude API, Prompt Manager |

---

## Cost Projections (200 emails/day)

### Infrastructure

| Service | Usage | Est. Monthly Cost |
|---------|-------|-------------------|
| Upstash Redis | ~250K commands/month | Free tier (sufficient) |
| Render (worker dyno) | 1x Standard instance | $25/month |
| GCS Storage | ~50GB (1 year retention) | ~$1/month |
| Claude API | ~600 docs/day × 3K tokens avg | ~$100-150/month (verify current pricing) |

**Total Est:** $126-176/month (excludes existing FastAPI hosting)

### Scaling Considerations

| Load | Architecture | Cost Impact |
|------|--------------|-------------|
| 200/day (current) | 1 worker, Upstash free | Baseline |
| 1000/day | 2-3 workers, Upstash paid tier | +$50/month |
| 5000/day | 5+ workers, Redis Cloud, CDN | +$200/month |

---

## Migration Path from Current System

### Phase 1: Add Queue Infrastructure
- Add Dramatiq + Redis to existing system
- Keep synchronous processing as fallback
- Test with 10% of traffic

### Phase 2: Multi-Format Extraction
- Add PyMuPDF, python-docx, pdf2image
- Build extraction pipeline
- Test with historical emails

### Phase 3: Intent Pipeline
- Implement database-backed prompts
- Add intent classification step
- A/B test against current system

### Phase 4: Full Migration
- Route all new emails through queue
- Monitor for 2 weeks
- Remove old synchronous code

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Dramatiq vs Celery | **HIGH** | Architecture pattern well-established as of training cutoff |
| Redis on Render | **MEDIUM** | Upstash recommendation sound, but verify 2026 Render integrations |
| PyMuPDF + Claude Vision | **HIGH** | Proven pattern for PDF extraction, within training data |
| python-docx/openpyxl | **HIGH** | Standard libraries, stable APIs |
| Prompt versioning schema | **HIGH** | Database pattern is production-proven |
| Version numbers | **LOW** | All versions need verification against current releases |
| Claude API limits | **MEDIUM** | API evolving rapidly, verify current rate limits and pricing |
| Cost projections | **LOW** | Pricing may have changed since training cutoff |

---

## Critical Verification Checklist

Before implementation, verify:

- [ ] Latest Dramatiq version and Redis compatibility
- [ ] Current Claude Vision API image size limits and pricing
- [ ] Upstash Redis free tier limits (verify 10K commands/day still available)
- [ ] PyMuPDF version compatible with current Python (3.11+)
- [ ] Anthropic Python SDK latest version (API evolving rapidly)
- [ ] Render's Redis hosting options (may have changed since 2025)
- [ ] Current Claude API rate limits for production tier

---

## Sources

**NOTE:** Due to unavailability of external verification tools during research, this document is based on training data current through January 2025. All recommendations should be verified against official documentation:

- Dramatiq: https://dramatiq.io/
- Celery: https://docs.celeryq.dev/
- Anthropic API: https://docs.anthropic.com/
- PyMuPDF: https://pymupdf.readthedocs.io/
- Render Deployment: https://render.com/docs
- Upstash Redis: https://upstash.com/

**Verification Status:** UNVERIFIED - External research tools unavailable. Treat as architectural guidance requiring validation.

---

## Next Steps for Roadmap

Based on this stack research, suggested milestone structure:

1. **Milestone 1:** Queue Infrastructure Setup
   - Add Dramatiq + Redis
   - Deploy worker process
   - Migrate one document type

2. **Milestone 2:** Multi-Format Extraction
   - PDF processing (PyMuPDF + Claude Vision)
   - DOCX/XLSX processing
   - Image processing pipeline

3. **Milestone 3:** Intent Pipeline
   - Database prompt repository
   - Intent classification
   - Dynamic prompt loading

4. **Milestone 4:** Production Hardening
   - Error handling & retries
   - Monitoring & alerting
   - Performance optimization

Each milestone builds on previous infrastructure, allowing incremental deployment and validation.
