# Architecture Patterns: Multi-Agent Email Processing Pipeline

**Domain:** AI-powered creditor email analysis with multi-agent document processing
**Researched:** 2026-02-04
**Confidence:** MEDIUM (based on existing codebase analysis + established patterns from training data)

## Executive Summary

This architecture research addresses 7 specific questions about building a multi-agent document processing pipeline for creditor email analysis:

1. **Agent orchestration** - Sequential pipeline using Celery chains with database state handoff
2. **Job queue architecture** - Single queue with task chaining for 200/day scale, split queues only at 1000+/day
3. **Attachment processing** - Parallel group tasks with type-specific handlers (PDF, DOCX, XLSX, images)
4. **Prompt repository** - PostgreSQL table with versioning, intent-based selection, runtime caching
5. **Confidence scoring** - Weighted aggregation across sources with per-field + overall thresholds
6. **Error handling** - Exponential backoff with retries per error type, circuit breakers on external services
7. **GCS integration** - Temp bucket with 7-day lifecycle for attachment staging, signed URLs for access

The recommended architecture builds on the existing v1 FastAPI app by adding Celery workers for async processing, maintaining backward compatibility with the MongoDB schema used by the Node.js portal.

---

## 1. Agent Orchestration Patterns

### Question: How do Agent 1 (Intent/Classification) → Agent 2 (Extraction) → Agent 3 (Consolidation) communicate?

### Recommendation: Database State Handoff with Celery Chains

**Pattern:** Agents communicate through PostgreSQL state + Celery task chaining, NOT direct function calls or message passing.

```
Agent 1 completes → Writes result to PostgreSQL → Returns agent1_result
                                                           ↓
                                    Celery chain triggers Agent 2 with agent1_result
                                                           ↓
Agent 2 completes → Writes result to PostgreSQL → Returns agent2_result
                                                           ↓
                                    Celery chain triggers Agent 3 with agent2_result
                                                           ↓
Agent 3 completes → Writes final result to PostgreSQL + MongoDB
```

### Implementation

**PostgreSQL Schema for State Management:**
```sql
CREATE TABLE processing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id INTEGER NOT NULL REFERENCES incoming_emails(id),

    -- State machine
    status VARCHAR(50) NOT NULL, -- received, classifying, extracting, consolidating, completed, failed
    current_agent VARCHAR(50),   -- agent_1_intake, agent_2_extract, agent_3_consolidate

    -- Agent outputs (JSON)
    agent1_result JSONB,  -- { intent: "debt_statement", prompt_id: 42, gcs_paths: [...] }
    agent2_result JSONB,  -- [ { source: "email_body", data: {...}, confidence: 0.88 }, {...} ]
    agent3_result JSONB,  -- { consolidated: {...}, field_confidences: {...}, overall: 0.92 }

    -- Tracking
    error_details JSONB,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,

    -- Constraints
    CONSTRAINT valid_status CHECK (status IN (
        'received', 'classifying', 'extracting', 'consolidating',
        'completed', 'review_required', 'failed'
    ))
);

CREATE INDEX idx_jobs_status ON processing_jobs(status, created_at);
CREATE INDEX idx_jobs_email ON processing_jobs(email_id);
```

**Celery Task Chain Definition:**
```python
# app/tasks/pipeline.py
from celery import chain
from app.celery import app
from app.database import SessionLocal
from app.models import ProcessingJob
import logging

logger = logging.getLogger(__name__)

@app.task(bind=True, max_retries=3, default_retry_delay=30)
def agent_1_intake(self, email_id: int):
    """
    Agent 1: Intent classification and attachment download

    Returns:
        dict: {
            'email_id': int,
            'job_id': str,
            'intent': str,
            'prompt_id': int,
            'gcs_paths': [str],
            'confidence': float
        }
    """
    db = SessionLocal()
    try:
        # Create or get processing job
        job = ProcessingJob(email_id=email_id, status='classifying', current_agent='agent_1_intake')
        db.add(job)
        db.commit()
        db.refresh(job)

        logger.info(f"Agent 1 starting - Job: {job.id}, Email: {email_id}")

        # Load email
        from app.models import IncomingEmail
        email = db.query(IncomingEmail).filter_by(id=email_id).first()
        if not email:
            raise ValueError(f"Email {email_id} not found")

        # Parse email body (reuse existing email_parser)
        from app.services.email_parser import email_parser
        parsed = email_parser.parse_email(
            html_body=email.raw_body_html,
            text_body=email.raw_body_text
        )

        # Classify intent using Claude
        from app.services.claude_client import classify_intent
        intent_result = classify_intent(
            email_body=parsed['cleaned_body'],
            subject=email.subject,
            from_email=email.from_email
        )

        # Download attachments from Zendesk webhook data
        gcs_paths = []
        if email.zendesk_attachment_urls:  # Assuming this field exists in updated schema
            from app.services.attachment_downloader import download_attachments
            gcs_paths = download_attachments(
                email_id=email_id,
                attachment_urls=email.zendesk_attachment_urls
            )

        # Get prompt for intent
        from app.services.prompt_manager import get_prompt_for_intent
        prompt = get_prompt_for_intent(intent_result['intent'])

        # Store Agent 1 result
        agent1_result = {
            'intent': intent_result['intent'],
            'intent_confidence': intent_result['confidence'],
            'prompt_id': prompt['id'],
            'gcs_paths': gcs_paths,
            'parsed_body': parsed['cleaned_body'],
            'token_count': parsed['token_count_after']
        }

        job.agent1_result = agent1_result
        job.status = 'extracting'
        db.commit()

        logger.info(f"Agent 1 complete - Intent: {intent_result['intent']}, Attachments: {len(gcs_paths)}")

        return {
            'email_id': email_id,
            'job_id': str(job.id),
            **agent1_result
        }

    except Exception as exc:
        logger.error(f"Agent 1 failed - Email: {email_id}: {exc}", exc_info=True)
        if db:
            job = db.query(ProcessingJob).filter_by(email_id=email_id).first()
            if job:
                job.status = 'failed'
                job.error_details = {'agent': 'agent_1', 'error': str(exc)}
                job.retry_count += 1
                db.commit()
        raise self.retry(exc=exc, countdown=30 ** self.request.retries)  # Exponential backoff
    finally:
        db.close()


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def agent_2_extract(self, agent1_output: dict):
    """
    Agent 2: Multi-source extraction from email body + attachments

    Args:
        agent1_output: Output from Agent 1

    Returns:
        dict: {
            'email_id': int,
            'job_id': str,
            'extractions': [
                {'source': 'email_body', 'data': {...}, 'confidence': 0.88},
                {'source': 'invoice.pdf', 'data': {...}, 'confidence': 0.95},
                ...
            ]
        }
    """
    db = SessionLocal()
    email_id = agent1_output['email_id']
    job_id = agent1_output['job_id']

    try:
        job = db.query(ProcessingJob).filter_by(id=job_id).first()
        job.current_agent = 'agent_2_extract'
        db.commit()

        logger.info(f"Agent 2 starting - Job: {job_id}")

        extractions = []

        # Extract from email body
        from app.extractors.email_body import extract_from_body
        body_extraction = extract_from_body(
            email_body=agent1_output['parsed_body'],
            prompt_id=agent1_output['prompt_id']
        )
        extractions.append(body_extraction)

        # Extract from attachments in parallel (if any)
        if agent1_output['gcs_paths']:
            from celery import group
            from app.tasks.extraction_tasks import (
                extract_from_pdf, extract_from_docx,
                extract_from_xlsx, extract_from_image
            )

            attachment_tasks = []
            for gcs_path in agent1_output['gcs_paths']:
                filename = gcs_path.split('/')[-1].lower()

                if filename.endswith('.pdf'):
                    attachment_tasks.append(extract_from_pdf.s(email_id, gcs_path, agent1_output['prompt_id']))
                elif filename.endswith('.docx'):
                    attachment_tasks.append(extract_from_docx.s(email_id, gcs_path, agent1_output['prompt_id']))
                elif filename.endswith('.xlsx'):
                    attachment_tasks.append(extract_from_xlsx.s(email_id, gcs_path, agent1_output['prompt_id']))
                elif filename.endswith(('.jpg', '.jpeg', '.png')):
                    attachment_tasks.append(extract_from_image.s(email_id, gcs_path, agent1_output['prompt_id']))

            # Execute in parallel, wait for all to complete
            if attachment_tasks:
                job_group = group(attachment_tasks)
                attachment_results = job_group.apply_async().get(timeout=120)  # 2 min timeout
                extractions.extend(attachment_results)

        # Store Agent 2 result
        agent2_result = {
            'extractions': extractions,
            'source_count': len(extractions)
        }

        job.agent2_result = agent2_result
        job.status = 'consolidating'
        db.commit()

        logger.info(f"Agent 2 complete - Sources: {len(extractions)}")

        return {
            'email_id': email_id,
            'job_id': job_id,
            **agent2_result
        }

    except Exception as exc:
        logger.error(f"Agent 2 failed - Job: {job_id}: {exc}", exc_info=True)
        if db:
            job = db.query(ProcessingJob).filter_by(id=job_id).first()
            if job:
                job.status = 'failed'
                job.error_details = {'agent': 'agent_2', 'error': str(exc)}
                job.retry_count += 1
                db.commit()
        raise self.retry(exc=exc, countdown=60 ** self.request.retries)
    finally:
        db.close()


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def agent_3_consolidate(self, agent2_output: dict):
    """
    Agent 3: Consolidate extractions, resolve conflicts, route based on confidence

    Args:
        agent2_output: Output from Agent 2

    Returns:
        dict: Final processing result
    """
    db = SessionLocal()
    email_id = agent2_output['email_id']
    job_id = agent2_output['job_id']

    try:
        job = db.query(ProcessingJob).filter_by(id=job_id).first()
        job.current_agent = 'agent_3_consolidate'
        db.commit()

        logger.info(f"Agent 3 starting - Job: {job_id}")

        # Consolidate all extractions
        from app.agents.consolidator import consolidate_extractions
        consolidated = consolidate_extractions(agent2_output['extractions'])

        # Calculate confidence scores
        from app.agents.consolidator import calculate_confidence
        field_confidences = calculate_confidence(agent2_output['extractions'])
        overall_confidence = calculate_overall_confidence(field_confidences)

        # Match to client/creditor
        from app.services.matching_engine import MatchingEngine
        matching_engine = MatchingEngine()
        match_result = matching_engine.find_best_match(
            client_name=consolidated.get('client_name'),
            creditor_name=consolidated.get('creditor_name'),
            creditor_email=consolidated.get('creditor_email'),
            reference_numbers=consolidated.get('reference_numbers', [])
        )

        # Route based on confidence
        if overall_confidence >= 0.80 and match_result and match_result.confidence >= 0.80:
            # Auto-update MongoDB
            from app.services.mongodb_client import mongodb_service
            mongodb_service.update_creditor_debt_amount(
                client_name=consolidated['client_name'],
                client_aktenzeichen=match_result.client_aktenzeichen,
                creditor_email=consolidated.get('creditor_email'),
                creditor_name=consolidated['creditor_name'],
                new_debt_amount=consolidated['debt_amount'],
                response_text=consolidated.get('summary'),
                reference_numbers=consolidated.get('reference_numbers', [])
            )
            job.status = 'completed'
            route = 'auto_updated'
        elif overall_confidence >= 0.60:
            job.status = 'review_required'
            route = 'manual_review'
        else:
            job.status = 'failed'
            route = 'insufficient_confidence'

        # Store Agent 3 result
        agent3_result = {
            'consolidated': consolidated,
            'field_confidences': field_confidences,
            'overall_confidence': overall_confidence,
            'match_result': match_result.model_dump() if match_result else None,
            'route': route
        }

        job.agent3_result = agent3_result
        job.completed_at = datetime.utcnow()
        db.commit()

        logger.info(f"Agent 3 complete - Confidence: {overall_confidence:.2f}, Route: {route}")

        return {
            'email_id': email_id,
            'job_id': job_id,
            **agent3_result
        }

    except Exception as exc:
        logger.error(f"Agent 3 failed - Job: {job_id}: {exc}", exc_info=True)
        if db:
            job = db.query(ProcessingJob).filter_by(id=job_id).first()
            if job:
                job.status = 'failed'
                job.error_details = {'agent': 'agent_3', 'error': str(exc)}
                job.retry_count += 1
                db.commit()
        raise self.retry(exc=exc, countdown=30 ** self.request.retries)
    finally:
        db.close()


# Pipeline orchestration
def process_email_pipeline(email_id: int):
    """
    Orchestrate the full 3-agent pipeline
    """
    pipeline = chain(
        agent_1_intake.s(email_id),
        agent_2_extract.s(),
        agent_3_consolidate.s()
    )
    return pipeline.apply_async()
```

**Why Database State Handoff:**
1. **Resilience** - If worker crashes between agents, state is preserved in PostgreSQL
2. **Debuggability** - Can inspect `processing_jobs` table to see exactly where processing stopped
3. **Auditing** - Full history of agent outputs for compliance/debugging
4. **Decoupling** - Agents don't need to know about each other, only their input/output contracts

**Alternative Considered:** Direct message passing via Celery result backend
- **Rejected because:** Result backend (Redis) is ephemeral, loses data on restart. PostgreSQL is durable source of truth.

---

## 2. Job Queue Architecture

### Question: Celery worker topology for multi-stage pipeline - single queue vs multiple queues?

### Recommendation: Single Queue for Current Scale (200/day), Plan for Split at 1000+/day

**For 200 emails/day:**

```python
# Single queue configuration
CELERY_TASK_ROUTES = {
    '*': {'queue': 'celery_default'}  # All tasks to one queue
}

# Worker configuration
# celery -A app.celery worker --loglevel=info --concurrency=4
```

**Why single queue:**
- **Scale appropriate** - 200/day = 8/hour avg, peak ~30/hour. Single worker handles this easily.
- **Simpler ops** - One worker process to monitor, deploy, debug
- **Resource efficient** - Task chaining means agents run sequentially per email anyway
- **Render constraints** - Render Background Worker runs one process; splitting queues requires multiple worker services ($$$)

**Queue depth math:**
```
Avg processing time: 10-30 seconds per email (Agent 1: 2s, Agent 2: 5-15s, Agent 3: 3-10s)
Concurrency: 4 workers
Throughput: 4 emails × (60s / 30s) = 8 emails/minute = 480/hour

200 emails/day ÷ 24 hours = 8.3/hour avg
Peak (2× avg) = 16.6/hour

Queue depth during peak: 16.6/hour ÷ 480/hour = 0.03 (negligible)
```

**When to split queues (at 1000+/day):**

```python
# app/celery.py - Queue routing for high volume
CELERY_TASK_ROUTES = {
    'app.tasks.pipeline.agent_1_intake': {'queue': 'intake_queue'},
    'app.tasks.pipeline.agent_2_extract': {'queue': 'extraction_queue'},
    'app.tasks.pipeline.agent_3_consolidate': {'queue': 'consolidation_queue'},
    'app.tasks.extraction_tasks.*': {'queue': 'extraction_queue'},  # Attachment processing
}

# Worker topology (3 separate Render workers)
# Worker 1 (intake): celery -A app.celery worker -Q intake_queue --concurrency=4
# Worker 2 (extraction): celery -A app.celery worker -Q extraction_queue --concurrency=2
# Worker 3 (consolidation): celery -A app.celery worker -Q consolidation_queue --concurrency=2
```

**Benefits of split queues (only at scale):**
- **Isolation** - Slow Claude API calls in Agent 2 don't block fast Agent 1
- **Prioritization** - Can prioritize intake over extraction (respond to webhooks faster)
- **Horizontal scaling** - Add extraction workers independently

**Monitoring trigger to split:**
- Queue depth consistently > 50
- p95 processing time > 60 seconds
- Worker CPU consistently > 70%

### Celery Configuration for Single Queue

```python
# app/celery.py
from celery import Celery
from app.config import settings

app = Celery('creditor_email_matcher')

app.conf.update(
    broker_url=settings.redis_url,  # e.g., redis://redis:6379/0
    result_backend=settings.redis_url,
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Berlin',
    enable_utc=True,

    # Single queue config
    task_default_queue='celery_default',
    task_default_exchange='celery_default',
    task_default_routing_key='celery_default',

    # Task execution
    task_acks_late=True,  # ACK after task completes (ensures retries on crash)
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    worker_prefetch_multiplier=1,  # One task per worker at a time (prevents OOM)

    # Timeouts
    task_soft_time_limit=300,  # 5 minutes soft limit (raises exception)
    task_time_limit=360,  # 6 minutes hard limit (kills worker)

    # Result backend
    result_expires=3600,  # Results expire after 1 hour (not needed long-term, PostgreSQL has state)

    # Retries
    task_annotations={
        '*': {
            'max_retries': 3,
            'default_retry_delay': 30,
            'autoretry_for': (Exception,),
            'retry_backoff': True,
            'retry_backoff_max': 600,
            'retry_jitter': True
        }
    }
)

# Auto-discover tasks
app.autodiscover_tasks(['app.tasks'])
```

**Render Deployment:**
```yaml
# render.yaml
services:
  - type: web
    name: creditor-email-api
    env: python
    buildCommand: "pip install -r requirements.txt"
    startCommand: "gunicorn app.main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000"

  - type: worker
    name: creditor-email-worker
    env: python
    buildCommand: "pip install -r requirements.txt"
    startCommand: "celery -A app.celery worker --loglevel=info --concurrency=4"
    envVars:
      - key: REDIS_URL
        sync: false  # Set in Render dashboard
```

---

## 3. Attachment Processing Pipeline

### Question: How to handle mixed file types (PDF, DOCX, XLSX, images) efficiently?

### Recommendation: Type-Specific Extractors with Parallel Group Execution

**Architecture:**

```
Agent 2: Extract entities from body + attachments
    │
    ├─> Email Body Extractor (always runs, sequential)
    │   └─> Claude Messages API with text prompt
    │
    └─> Attachment Extractors (parallel group, only if attachments exist)
        │
        ├─> PDF Extractor
        │   ├─> Claude PDF API (native PDF support) - PRIMARY
        │   └─> Fallback: PyMuPDF text extraction → Claude Messages API
        │
        ├─> DOCX Extractor
        │   ├─> python-docx → extract text + tables
        │   └─> Claude Messages API with extracted content
        │
        ├─> XLSX Extractor
        │   ├─> openpyxl → parse sheets, convert to structured JSON
        │   └─> Claude Messages API with JSON data
        │
        └─> Image Extractor
            └─> Claude Vision API with image + prompt
```

**Implementation:**

```python
# app/tasks/extraction_tasks.py
from celery import group
from app.celery import app
from app.services.gcs_client import download_from_gcs
from app.services.claude_client import call_claude_api
import logging

logger = logging.getLogger(__name__)

@app.task(bind=True, max_retries=2, default_retry_delay=30)
def extract_from_pdf(self, email_id: int, gcs_path: str, prompt_id: int):
    """
    Extract structured data from PDF attachment

    Strategy:
    1. Try Claude PDF API (native PDF support) - PREFERRED
    2. Fallback: PyMuPDF text extraction → Claude Messages API
    """
    try:
        logger.info(f"Extracting from PDF: {gcs_path}")

        # Download PDF from GCS
        pdf_bytes = download_from_gcs(gcs_path)

        # Get prompt
        from app.services.prompt_manager import get_prompt_by_id
        prompt = get_prompt_by_id(prompt_id)

        # Try Claude PDF API (native support, no text extraction needed)
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=settings.anthropic_api_key)

            # Upload PDF as base64
            import base64
            pdf_base64 = base64.standard_b64encode(pdf_bytes).decode('utf-8')

            message = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": pdf_base64
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt['prompt_text']
                            }
                        ]
                    }
                ]
            )

            result_text = message.content[0].text
            extracted_data = json.loads(result_text)

            return {
                'source': gcs_path.split('/')[-1],
                'source_type': 'pdf',
                'method': 'claude_pdf_native',
                'data': extracted_data,
                'confidence': extracted_data.get('confidence', 0.8),
                'token_count': message.usage.input_tokens + message.usage.output_tokens
            }

        except Exception as pdf_api_error:
            logger.warning(f"Claude PDF API failed, falling back to text extraction: {pdf_api_error}")

            # Fallback: Extract text with PyMuPDF
            import fitz  # PyMuPDF
            import io

            pdf_doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
            text_content = ""
            for page in pdf_doc:
                text_content += page.get_text()
            pdf_doc.close()

            # Call Claude with extracted text
            message = call_claude_api(
                prompt=prompt['prompt_text'],
                content=text_content,
                system_prompt=prompt.get('system_prompt')
            )

            extracted_data = json.loads(message.content[0].text)

            return {
                'source': gcs_path.split('/')[-1],
                'source_type': 'pdf',
                'method': 'pymupdf_text_extraction',
                'data': extracted_data,
                'confidence': extracted_data.get('confidence', 0.75),  # Lower confidence for OCR
                'token_count': len(text_content) // 4  # Rough estimate
            }

    except Exception as exc:
        logger.error(f"PDF extraction failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def extract_from_docx(self, email_id: int, gcs_path: str, prompt_id: int):
    """
    Extract structured data from DOCX attachment
    """
    try:
        logger.info(f"Extracting from DOCX: {gcs_path}")

        # Download DOCX from GCS
        docx_bytes = download_from_gcs(gcs_path)

        # Extract text and tables with python-docx
        from docx import Document
        import io

        doc = Document(io.BytesIO(docx_bytes))

        # Extract paragraphs
        text_content = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

        # Extract tables (if any)
        tables_content = []
        for table in doc.tables:
            table_data = []
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                table_data.append(row_data)
            tables_content.append(table_data)

        # Combine text + tables
        full_content = text_content
        if tables_content:
            full_content += "\n\nTables:\n" + str(tables_content)

        # Get prompt and call Claude
        from app.services.prompt_manager import get_prompt_by_id
        prompt = get_prompt_by_id(prompt_id)

        message = call_claude_api(
            prompt=prompt['prompt_text'],
            content=full_content,
            system_prompt=prompt.get('system_prompt')
        )

        extracted_data = json.loads(message.content[0].text)

        return {
            'source': gcs_path.split('/')[-1],
            'source_type': 'docx',
            'method': 'python_docx',
            'data': extracted_data,
            'confidence': extracted_data.get('confidence', 0.85),
            'token_count': len(full_content) // 4
        }

    except Exception as exc:
        logger.error(f"DOCX extraction failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def extract_from_xlsx(self, email_id: int, gcs_path: str, prompt_id: int):
    """
    Extract structured data from XLSX attachment
    """
    try:
        logger.info(f"Extracting from XLSX: {gcs_path}")

        # Download XLSX from GCS
        xlsx_bytes = download_from_gcs(gcs_path)

        # Parse with openpyxl
        from openpyxl import load_workbook
        import io

        wb = load_workbook(io.BytesIO(xlsx_bytes), data_only=True)

        # Convert sheets to JSON structure
        sheets_data = {}
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_data = []
            for row in ws.iter_rows(values_only=True):
                sheet_data.append([str(cell) if cell is not None else "" for cell in row])
            sheets_data[sheet_name] = sheet_data

        # Convert to formatted text for Claude
        formatted_content = json.dumps(sheets_data, indent=2, ensure_ascii=False)

        # Get prompt and call Claude
        from app.services.prompt_manager import get_prompt_by_id
        prompt = get_prompt_by_id(prompt_id)

        # Add context about spreadsheet structure
        prompt_with_context = f"{prompt['prompt_text']}\n\nSpreadsheet data (JSON format):\n{formatted_content}"

        message = call_claude_api(
            prompt=prompt_with_context,
            content="",
            system_prompt=prompt.get('system_prompt')
        )

        extracted_data = json.loads(message.content[0].text)

        return {
            'source': gcs_path.split('/')[-1],
            'source_type': 'xlsx',
            'method': 'openpyxl',
            'data': extracted_data,
            'confidence': extracted_data.get('confidence', 0.80),  # Spreadsheets can be ambiguous
            'token_count': len(formatted_content) // 4
        }

    except Exception as exc:
        logger.error(f"XLSX extraction failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def extract_from_image(self, email_id: int, gcs_path: str, prompt_id: int):
    """
    Extract structured data from image attachment using Claude Vision
    """
    try:
        logger.info(f"Extracting from image: {gcs_path}")

        # Download image from GCS
        image_bytes = download_from_gcs(gcs_path)

        # Detect image type
        import imghdr
        import io
        image_type = imghdr.what(io.BytesIO(image_bytes))
        media_type_map = {
            'jpeg': 'image/jpeg',
            'jpg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        media_type = media_type_map.get(image_type, 'image/jpeg')

        # Get prompt
        from app.services.prompt_manager import get_prompt_by_id
        prompt = get_prompt_by_id(prompt_id)

        # Call Claude Vision API
        from anthropic import Anthropic
        import base64

        client = Anthropic(api_key=settings.anthropic_api_key)
        image_base64 = base64.standard_b64encode(image_bytes).decode('utf-8')

        message = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt['prompt_text']
                        }
                    ]
                }
            ]
        )

        extracted_data = json.loads(message.content[0].text)

        return {
            'source': gcs_path.split('/')[-1],
            'source_type': 'image',
            'method': 'claude_vision',
            'data': extracted_data,
            'confidence': extracted_data.get('confidence', 0.70),  # OCR can be unreliable
            'token_count': message.usage.input_tokens + message.usage.output_tokens
        }

    except Exception as exc:
        logger.error(f"Image extraction failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)
```

**Parallel Execution:**

```python
# In agent_2_extract task (from section 1)
if agent1_output['gcs_paths']:
    from celery import group

    attachment_tasks = []
    for gcs_path in agent1_output['gcs_paths']:
        filename = gcs_path.split('/')[-1].lower()

        if filename.endswith('.pdf'):
            attachment_tasks.append(extract_from_pdf.s(email_id, gcs_path, prompt_id))
        elif filename.endswith('.docx'):
            attachment_tasks.append(extract_from_docx.s(email_id, gcs_path, prompt_id))
        elif filename.endswith('.xlsx'):
            attachment_tasks.append(extract_from_xlsx.s(email_id, gcs_path, prompt_id))
        elif filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
            attachment_tasks.append(extract_from_image.s(email_id, gcs_path, prompt_id))

    # Execute all attachment extractions in parallel
    if attachment_tasks:
        job_group = group(attachment_tasks)
        attachment_results = job_group.apply_async().get(timeout=120)
        extractions.extend(attachment_results)
```

**Performance:**
- **Sequential:** 3 attachments × 10s each = 30 seconds total
- **Parallel:** max(10s, 10s, 10s) = 10 seconds total (3× speedup)

**Error handling:**
- If one attachment extraction fails, others continue
- Failed attachment marked in result with partial data
- Agent 3 consolidates available data, notes missing source

---

## 4. Prompt Repository Architecture

### Question: How to store/version/select prompts dynamically from database?

### Recommendation: PostgreSQL Table with Versioning + Runtime Caching

**Schema:**

```sql
CREATE TABLE prompts (
    id SERIAL PRIMARY KEY,

    -- Prompt identification
    intent_type VARCHAR(50) NOT NULL,  -- debt_statement, payment_plan, rejection, etc.
    version INTEGER NOT NULL,          -- Incremental version number

    -- Prompt content
    prompt_text TEXT NOT NULL,         -- User message template
    system_prompt TEXT,                -- System message (optional)

    -- Metadata
    is_active BOOLEAN DEFAULT true,    -- Only one version active per intent at a time
    created_at TIMESTAMP DEFAULT NOW(),
    created_by VARCHAR(100),           -- Who created this version
    notes TEXT,                        -- Change notes, A/B test results, etc.

    -- Constraints
    UNIQUE(intent_type, version),
    CHECK(intent_type IN (
        'debt_statement', 'payment_plan', 'rejection',
        'inquiry', 'auto_reply', 'spam', 'unknown'
    ))
);

CREATE INDEX idx_prompts_intent_active ON prompts(intent_type, is_active, version DESC);

-- Example data
INSERT INTO prompts (intent_type, version, prompt_text, system_prompt, is_active, created_by, notes) VALUES
('debt_statement', 1,
 'Extrahiere aus dieser Gläubiger-Antwort: client_name, creditor_name, debt_amount, reference_numbers.',
 'Du bist ein Experten-Assistent für Schuldnerberatung...',
 true,
 'system',
 'Initial prompt based on v1 hardcoded version'
);
```

**Prompt Manager Service:**

```python
# app/services/prompt_manager.py
from sqlalchemy.orm import Session
from app.models import Prompt
from app.database import SessionLocal
from functools import lru_cache
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class PromptManager:
    """
    Manages prompt templates with versioning and caching
    """

    def __init__(self):
        self._cache = {}
        self._cache_ttl = timedelta(minutes=10)  # Cache prompts for 10 minutes
        self._last_cache_clear = datetime.utcnow()

    def get_prompt_for_intent(self, intent: str, db: Session = None) -> dict:
        """
        Get the active prompt template for an intent type

        Args:
            intent: Intent type (debt_statement, payment_plan, etc.)
            db: Database session (optional, creates new if not provided)

        Returns:
            dict with keys: id, prompt_text, system_prompt, version
        """
        # Check cache first
        cache_key = f"intent:{intent}"
        if cache_key in self._cache:
            cached_prompt, cached_at = self._cache[cache_key]
            if datetime.utcnow() - cached_at < self._cache_ttl:
                logger.debug(f"Prompt cache hit for intent: {intent}")
                return cached_prompt

        # Cache miss, query database
        db_owned = db is None
        if db_owned:
            db = SessionLocal()

        try:
            prompt = db.query(Prompt).filter(
                Prompt.intent_type == intent,
                Prompt.is_active == True
            ).order_by(Prompt.version.desc()).first()

            if not prompt:
                logger.warning(f"No active prompt found for intent: {intent}, using fallback")
                return self._get_fallback_prompt(intent)

            result = {
                'id': prompt.id,
                'prompt_text': prompt.prompt_text,
                'system_prompt': prompt.system_prompt,
                'version': prompt.version,
                'intent_type': prompt.intent_type
            }

            # Cache result
            self._cache[cache_key] = (result, datetime.utcnow())

            logger.info(f"Loaded prompt for intent {intent}, version {prompt.version}")
            return result

        finally:
            if db_owned:
                db.close()

    def get_prompt_by_id(self, prompt_id: int, db: Session = None) -> dict:
        """
        Get a specific prompt by ID (for Agent 2 to use exact prompt from Agent 1)
        """
        cache_key = f"id:{prompt_id}"
        if cache_key in self._cache:
            cached_prompt, cached_at = self._cache[cache_key]
            if datetime.utcnow() - cached_at < self._cache_ttl:
                return cached_prompt

        db_owned = db is None
        if db_owned:
            db = SessionLocal()

        try:
            prompt = db.query(Prompt).filter(Prompt.id == prompt_id).first()

            if not prompt:
                raise ValueError(f"Prompt ID {prompt_id} not found")

            result = {
                'id': prompt.id,
                'prompt_text': prompt.prompt_text,
                'system_prompt': prompt.system_prompt,
                'version': prompt.version,
                'intent_type': prompt.intent_type
            }

            self._cache[cache_key] = (result, datetime.utcnow())
            return result

        finally:
            if db_owned:
                db.close()

    def create_new_version(
        self,
        intent: str,
        prompt_text: str,
        system_prompt: str = None,
        created_by: str = None,
        notes: str = None,
        activate: bool = False,
        db: Session = None
    ) -> Prompt:
        """
        Create a new version of a prompt

        Args:
            intent: Intent type
            prompt_text: New prompt template
            system_prompt: System message (optional)
            created_by: User/system creating this version
            notes: Change notes
            activate: If True, deactivate old version and activate this one

        Returns:
            Created Prompt object
        """
        db_owned = db is None
        if db_owned:
            db = SessionLocal()

        try:
            # Get current max version
            max_version = db.query(Prompt.version).filter(
                Prompt.intent_type == intent
            ).order_by(Prompt.version.desc()).first()

            new_version = (max_version[0] + 1) if max_version else 1

            # Create new prompt
            new_prompt = Prompt(
                intent_type=intent,
                version=new_version,
                prompt_text=prompt_text,
                system_prompt=system_prompt,
                is_active=activate,
                created_by=created_by,
                notes=notes
            )

            db.add(new_prompt)

            # If activating, deactivate old versions
            if activate:
                db.query(Prompt).filter(
                    Prompt.intent_type == intent,
                    Prompt.id != new_prompt.id
                ).update({'is_active': False})

            db.commit()
            db.refresh(new_prompt)

            # Invalidate cache
            self._invalidate_cache(intent)

            logger.info(f"Created prompt version {new_version} for intent {intent}, active={activate}")
            return new_prompt

        finally:
            if db_owned:
                db.close()

    def activate_version(self, prompt_id: int, db: Session = None):
        """
        Activate a specific prompt version (deactivates others for same intent)
        """
        db_owned = db is None
        if db_owned:
            db = SessionLocal()

        try:
            prompt = db.query(Prompt).filter(Prompt.id == prompt_id).first()
            if not prompt:
                raise ValueError(f"Prompt ID {prompt_id} not found")

            # Deactivate all other versions for this intent
            db.query(Prompt).filter(
                Prompt.intent_type == prompt.intent_type,
                Prompt.id != prompt_id
            ).update({'is_active': False})

            # Activate this version
            prompt.is_active = True
            db.commit()

            # Invalidate cache
            self._invalidate_cache(prompt.intent_type)

            logger.info(f"Activated prompt version {prompt.version} for intent {prompt.intent_type}")

        finally:
            if db_owned:
                db.close()

    def _invalidate_cache(self, intent: str):
        """Invalidate cached prompts for an intent"""
        cache_key = f"intent:{intent}"
        if cache_key in self._cache:
            del self._cache[cache_key]
        logger.debug(f"Cache invalidated for intent: {intent}")

    def _get_fallback_prompt(self, intent: str) -> dict:
        """
        Fallback prompts if database is unavailable
        """
        FALLBACK_PROMPTS = {
            'debt_statement': {
                'id': -1,
                'prompt_text': 'Extrahiere: client_name, creditor_name, debt_amount, reference_numbers aus dieser Email.',
                'system_prompt': 'Du bist ein Assistent für Schuldnerberatung.',
                'version': 0,
                'intent_type': 'debt_statement'
            },
            # ... other fallback prompts
        }

        return FALLBACK_PROMPTS.get(intent, FALLBACK_PROMPTS['debt_statement'])

# Global instance
prompt_manager = PromptManager()
```

**Migration from Hardcoded Prompts:**

```python
# alembic/versions/20260204_seed_prompts.py
"""Seed initial prompts from v1 hardcoded values"""

from alembic import op
import sqlalchemy as sa

def upgrade():
    # Create prompts table (already done in schema migration)

    # Insert v1 prompts
    op.execute("""
        INSERT INTO prompts (intent_type, version, prompt_text, system_prompt, is_active, created_by, notes)
        VALUES
        ('debt_statement', 1,
         'Bitte extrahiere Informationen aus dieser E-Mail:\n**Von**: {from_email}\n**Betreff**: {subject}\n\n**E-Mail Inhalt**:\n{email_body}\n\nGib die Antwort als JSON zurück',
         'Du bist ein Experten-Assistent für eine deutsche Rechtsanwaltskanzlei, die sich auf Schuldnerberatung spezialisiert hat...',
         true,
         'migration_from_v1',
         'Initial prompt from entity_extractor_claude.py hardcoded version'
        );
    """)

def downgrade():
    op.execute("DELETE FROM prompts WHERE created_by = 'migration_from_v1'")
```

**Usage in Agents:**

```python
# In agent_1_intake
from app.services.prompt_manager import prompt_manager

intent_result = classify_intent(email_body, subject, from_email)
prompt = prompt_manager.get_prompt_for_intent(intent_result['intent'])

# Pass prompt_id to Agent 2 via agent1_result
agent1_result = {
    'intent': intent_result['intent'],
    'prompt_id': prompt['id'],  # Agent 2 will use this exact prompt
    # ...
}

# In agent_2_extract
from app.services.prompt_manager import prompt_manager

prompt = prompt_manager.get_prompt_by_id(agent1_output['prompt_id'])
# Use prompt['prompt_text'] and prompt['system_prompt'] for Claude API calls
```

**Prompt Versioning Workflow:**

1. **Create new version** (via admin API or SQL)
   ```sql
   INSERT INTO prompts (intent_type, version, prompt_text, is_active, created_by, notes)
   VALUES ('debt_statement', 2, 'New improved prompt...', false, 'admin@company.de', 'Testing better extraction for amounts');
   ```

2. **A/B test** (run both v1 and v2, compare results)
   - Keep v1 active, manually test v2 by activating temporarily

3. **Activate new version** (when confident)
   ```sql
   UPDATE prompts SET is_active = false WHERE intent_type = 'debt_statement' AND version = 1;
   UPDATE prompts SET is_active = true WHERE intent_type = 'debt_statement' AND version = 2;
   ```

4. **Rollback if needed**
   ```sql
   UPDATE prompts SET is_active = false WHERE intent_type = 'debt_statement' AND version = 2;
   UPDATE prompts SET is_active = true WHERE intent_type = 'debt_statement' AND version = 1;
   ```

---

## 5. Confidence Scoring Architecture

### Question: How to aggregate scores from multiple extraction sources?

### Recommendation: Weighted Average with Per-Field + Overall Thresholds

**Confidence Calculation Algorithm:**

```python
# app/agents/consolidator.py
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

# Source reliability weights
SOURCE_WEIGHTS = {
    'pdf': 0.40,        # PDFs are usually official documents
    'docx': 0.40,       # Word docs are structured
    'email_body': 0.30, # Email text can be informal/incomplete
    'xlsx': 0.25,       # Spreadsheets may need interpretation
    'image': 0.20       # OCR can have errors
}

# Required fields with individual thresholds
FIELD_THRESHOLDS = {
    'client_name': 0.70,       # Must be confident in client identification
    'creditor_name': 0.70,     # Must be confident in creditor identification
    'debt_amount': 0.85,       # CRITICAL - higher threshold
    'reference_numbers': 0.60  # Nice to have, lower threshold OK
}

REQUIRED_FIELDS = ['client_name', 'creditor_name', 'debt_amount']


def calculate_field_confidence(extractions: List[dict], field: str) -> float:
    """
    Calculate weighted confidence for a single field across all sources

    Args:
        extractions: List of extraction results
            [
                {'source': 'email_body', 'source_type': 'email_body', 'data': {...}, 'confidence': 0.88},
                {'source': 'invoice.pdf', 'source_type': 'pdf', 'data': {...}, 'confidence': 0.95},
                ...
            ]
        field: Field name (e.g., 'debt_amount')

    Returns:
        Weighted confidence score (0.0 - 1.0)
    """
    weighted_sum = 0.0
    total_weight = 0.0

    for extraction in extractions:
        # Check if field exists in this extraction
        field_value = extraction['data'].get(field)
        if field_value is None:
            continue

        # Get source weight
        source_type = extraction['source_type']
        weight = SOURCE_WEIGHTS.get(source_type, 0.20)

        # Get extraction confidence (can be overall or field-specific)
        if isinstance(extraction.get('confidence'), dict):
            # Field-specific confidence
            field_confidence = extraction['confidence'].get(field, 0.5)
        else:
            # Overall confidence
            field_confidence = extraction.get('confidence', 0.5)

        weighted_sum += field_confidence * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    return weighted_sum / total_weight


def consolidate_extractions(extractions: List[dict]) -> dict:
    """
    Merge data from multiple extractions, resolving conflicts

    Strategy:
    - For each field, collect all values + confidences
    - If all values agree (normalized) → use highest confidence version
    - If values conflict → prefer source with highest confidence
    - If still ambiguous → flag for manual review

    Args:
        extractions: List of extraction results from all sources

    Returns:
        Consolidated data dictionary
    """
    consolidated = {}
    field_confidences = {}
    conflicts = []

    # Get all possible fields
    all_fields = set()
    for extraction in extractions:
        all_fields.update(extraction['data'].keys())

    for field in all_fields:
        # Collect all values for this field
        field_values = []
        for extraction in extractions:
            value = extraction['data'].get(field)
            if value is not None:
                field_values.append({
                    'value': value,
                    'source': extraction['source'],
                    'source_type': extraction['source_type'],
                    'confidence': extraction.get('confidence', 0.5)
                })

        if not field_values:
            # Field not found in any extraction
            consolidated[field] = None
            field_confidences[field] = 0.0
            continue

        if len(field_values) == 1:
            # Only one source has this field
            consolidated[field] = field_values[0]['value']
            field_confidences[field] = field_values[0]['confidence']
            continue

        # Multiple sources have this field - check for conflicts
        normalized_values = [normalize_value(fv['value'], field) for fv in field_values]
        unique_values = set(normalized_values)

        if len(unique_values) == 1:
            # All values agree (after normalization)
            # Use the version from highest confidence source
            best = max(field_values, key=lambda x: x['confidence'])
            consolidated[field] = best['value']
            field_confidences[field] = calculate_field_confidence(extractions, field)
        else:
            # Conflict detected
            # Prefer value from highest weighted source
            weighted_values = []
            for fv in field_values:
                weight = SOURCE_WEIGHTS.get(fv['source_type'], 0.20)
                weighted_score = fv['confidence'] * weight
                weighted_values.append((fv['value'], weighted_score, fv['source']))

            best_value, best_score, best_source = max(weighted_values, key=lambda x: x[1])
            consolidated[field] = best_value
            field_confidences[field] = calculate_field_confidence(extractions, field) * 0.8  # Penalty for conflict

            conflicts.append({
                'field': field,
                'values': [{'value': fv['value'], 'source': fv['source']} for fv in field_values],
                'selected': best_value,
                'selected_source': best_source
            })

            logger.warning(f"Conflict in field '{field}': {unique_values}, selected: {best_value} from {best_source}")

    # Add metadata
    consolidated['_field_confidences'] = field_confidences
    consolidated['_conflicts'] = conflicts
    consolidated['_source_count'] = len(extractions)

    return consolidated


def calculate_overall_confidence(consolidated: dict) -> float:
    """
    Calculate overall record confidence

    Factors:
    1. Average confidence of required fields
    2. Completeness (all required fields present)
    3. Penalty for conflicts

    Returns:
        Overall confidence score (0.0 - 1.0)
    """
    field_confidences = consolidated['_field_confidences']

    # Check required fields
    required_confidences = []
    missing_fields = []

    for field in REQUIRED_FIELDS:
        if field in field_confidences and field_confidences[field] > 0:
            required_confidences.append(field_confidences[field])
        else:
            missing_fields.append(field)

    if not required_confidences:
        return 0.0

    # Average confidence of required fields
    avg_confidence = sum(required_confidences) / len(required_confidences)

    # Completeness factor
    completeness = len(required_confidences) / len(REQUIRED_FIELDS)

    # Conflict penalty
    conflict_penalty = 1.0
    if consolidated.get('_conflicts'):
        # Each conflict reduces confidence by 5%
        conflict_penalty = max(0.5, 1.0 - (len(consolidated['_conflicts']) * 0.05))

    # Overall score
    overall = avg_confidence * completeness * conflict_penalty

    logger.info(
        f"Overall confidence: {overall:.2f} "
        f"(avg: {avg_confidence:.2f}, completeness: {completeness:.2f}, "
        f"conflict_penalty: {conflict_penalty:.2f})"
    )

    return overall


def is_safe_to_auto_update(consolidated: dict, overall_confidence: float) -> tuple[bool, str]:
    """
    Determine if data quality is sufficient for automatic MongoDB update

    Returns:
        (is_safe, reason)
    """
    field_confidences = consolidated['_field_confidences']

    # Check overall threshold
    if overall_confidence < 0.80:
        return False, f"Overall confidence {overall_confidence:.2f} below 0.80 threshold"

    # Check per-field thresholds
    for field, threshold in FIELD_THRESHOLDS.items():
        if field in REQUIRED_FIELDS:
            field_conf = field_confidences.get(field, 0.0)
            if field_conf < threshold:
                return False, f"Field '{field}' confidence {field_conf:.2f} below {threshold:.2f} threshold"

    # Check for missing required fields
    for field in REQUIRED_FIELDS:
        if consolidated.get(field) is None:
            return False, f"Required field '{field}' is missing"

    # Check for critical conflicts
    conflicts = consolidated.get('_conflicts', [])
    critical_conflicts = [c for c in conflicts if c['field'] in REQUIRED_FIELDS]
    if critical_conflicts:
        return False, f"Critical field conflicts: {[c['field'] for c in critical_conflicts]}"

    return True, "All checks passed"


def normalize_value(value, field: str):
    """
    Normalize values for comparison (handles formatting differences)
    """
    if field == 'client_name':
        # "Max Mustermann" vs "Mustermann, Max" should match
        if isinstance(value, str):
            # Remove commas, lowercase, sort words
            words = value.replace(',', '').lower().split()
            return ' '.join(sorted(words))

    elif field == 'debt_amount':
        # "1.234,56" vs "1234.56" should match
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # German format: 1.234,56 → 1234.56
            value = value.replace('.', '').replace(',', '.')
            try:
                return float(value)
            except ValueError:
                return value

    elif field == 'creditor_name':
        # "Sparkasse Bochum" vs "sparkasse bochum GmbH" should match core name
        if isinstance(value, str):
            # Remove legal forms, lowercase
            legal_forms = ['gmbh', 'ag', 'e.v.', 'kg', 'ohg']
            clean = value.lower()
            for form in legal_forms:
                clean = clean.replace(form, '')
            return clean.strip()

    return value
```

**Example Scenario:**

```python
# Input: 3 extractions
extractions = [
    {
        'source': 'email_body',
        'source_type': 'email_body',
        'data': {
            'client_name': 'Max Mustermann',
            'creditor_name': 'Sparkasse Bochum',
            'debt_amount': 1234.56,
            'reference_numbers': ['AZ-123456']
        },
        'confidence': 0.75
    },
    {
        'source': 'forderung.pdf',
        'source_type': 'pdf',
        'data': {
            'client_name': 'Mustermann, Max',  # Different format
            'creditor_name': 'Sparkasse Bochum GmbH',  # Includes legal form
            'debt_amount': 1234.56,
            'reference_numbers': ['AZ-123456', 'KD-789']
        },
        'confidence': 0.95
    },
    {
        'source': 'breakdown.xlsx',
        'source_type': 'xlsx',
        'data': {
            'debt_amount': 1234.50,  # Slight difference (rounding?)
            'reference_numbers': ['AZ-123456']
        },
        'confidence': 0.88
    }
]

# Consolidation
consolidated = consolidate_extractions(extractions)

# Result:
# {
#     'client_name': 'Mustermann, Max',  # From PDF (highest confidence source)
#     'creditor_name': 'Sparkasse Bochum GmbH',  # From PDF
#     'debt_amount': 1234.56,  # From PDF (preferred over 1234.50 due to higher source weight)
#     'reference_numbers': ['AZ-123456', 'KD-789'],  # Merged from all sources
#     '_field_confidences': {
#         'client_name': 0.845,  # (0.75*0.30 + 0.95*0.40) / 0.70 = 0.845
#         'creditor_name': 0.845,
#         'debt_amount': 0.856,  # (0.75*0.30 + 0.95*0.40 + 0.88*0.25) / 0.95 = 0.856
#         'reference_numbers': 0.845
#     },
#     '_conflicts': [],  # No conflicts after normalization
#     '_source_count': 3
# }

overall_confidence = calculate_overall_confidence(consolidated)
# overall_confidence = (0.845 + 0.845 + 0.856) / 3 * 1.0 * 1.0 = 0.849

is_safe, reason = is_safe_to_auto_update(consolidated, overall_confidence)
# is_safe = True (0.849 >= 0.80, all field thresholds met, no critical conflicts)
```

---

## 6. Error Handling and Retry Patterns

### Question: How to handle errors in LLM-based pipelines?

### Recommendation: Tiered Retry Strategy with Circuit Breakers

**Error Categories and Strategies:**

| Error Type | Retry Strategy | Example | Handling |
|------------|---------------|---------|----------|
| **Transient API errors** | Exponential backoff, 3-5 retries | Claude 429 (rate limit), 503 (service unavailable) | Retry with backoff: 10s, 30s, 90s, 270s |
| **Invalid input** | No retry, fail fast | Malformed email, missing required fields | Log error, mark job as failed, alert |
| **External service down** | Circuit breaker pattern | MongoDB connection refused | Open circuit after 3 failures, retry after 5 min |
| **Timeout** | Retry with longer timeout | Claude API call > 60s | Increase timeout, retry 2× |
| **Data validation** | No retry, manual review | Extracted amount is negative | Route to manual review queue |
| **Worker OOM** | Automatic requeue | Worker killed by system | Celery requeues task automatically |

**Implementation:**

```python
# app/tasks/error_handling.py
from celery import Task
from anthropic import (
    APIError,
    RateLimitError,
    APIConnectionError,
    APITimeoutError
)
from requests.exceptions import RequestException
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Circuit breaker state
CIRCUIT_BREAKERS = {
    'claude_api': {'state': 'closed', 'failures': 0, 'opened_at': None},
    'mongodb': {'state': 'closed', 'failures': 0, 'opened_at': None},
    'gcs': {'state': 'closed', 'failures': 0, 'opened_at': None}
}

CIRCUIT_BREAKER_THRESHOLDS = {
    'claude_api': {'max_failures': 5, 'timeout_minutes': 5},
    'mongodb': {'max_failures': 3, 'timeout_minutes': 2},
    'gcs': {'max_failures': 3, 'timeout_minutes': 5}
}


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open"""
    pass


def check_circuit_breaker(service: str):
    """
    Check if circuit breaker is open for a service

    Raises CircuitBreakerOpen if circuit is open
    """
    breaker = CIRCUIT_BREAKERS[service]
    config = CIRCUIT_BREAKER_THRESHOLDS[service]

    if breaker['state'] == 'open':
        # Check if timeout has passed
        if breaker['opened_at']:
            timeout = timedelta(minutes=config['timeout_minutes'])
            if datetime.utcnow() - breaker['opened_at'] > timeout:
                # Try to close circuit (half-open state)
                breaker['state'] = 'half-open'
                logger.info(f"Circuit breaker for {service} entering half-open state")
            else:
                raise CircuitBreakerOpen(f"Circuit breaker open for {service}")


def record_success(service: str):
    """Record successful call to a service"""
    breaker = CIRCUIT_BREAKERS[service]
    if breaker['state'] == 'half-open':
        # Success in half-open state → close circuit
        breaker['state'] = 'closed'
        breaker['failures'] = 0
        logger.info(f"Circuit breaker for {service} closed after success")
    elif breaker['state'] == 'closed':
        # Reset failure count on success
        breaker['failures'] = 0


def record_failure(service: str):
    """Record failed call to a service"""
    breaker = CIRCUIT_BREAKERS[service]
    config = CIRCUIT_BREAKER_THRESHOLDS[service]

    breaker['failures'] += 1

    if breaker['failures'] >= config['max_failures']:
        breaker['state'] = 'open'
        breaker['opened_at'] = datetime.utcnow()
        logger.error(
            f"Circuit breaker for {service} OPENED after {breaker['failures']} failures. "
            f"Will retry in {config['timeout_minutes']} minutes."
        )


# Custom Celery task base class with retry logic
class RetryTask(Task):
    """
    Base task class with intelligent retry logic
    """
    autoretry_for = (
        APIError,
        APIConnectionError,
        APITimeoutError,
        RequestException
    )

    retry_kwargs = {
        'max_retries': 5,
        'countdown': 10  # Initial delay
    }

    retry_backoff = True  # Exponential backoff
    retry_backoff_max = 600  # Max 10 minutes between retries
    retry_jitter = True  # Add randomness to avoid thundering herd

    def retry(self, args=None, kwargs=None, exc=None, **options):
        """
        Custom retry logic with exponential backoff based on error type
        """
        if isinstance(exc, RateLimitError):
            # Rate limit: longer backoff
            countdown = 60 * (2 ** self.request.retries)  # 60s, 120s, 240s, 480s
            logger.warning(f"Rate limited, retrying in {countdown}s")
            options['countdown'] = min(countdown, 600)

        elif isinstance(exc, APITimeoutError):
            # Timeout: moderate backoff
            countdown = 30 * (2 ** self.request.retries)  # 30s, 60s, 120s, 240s
            logger.warning(f"Timeout, retrying in {countdown}s")
            options['countdown'] = min(countdown, 300)

        elif isinstance(exc, APIConnectionError):
            # Connection error: fast initial retry, then backoff
            countdown = 10 * (2 ** self.request.retries)  # 10s, 20s, 40s, 80s
            logger.warning(f"Connection error, retrying in {countdown}s")
            options['countdown'] = min(countdown, 120)

        else:
            # Generic error: standard backoff
            countdown = 10 * (2 ** self.request.retries)
            options['countdown'] = min(countdown, 300)

        return super().retry(args=args, kwargs=kwargs, exc=exc, **options)


# Example usage in extraction task
@app.task(base=RetryTask, bind=True)
def extract_with_claude(self, email_body: str, prompt_id: int):
    """
    Extract entities using Claude with circuit breaker and retry logic
    """
    try:
        # Check circuit breaker
        check_circuit_breaker('claude_api')

        # Get prompt
        from app.services.prompt_manager import prompt_manager
        prompt = prompt_manager.get_prompt_by_id(prompt_id)

        # Call Claude API
        from anthropic import Anthropic
        from app.config import settings

        client = Anthropic(api_key=settings.anthropic_api_key)

        message = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": f"{prompt['prompt_text']}\n\n{email_body}"
                }
            ]
        )

        # Success: record for circuit breaker
        record_success('claude_api')

        result = json.loads(message.content[0].text)
        return result

    except RateLimitError as exc:
        # Rate limit: record failure, will retry with backoff
        record_failure('claude_api')
        logger.warning(f"Claude rate limit hit, retry {self.request.retries}/5")
        raise self.retry(exc=exc)

    except (APIConnectionError, APITimeoutError) as exc:
        # Transient error: record failure, retry
        record_failure('claude_api')
        logger.error(f"Claude API error: {exc}, retry {self.request.retries}/5")
        raise self.retry(exc=exc)

    except CircuitBreakerOpen as exc:
        # Circuit breaker open: don't retry, fail fast
        logger.error(f"Circuit breaker open, failing task: {exc}")
        raise  # Don't retry

    except Exception as exc:
        # Unexpected error: log and fail
        logger.error(f"Unexpected error in Claude extraction: {exc}", exc_info=True)
        raise  # Don't retry on unknown errors
```

**MongoDB Write Error Handling:**

```python
# app/services/mongodb_client.py - Enhanced with retry logic
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class MongoDBService:
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionFailure, ServerSelectionTimeoutError)),
        reraise=True
    )
    def update_creditor_debt_amount(self, **kwargs) -> bool:
        """
        Update MongoDB with retry logic

        Note: PostgreSQL is source of truth. MongoDB failures are logged but don't block.
        """
        try:
            check_circuit_breaker('mongodb')

            # ... existing update logic ...

            record_success('mongodb')
            return True

        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            record_failure('mongodb')
            logger.error(f"MongoDB connection failed: {exc}")
            raise  # Tenacity will retry

        except CircuitBreakerOpen as exc:
            logger.error(f"MongoDB circuit breaker open: {exc}")
            return False  # Fail gracefully, don't block processing

        except Exception as exc:
            logger.error(f"MongoDB update failed: {exc}", exc_info=True)
            return False  # Fail gracefully
```

**Dead Letter Queue for Failed Tasks:**

```python
# app/tasks/dead_letter.py
from celery import Task
from app.database import SessionLocal
from app.models import ProcessingJob
import logging

logger = logging.getLogger(__name__)


class DeadLetterTask(Task):
    """
    Task that sends failures to dead letter queue after max retries
    """
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        Called when task fails after all retries exhausted
        """
        logger.error(
            f"Task {self.name} failed permanently - ID: {task_id}, "
            f"Error: {exc}, Args: {args}"
        )

        # Update processing job status
        if 'email_id' in kwargs or (args and len(args) > 0):
            email_id = kwargs.get('email_id') or args[0]

            db = SessionLocal()
            try:
                job = db.query(ProcessingJob).filter_by(email_id=email_id).first()
                if job:
                    job.status = 'failed'
                    job.error_details = {
                        'task': self.name,
                        'error': str(exc),
                        'retry_count': self.request.retries,
                        'stack_trace': str(einfo)
                    }
                    db.commit()

                    # Send alert to monitoring system
                    send_alert(
                        f"Email processing failed permanently",
                        f"Email ID: {email_id}, Task: {self.name}, Error: {exc}"
                    )
            finally:
                db.close()


# Use DeadLetterTask as base
@app.task(base=DeadLetterTask, bind=True, max_retries=3)
def agent_1_intake(self, email_id: int):
    # ... task logic ...
```

---

## 7. GCS Integration Patterns

### Question: How to integrate GCS for temporary attachment storage?

### Recommendation: Temp Bucket with 7-Day Lifecycle + Signed URLs

**GCS Bucket Setup:**

```bash
# Create bucket (one-time setup)
gsutil mb -l europe-west3 gs://creditor-emails-temp

# Set lifecycle policy (auto-delete after 7 days)
cat > lifecycle.json <<EOF
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 7}
      }
    ]
  }
}
EOF

gsutil lifecycle set lifecycle.json gs://creditor-emails-temp

# Set uniform bucket-level access (no public access)
gsutil uniformbucketlevelaccess set on gs://creditor-emails-temp

# Grant service account access
gsutil iam ch serviceAccount:creditor-email-matcher@PROJECT_ID.iam.gserviceaccount.com:objectAdmin gs://creditor-emails-temp
```

**GCS Client Service:**

```python
# app/services/gcs_client.py
from google.cloud import storage
from google.oauth2 import service_account
from app.config import settings
from datetime import timedelta
import logging
import mimetypes

logger = logging.getLogger(__name__)


class GCSClient:
    """
    Google Cloud Storage client for attachment management
    """

    def __init__(self):
        # Load credentials from JSON key file or environment
        if settings.gcs_credentials_path:
            credentials = service_account.Credentials.from_service_account_file(
                settings.gcs_credentials_path
            )
            self.client = storage.Client(
                credentials=credentials,
                project=settings.gcp_project_id
            )
        else:
            # Use default credentials (works on GCP environments)
            self.client = storage.Client()

        self.bucket_name = settings.gcs_bucket_name
        self.bucket = self.client.bucket(self.bucket_name)

    def upload_attachment(
        self,
        email_id: int,
        filename: str,
        content: bytes,
        content_type: str = None
    ) -> str:
        """
        Upload attachment to GCS

        Args:
            email_id: Email ID (used for folder structure)
            filename: Original filename
            content: File bytes
            content_type: MIME type (auto-detected if not provided)

        Returns:
            GCS path (gs://bucket/email_id/filename)
        """
        try:
            # Construct blob path: {email_id}/{filename}
            blob_path = f"{email_id}/{filename}"
            blob = self.bucket.blob(blob_path)

            # Detect content type if not provided
            if not content_type:
                content_type, _ = mimetypes.guess_type(filename)
                if not content_type:
                    content_type = 'application/octet-stream'

            # Upload with metadata
            blob.upload_from_string(
                content,
                content_type=content_type
            )

            # Set metadata
            blob.metadata = {
                'email_id': str(email_id),
                'original_filename': filename,
                'uploaded_at': datetime.utcnow().isoformat()
            }
            blob.patch()

            gcs_path = f"gs://{self.bucket_name}/{blob_path}"
            logger.info(f"Uploaded attachment to GCS: {gcs_path}")

            return gcs_path

        except Exception as exc:
            logger.error(f"Failed to upload to GCS: {exc}", exc_info=True)
            raise

    def download_attachment(self, gcs_path: str) -> bytes:
        """
        Download attachment from GCS

        Args:
            gcs_path: GCS path (gs://bucket/path/to/file)

        Returns:
            File content as bytes
        """
        try:
            # Parse GCS path
            if not gcs_path.startswith('gs://'):
                raise ValueError(f"Invalid GCS path: {gcs_path}")

            path_parts = gcs_path[5:].split('/', 1)  # Remove 'gs://'
            bucket_name = path_parts[0]
            blob_path = path_parts[1] if len(path_parts) > 1 else ''

            # Download
            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(blob_path)

            content = blob.download_as_bytes()
            logger.debug(f"Downloaded from GCS: {gcs_path}, size: {len(content)} bytes")

            return content

        except Exception as exc:
            logger.error(f"Failed to download from GCS: {exc}", exc_info=True)
            raise

    def generate_signed_url(self, gcs_path: str, expiration_minutes: int = 60) -> str:
        """
        Generate signed URL for temporary access to attachment

        Args:
            gcs_path: GCS path
            expiration_minutes: URL validity duration

        Returns:
            Signed URL (valid for expiration_minutes)
        """
        try:
            path_parts = gcs_path[5:].split('/', 1)
            bucket_name = path_parts[0]
            blob_path = path_parts[1]

            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(blob_path)

            url = blob.generate_signed_url(
                version='v4',
                expiration=timedelta(minutes=expiration_minutes),
                method='GET'
            )

            logger.debug(f"Generated signed URL for {gcs_path}, expires in {expiration_minutes} min")
            return url

        except Exception as exc:
            logger.error(f"Failed to generate signed URL: {exc}", exc_info=True)
            raise

    def delete_attachment(self, gcs_path: str) -> bool:
        """
        Manually delete attachment (normally handled by lifecycle policy)

        Args:
            gcs_path: GCS path

        Returns:
            True if deleted, False otherwise
        """
        try:
            path_parts = gcs_path[5:].split('/', 1)
            bucket_name = path_parts[0]
            blob_path = path_parts[1]

            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(blob_path)

            blob.delete()
            logger.info(f"Deleted from GCS: {gcs_path}")
            return True

        except Exception as exc:
            logger.warning(f"Failed to delete from GCS: {exc}")
            return False

    def list_email_attachments(self, email_id: int) -> list:
        """
        List all attachments for an email

        Args:
            email_id: Email ID

        Returns:
            List of GCS paths
        """
        prefix = f"{email_id}/"
        blobs = self.bucket.list_blobs(prefix=prefix)

        paths = [f"gs://{self.bucket_name}/{blob.name}" for blob in blobs]
        logger.debug(f"Found {len(paths)} attachments for email {email_id}")

        return paths


# Global instance
gcs_client = GCSClient()


# Convenience function for downloading
def download_from_gcs(gcs_path: str) -> bytes:
    """Download file from GCS (used in extraction tasks)"""
    return gcs_client.download_attachment(gcs_path)
```

**Attachment Download Service (Agent 1):**

```python
# app/services/attachment_downloader.py
from app.services.gcs_client import gcs_client
from app.config import settings
import requests
import logging

logger = logging.getLogger(__name__)


def download_attachments(email_id: int, attachment_urls: list) -> list:
    """
    Download attachments from Zendesk URLs and upload to GCS

    Args:
        email_id: Email ID
        attachment_urls: List of dicts with 'filename' and 'content_url'
            [
                {'filename': 'invoice.pdf', 'content_url': 'https://zendesk.com/...'},
                ...
            ]

    Returns:
        List of GCS paths
    """
    gcs_paths = []

    for attachment in attachment_urls:
        try:
            filename = attachment['filename']
            url = attachment['content_url']

            logger.info(f"Downloading attachment: {filename}")

            # Download from Zendesk (URLs are pre-authenticated)
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            content = response.content
            content_type = response.headers.get('Content-Type')

            logger.info(f"Downloaded {filename}, size: {len(content)} bytes")

            # Upload to GCS
            gcs_path = gcs_client.upload_attachment(
                email_id=email_id,
                filename=filename,
                content=content,
                content_type=content_type
            )

            gcs_paths.append(gcs_path)

        except Exception as exc:
            logger.error(f"Failed to download attachment {filename}: {exc}", exc_info=True)
            # Continue with other attachments

    logger.info(f"Downloaded {len(gcs_paths)}/{len(attachment_urls)} attachments for email {email_id}")
    return gcs_paths
```

**Updated `incoming_emails` Schema:**

```sql
-- Add columns for attachment tracking
ALTER TABLE incoming_emails
ADD COLUMN zendesk_attachment_urls JSONB,  -- Raw URLs from webhook
ADD COLUMN gcs_attachment_paths JSONB;     -- Uploaded GCS paths

-- Example data:
-- zendesk_attachment_urls: [
--   {"filename": "invoice.pdf", "content_url": "https://...", "size": 123456},
--   {"filename": "letter.docx", "content_url": "https://...", "size": 45678}
-- ]
-- gcs_attachment_paths: [
--   "gs://creditor-emails-temp/42/invoice.pdf",
--   "gs://creditor-emails-temp/42/letter.docx"
-- ]
```

**Lifecycle Management:**

1. **Upload** (Agent 1): Download from Zendesk → Upload to GCS
2. **Process** (Agent 2): Download from GCS → Extract → Discard bytes
3. **Cleanup** (Automatic): GCS lifecycle policy deletes after 7 days

**Why 7 days:**
- Attachments needed only during processing (minutes to hours)
- 7-day buffer allows manual review of failed emails
- Permanent storage not needed (original in Zendesk)

**Cost estimate:**
```
Storage: ~1GB/month (200 emails × 5MB avg) × 7/30 days = 0.23GB
Cost: $0.02/GB/month in europe-west3 = $0.005/month
Operations: 200 uploads + 600 downloads (3 per email) = $0.001/month
Total: <$0.01/month
```

---

## Confidence Assessment

| Architecture Area | Confidence | Reason |
|------------------|-----------|--------|
| Agent Orchestration (Celery chains + DB state) | HIGH | Based on existing Celery patterns and analysis of current v1 webhook code structure |
| Job Queue Topology (single queue for 200/day) | MEDIUM | Math checks out for volume, but needs load testing to validate assumptions |
| Attachment Processing (type-specific extractors) | MEDIUM | Claude PDF/Vision API capabilities assumed based on training data; need to verify current API features |
| Prompt Repository (PostgreSQL versioning) | HIGH | Standard database pattern, existing v1 prompts provide migration baseline |
| Confidence Scoring (weighted aggregation) | LOW | Weights and thresholds are estimated; need real data to calibrate properly |
| Error Handling (circuit breakers, retries) | MEDIUM | Established patterns, but specific failure modes (Claude rate limits) need production validation |
| GCS Integration (temp storage with lifecycle) | HIGH | GCS lifecycle policies are well-documented feature, straightforward implementation |

## Sources

**Existing Codebase Analysis:**
- `/Users/luka.s/NEW AI Creditor Answer Analysis/_existing-code/app/routers/webhook.py` - Current v1 synchronous processing
- `/Users/luka.s/NEW AI Creditor Answer Analysis/_existing-code/app/services/entity_extractor_claude.py` - Existing Claude integration patterns
- `/Users/luka.s/NEW AI Creditor Answer Analysis/_existing-code/app/services/mongodb_client.py` - MongoDB update patterns
- `/Users/luka.s/NEW AI Creditor Answer Analysis/_existing-code/app/config.py` - Configuration structure
- `/Users/luka.s/NEW AI Creditor Answer Analysis/.planning/PROJECT.md` - Requirements and constraints

**Architecture Patterns (from training data - January 2025 cutoff):**
- Celery Canvas documentation patterns (chains, groups, chords)
- Multi-agent LLM pipeline architectures
- Database state machine patterns for job tracking
- Circuit breaker pattern for external service resilience
- Weighted confidence scoring algorithms
- GCS lifecycle policies and signed URL patterns

**Verification Recommended:**
- Celery 5.x latest features against official docs
- Claude API current capabilities (PDF native, Vision API)
- Anthropic rate limits and pricing as of 2026
- Render platform constraints for Celery workers (memory, concurrency)
- GCS API changes since training cutoff

**Note:** WebSearch and WebFetch were unavailable during this research. Recommendations are based on analysis of the existing v1 codebase + established distributed systems patterns from training data. Production deployment should validate Claude API capabilities and Render/GCS integration specifics against current official documentation.
