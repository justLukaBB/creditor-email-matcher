# Phase 3: Multi-Format Document Extraction - Research

**Researched:** 2026-02-04
**Domain:** Multi-format document extraction (PDF, DOCX, XLSX, images) with LLM fallback
**Confidence:** HIGH

## Summary

Multi-format document extraction requires a hybrid approach: native text extraction libraries (PyMuPDF, python-docx, openpyxl) for digitally-generated documents with Claude Vision API as fallback for scanned/complex documents. The standard stack for Python in 2026 is well-established with PyMuPDF dominating PDF extraction (3x faster than alternatives), python-docx for DOCX, openpyxl for XLSX, and Claude Vision API for both fallback and image processing.

Critical considerations for this phase include:
- **Memory constraints**: Render's 512MB worker limits require streaming approaches and careful PDF page-by-page processing
- **Cost controls**: Claude API token budgets must be enforced per-job (max 100K tokens recommended) with circuit breakers for daily limits
- **Format detection**: PDFs require detection logic to determine if scanned (send to Claude Vision) vs digital (use PyMuPDF)
- **GCS integration**: Attachments must be downloaded from GCS, processed locally with temp cleanup, then results stored

The Forderungshoehe extraction goal (German creditor claim amounts) is straightforward text extraction, not requiring complex NER models—Claude Vision with structured prompts provides high accuracy for fallback cases.

**Primary recommendation:** Use PyMuPDF for digital PDFs with text-to-filesize ratio detection (< 0.01 = scanned), fall back to Claude Vision API for scanned PDFs and images. Enforce 100K token budget per job with daily circuit breaker. Process PDFs page-by-page in memory-constrained environment using streaming patterns.

## Standard Stack

### Core Libraries

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyMuPDF (fitz) | 1.24+ | PDF text extraction | 3x faster than pdftotext, 30-45x faster than PyPDF2/pdfminer. High table detection precision. No external dependencies. Industry standard for Python PDF extraction. |
| anthropic | 0.40+ | Claude Vision API client | Official Python SDK for Claude API. Supports PDF, image, and document processing with native multimodal capabilities. |
| python-docx | 1.2.0+ | DOCX text/table extraction | De facto standard for Word document manipulation in Python. Maintained by python-openxml team. |
| openpyxl | 3.1.5+ | XLSX data extraction | Standard library for Excel 2010+ formats. Read-only mode for memory efficiency. |
| google-cloud-storage | 2.18+ | GCS file download/upload | Official Google Cloud client library. Supports streaming and automatic cleanup. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| docx2python | 2.8+ | Alternative DOCX extractor | When document order (paragraphs + tables + images interleaved) is critical. Provides cleaner structured output. |
| Pillow | 10+ | Image preprocessing | Resize images before sending to Claude Vision to optimize token usage. |
| httpx | 0.27+ | HTTP client for downloads | Alternative to requests for async-capable HTTP operations. Used in examples for downloading from URLs. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyMuPDF | pdfplumber | pdfplumber has better table extraction but 10x slower. PyMuPDF's find_tables() is sufficient for most cases. |
| Claude Vision | Tesseract OCR | Tesseract is free but requires separate OCR service deployment, complex preprocessing, and provides no structured extraction. Claude Vision combines OCR + extraction + understanding. |
| openpyxl | pandas.read_excel() | pandas is heavier (more dependencies) and designed for analysis not extraction. openpyxl is more lightweight for read-only operations. |
| GCS Python client | Direct HTTP API | Client library handles auth, retries, streaming automatically. HTTP API requires manual implementation. |

**Installation:**
```bash
pip install PyMuPDF anthropic python-docx openpyxl google-cloud-storage Pillow httpx
```

## Architecture Patterns

### Recommended Project Structure
```
app/
├── services/
│   ├── extraction/
│   │   ├── __init__.py
│   │   ├── base.py              # BaseExtractor abstract class
│   │   ├── pdf_extractor.py     # PyMuPDF + Claude Vision fallback
│   │   ├── docx_extractor.py    # python-docx extraction
│   │   ├── xlsx_extractor.py    # openpyxl extraction
│   │   ├── image_extractor.py   # Claude Vision for JPG/PNG
│   │   └── detector.py          # Format detection and scanned PDF detection
│   ├── storage/
│   │   └── gcs_client.py        # GCS download/upload with streaming
│   └── cost_control/
│       ├── token_counter.py     # Per-job token budget tracking
│       └── circuit_breaker.py   # Daily cost limit enforcement
├── actors/
│   └── content_extractor.py     # Dramatiq actor (calls extraction services)
└── models/
    └── extraction_result.py     # Pydantic models for structured output
```

### Pattern 1: Hybrid Extraction Strategy

**What:** Use native library extraction first, fall back to Claude Vision only when necessary.

**When to use:** Always for PDF processing to minimize API costs and latency.

**Example:**
```python
# Source: Researched pattern combining PyMuPDF detection + Claude Vision fallback
import fitz  # PyMuPDF
from anthropic import Anthropic

class PDFExtractor:
    def __init__(self, claude_client: Anthropic, token_budget: int = 100000):
        self.claude_client = claude_client
        self.token_budget = token_budget
        self.tokens_used = 0

    def is_scanned_pdf(self, pdf_path: str) -> bool:
        """Detect if PDF is scanned using text-to-filesize ratio."""
        import os

        doc = fitz.open(pdf_path)
        total_text = ""
        for page in doc:
            total_text += page.get_text()
        doc.close()

        file_size = os.path.getsize(pdf_path)
        text_ratio = len(total_text) / file_size

        # If text is less than 1% of file size, likely scanned
        return text_ratio < 0.01

    def extract_with_pymupdf(self, pdf_path: str, max_pages: int = 10) -> dict:
        """Extract text using PyMuPDF for digital PDFs."""
        doc = fitz.open(pdf_path)

        # Handle >10 page limit: first 5 + last 5 pages
        total_pages = len(doc)
        if total_pages > max_pages:
            pages_to_process = (
                list(range(5)) +  # First 5 pages
                list(range(total_pages - 5, total_pages))  # Last 5 pages
            )
        else:
            pages_to_process = range(total_pages)

        extracted_text = []
        for page_num in pages_to_process:
            page = doc[page_num]
            text = page.get_text("text", sort=True)  # Sort for natural reading order
            extracted_text.append({"page": page_num + 1, "text": text})

        doc.close()
        return {"pages": extracted_text, "method": "pymupdf"}

    def extract_with_claude_vision(self, pdf_path: str, max_pages: int = 10) -> dict:
        """Fallback to Claude Vision for scanned PDFs."""
        import base64

        # Estimate: ~2000 tokens per page for Claude Vision processing
        estimated_tokens = max_pages * 2000
        if self.tokens_used + estimated_tokens > self.token_budget:
            raise TokenBudgetExceeded(
                f"Would exceed budget: {self.tokens_used + estimated_tokens} > {self.token_budget}"
            )

        with open(pdf_path, "rb") as f:
            pdf_data = base64.b64encode(f.read()).decode("utf-8")

        message = self.claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data
                        }
                    },
                    {
                        "type": "text",
                        "text": """Extract the following from this German creditor document:
                        1. Gesamtforderung (total claim amount)
                        2. Gläubiger (creditor name)
                        3. Schuldner (debtor/client name)

                        Return as JSON: {"gesamtforderung": "123.45 EUR", "glaeubiger": "...", "schuldner": "..."}"""
                    }
                ]
            }]
        )

        self.tokens_used += message.usage.input_tokens + message.usage.output_tokens
        return {
            "extracted_data": message.content[0].text,
            "method": "claude_vision",
            "tokens_used": self.tokens_used
        }

    def extract(self, pdf_path: str) -> dict:
        """Main extraction method with automatic fallback."""
        if self.is_scanned_pdf(pdf_path):
            return self.extract_with_claude_vision(pdf_path)
        else:
            return self.extract_with_pymupdf(pdf_path)
```

### Pattern 2: Token Budget Enforcement

**What:** Track token usage per job and enforce hard limits to prevent cost explosions.

**When to use:** Every Claude API call in extraction pipeline.

**Example:**
```python
# Source: Based on Claude API rate limit patterns
class TokenBudgetTracker:
    """Per-job token budget enforcement."""

    def __init__(self, max_tokens_per_job: int = 100000):
        self.max_tokens = max_tokens_per_job
        self.used_tokens = 0

    def check_budget(self, estimated_tokens: int) -> bool:
        """Check if operation would exceed budget."""
        return (self.used_tokens + estimated_tokens) <= self.max_tokens

    def add_usage(self, input_tokens: int, output_tokens: int):
        """Record token usage from Claude API response."""
        self.used_tokens += (input_tokens + output_tokens)

    def remaining(self) -> int:
        """Tokens remaining in budget."""
        return self.max_tokens - self.used_tokens

class DailyCostCircuitBreaker:
    """Daily cost limit enforcement using Redis."""

    def __init__(self, redis_client, daily_limit_usd: float = 50.0):
        self.redis = redis_client
        self.daily_limit = daily_limit_usd
        self.key_prefix = "cost_tracker"

    def check_and_record_cost(self, estimated_cost_usd: float) -> bool:
        """Check if within daily limit and record cost."""
        from datetime import datetime

        today = datetime.utcnow().strftime("%Y-%m-%d")
        key = f"{self.key_prefix}:{today}"

        # Get current spend
        current_spend = float(self.redis.get(key) or 0)

        # Check if would exceed limit
        if current_spend + estimated_cost_usd > self.daily_limit:
            return False

        # Record cost (atomic increment)
        self.redis.incrbyfloat(key, estimated_cost_usd)
        self.redis.expire(key, 86400 * 2)  # Keep for 2 days

        return True
```

### Pattern 3: Streaming GCS Downloads with Temp Cleanup

**What:** Download attachments from GCS to temporary files, process, then cleanup.

**When to use:** All attachment processing to avoid keeping files in memory.

**Example:**
```python
# Source: GCS Python client best practices
from google.cloud import storage
import tempfile
import os
from contextlib import contextmanager

class GCSAttachmentHandler:
    """Handle GCS downloads with automatic cleanup."""

    def __init__(self):
        self.client = storage.Client()

    @contextmanager
    def download_attachment(self, gcs_url: str):
        """Download from GCS to temp file, yield path, cleanup on exit."""
        # Parse GCS URL: gs://bucket/path/to/file.pdf
        if not gcs_url.startswith("gs://"):
            raise ValueError(f"Invalid GCS URL: {gcs_url}")

        parts = gcs_url[5:].split("/", 1)
        bucket_name = parts[0]
        blob_path = parts[1]

        # Get file extension for temp file
        _, ext = os.path.splitext(blob_path)

        # Create temp file
        temp_fd, temp_path = tempfile.mkstemp(suffix=ext)
        os.close(temp_fd)

        try:
            # Download from GCS
            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            blob.download_to_filename(temp_path)

            yield temp_path
        finally:
            # Cleanup temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def download_with_size_check(self, gcs_url: str, max_size_mb: int = 32):
        """Download only if under size limit (Claude API 32MB limit)."""
        parts = gcs_url[5:].split("/", 1)
        bucket = self.client.bucket(parts[0])
        blob = bucket.blob(parts[1])

        # Check size before download
        size_mb = blob.size / (1024 * 1024)
        if size_mb > max_size_mb:
            raise FileTooLargeError(f"File {gcs_url} is {size_mb:.2f}MB, max is {max_size_mb}MB")

        return self.download_attachment(gcs_url)

# Usage in extraction actor
gcs_handler = GCSAttachmentHandler()

with gcs_handler.download_attachment("gs://my-bucket/attachments/invoice.pdf") as pdf_path:
    # Process the file
    result = pdf_extractor.extract(pdf_path)
    # Temp file automatically deleted when exiting context
```

### Pattern 4: Memory-Efficient XLSX Processing

**What:** Use openpyxl read-only mode for large Excel files in memory-constrained environment.

**When to use:** All XLSX extraction on Render 512MB workers.

**Example:**
```python
# Source: openpyxl optimized modes documentation
from openpyxl import load_workbook

class XLSXExtractor:
    """Memory-efficient XLSX extraction."""

    def extract_all_sheets(self, xlsx_path: str) -> dict:
        """Extract data from all sheets using read-only mode."""
        # Read-only mode: ~constant memory regardless of file size
        wb = load_workbook(xlsx_path, read_only=True, data_only=True)

        sheets_data = {}
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]

            # Extract all rows efficiently
            rows = []
            for row in sheet.iter_rows(values_only=True):
                # Filter out completely empty rows
                if any(cell is not None for cell in row):
                    rows.append(list(row))

            sheets_data[sheet_name] = rows

        wb.close()
        return sheets_data

    def search_for_amount(self, xlsx_path: str, keywords: list[str]) -> dict:
        """Search for Forderungshoehe by keywords across all sheets."""
        wb = load_workbook(xlsx_path, read_only=True, data_only=True)

        results = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]

            for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                for col_idx, cell in enumerate(row, start=1):
                    if cell and any(kw.lower() in str(cell).lower() for kw in keywords):
                        # Found keyword, check adjacent cells for amount
                        next_cell = row[col_idx] if col_idx < len(row) else None
                        results.append({
                            "sheet": sheet_name,
                            "row": row_idx,
                            "col": col_idx,
                            "label": cell,
                            "value": next_cell
                        })

        wb.close()
        return {"matches": results}
```

### Anti-Patterns to Avoid

- **Loading entire PDF into memory**: Always process page-by-page in 512MB environment. Don't use `doc.load_page()` for all pages at once.
- **Sending every PDF to Claude Vision**: Native extraction is 100x cheaper and faster. Only use Claude for scanned PDFs or when PyMuPDF extraction fails.
- **No token budget enforcement**: Claude API calls can spiral to thousands of dollars without per-job and daily limits.
- **Synchronous GCS downloads in actor**: Download all attachments first before processing to avoid blocking on I/O during extraction.
- **Keeping temp files after processing**: Memory leaks accumulate on long-running workers. Always cleanup temp files in finally blocks.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| PDF text extraction | Custom PDF parser using PyPDF2 | PyMuPDF (fitz) | PyMuPDF is 30-45x faster, handles complex layouts, includes table detection. Edge cases like form fields, annotations, embedded fonts are already handled. |
| Scanned PDF detection | Image-based heuristics (check for embedded images) | Text-to-filesize ratio + page.get_text() | PyMuPDF community has validated this approach. Image presence doesn't mean scanned (digital PDFs have images too). |
| Token counting for images | Manual pixel calculations | Claude API usage response | Claude's actual token count varies by model version and image complexity. API response gives exact usage. |
| DOCX document order | Custom XML parsing of .docx structure | docx2python library | .docx is complex Office Open XML format. docx2python handles relationships, ordering, embedded objects correctly. |
| Cost tracking | Application-level cost calculation | Redis atomic counters + Claude API usage | Race conditions in cost tracking can cause budget overruns. Redis INCRBYFLOAT is atomic and distributed-safe. |
| Temp file cleanup | Manual os.unlink() calls | contextlib.contextmanager pattern | Easy to miss cleanup on exceptions. Context managers guarantee cleanup even with errors. |

**Key insight:** Document extraction has mature ecosystems. Custom solutions miss edge cases that took years to discover (encrypted PDFs, corrupted files, Unicode handling, font encoding, embedded objects). Use battle-tested libraries and focus on business logic (German creditor data extraction).

## Common Pitfalls

### Pitfall 1: Claude Vision PDF Token Explosion

**What goes wrong:** A single 100-page PDF sent to Claude Vision can consume 200K tokens ($600+ for Opus, $60+ for Sonnet), exhausting daily budgets in one job.

**Why it happens:** Claude Vision processes PDFs as both text + images. Each page = ~2000 tokens. The 100-page limit in docs is a maximum, not a recommendation.

**How to avoid:**
- Enforce 10-page limit per PDF (user decision: first 5 + last 5 for >10 pages)
- Always check if PDF is scanned before sending to Claude
- Use PyMuPDF first, Claude Vision only for fallback
- Implement 100K token budget per job with hard cutoff

**Warning signs:**
- `usage.input_tokens > 50000` in Claude API response for single document
- Job processing time > 30 seconds
- Daily Claude API spend exceeds $50

### Pitfall 2: Memory Bloat on Render 512MB Workers

**What goes wrong:** Worker crashes with OOMKilled error when processing multiple large PDFs in sequence. Memory usage creeps up until process dies.

**Why it happens:**
- PyMuPDF keeps document objects in memory even after `close()`
- Temp files not cleaned up accumulate disk I/O buffers in memory
- Python garbage collection doesn't run frequently enough for large objects

**How to avoid:**
```python
# Bad: Memory leak pattern
def process_pdf(path):
    doc = fitz.open(path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()  # Not enough! doc still in memory
    return text

# Good: Explicit cleanup pattern
def process_pdf(path):
    doc = fitz.open(path)
    try:
        pages_data = []
        for page in doc:
            pages_data.append(page.get_text())
            # Process page immediately, don't accumulate
    finally:
        doc.close()
        del doc  # Explicit deletion
        import gc; gc.collect()  # Force GC
    return pages_data
```

**Warning signs:**
- Gradual memory increase over hours (monitor with `/proc/self/status`)
- Random worker restarts with exit code 137 (OOMKilled)
- Processing same file size gets slower over time

### Pitfall 3: Ignoring Password-Protected PDFs

**What goes wrong:** PyMuPDF throws exception on encrypted PDFs, crashing entire job instead of skipping that attachment.

**Why it happens:** Many creditor documents are password-protected. PyMuPDF.open() raises `RuntimeError` on encrypted files.

**How to avoid:**
```python
import fitz

def safe_open_pdf(path: str) -> Optional[fitz.Document]:
    """Safely open PDF, return None if encrypted/corrupted."""
    try:
        doc = fitz.open(path)
        if doc.is_encrypted:
            # User decision: skip encrypted attachments
            logger.warning(f"Skipping encrypted PDF: {path}")
            doc.close()
            return None
        return doc
    except Exception as e:
        logger.error(f"Failed to open PDF {path}: {e}")
        return None

# Usage
doc = safe_open_pdf(pdf_path)
if doc is None:
    # Continue processing other attachments
    return {"error": "encrypted_or_corrupted", "status": "skipped"}
```

**Warning signs:**
- Jobs failing with "RuntimeError: PDF is encrypted" in logs
- Some customer emails never complete processing
- No error handling around `fitz.open()`

### Pitfall 4: Highest-Amount-Wins Logic Without Deduplication

**What goes wrong:** Email body says "100 EUR", PDF attachment has table with "100 EUR" (principal) and "120 EUR" (total with fees). System extracts three amounts, picks 120 EUR, but should deduplicate and sum components.

**Why it happens:** User decision states "highest amount wins" when multiple sources have conflicting amounts. But if same document has multiple amount types, simple max() gives wrong result.

**How to avoid:**
```python
def consolidate_amounts(email_body_amount: float, attachment_amounts: list[dict]) -> float:
    """
    Apply highest-amount-wins with deduplication.

    User decisions:
    - Email body + attachments: highest wins
    - No explicit Gesamtforderung: sum components (Hauptforderung + Zinsen + Kosten)
    - Nothing found: default 100 EUR
    """
    all_amounts = []

    if email_body_amount:
        all_amounts.append(email_body_amount)

    for attachment in attachment_amounts:
        # Check if attachment has explicit Gesamtforderung
        if attachment.get("gesamtforderung"):
            all_amounts.append(attachment["gesamtforderung"])
        elif attachment.get("components"):
            # Sum components: Hauptforderung + Zinsen + Kosten
            component_sum = sum(attachment["components"].values())
            all_amounts.append(component_sum)

    if not all_amounts:
        return 100.0  # User decision: default fallback

    # Deduplicate: if amounts are within 1 EUR, consider same
    unique_amounts = []
    for amt in sorted(all_amounts, reverse=True):
        if not any(abs(amt - existing) < 1.0 for existing in unique_amounts):
            unique_amounts.append(amt)

    return max(unique_amounts)  # Highest amount wins
```

**Warning signs:**
- Extracted amounts don't match what human sees in document
- System picks "Hauptforderung" when "Gesamtforderung" exists
- Edge case: amounts like 99.99 and 100.00 treated as different values

### Pitfall 5: Claude Vision Confidence Score Misinterpretation

**What goes wrong:** Treating Claude Vision output as "low confidence" when it doesn't provide numeric confidence scores like traditional ML models.

**Why it happens:** Developers expect confidence scores from document extraction (like OCR systems provide). Claude API doesn't return per-field confidence scores.

**How to avoid:**
```python
def extract_with_confidence(text: str) -> dict:
    """
    Estimate confidence based on Claude's response structure.

    Research finding: Claude doesn't provide numeric confidence.
    Instead, assess confidence from response characteristics.
    """
    import re

    # Parse Claude's JSON response
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"confidence": "LOW", "reason": "invalid_json"}

    confidence_indicators = {
        "HIGH": [],
        "MEDIUM": [],
        "LOW": []
    }

    # High confidence: Explicit currency and decimal precision
    if "gesamtforderung" in data:
        amount_str = data["gesamtforderung"]
        if re.match(r'\d+\.\d{2}\s*(EUR|€)', amount_str):
            confidence_indicators["HIGH"].append("precise_amount_format")
        elif re.match(r'\d+', amount_str):
            confidence_indicators["MEDIUM"].append("numeric_value_found")
        else:
            confidence_indicators["LOW"].append("no_numeric_value")
    else:
        confidence_indicators["LOW"].append("field_missing")

    # High confidence: Full names (not abbreviations)
    if data.get("glaeubiger") and len(data["glaeubiger"]) > 10:
        confidence_indicators["HIGH"].append("full_creditor_name")

    # Determine overall confidence
    if confidence_indicators["LOW"]:
        overall = "LOW"
    elif len(confidence_indicators["HIGH"]) >= 2:
        overall = "HIGH"
    else:
        overall = "MEDIUM"

    return {
        "confidence": overall,
        "indicators": confidence_indicators,
        "extracted_data": data
    }
```

**Warning signs:**
- No confidence tracking in extraction results
- Treating all Claude Vision outputs as equally reliable
- No mechanism to flag uncertain extractions for human review

## Code Examples

Verified patterns from official sources:

### Claude Vision PDF Processing with Base64

```python
# Source: https://platform.claude.com/docs/en/docs/build-with-claude/pdf-support
import anthropic
import base64

client = anthropic.Anthropic()

# Load and encode PDF
with open("creditor_notice.pdf", "rb") as f:
    pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")

# Extract structured data with Claude Vision
message = client.messages.create(
    model="claude-sonnet-4-5",
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
                        "data": pdf_data
                    }
                },
                {
                    "type": "text",
                    "text": """Analyze this German creditor document and extract:

1. Gesamtforderung (total claim): The complete amount owed including all components
2. If no explicit Gesamtforderung: Sum Hauptforderung + Zinsen + Kosten
3. Gläubiger (creditor name): The entity owed the money
4. Schuldner (debtor name): The person/entity who owes the money

Return ONLY valid JSON in this format:
{
  "gesamtforderung": "123.45 EUR",
  "glaeubiger": "creditor name",
  "schuldner": "debtor name",
  "components": {
    "hauptforderung": 100.00,
    "zinsen": 15.45,
    "kosten": 8.00
  }
}"""
                }
            ]
        }
    ]
)

print(f"Tokens used: {message.usage.input_tokens + message.usage.output_tokens}")
print(f"Extracted data: {message.content[0].text}")
```

### PyMuPDF Text Extraction with Table Detection

```python
# Source: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
import fitz  # PyMuPDF

def extract_pdf_with_tables(pdf_path: str) -> dict:
    """Extract text and tables from PDF using PyMuPDF."""
    doc = fitz.open(pdf_path)

    results = {
        "text": [],
        "tables": []
    }

    for page_num, page in enumerate(doc):
        # Extract text with natural reading order
        text = page.get_text("text", sort=True)
        results["text"].append({
            "page": page_num + 1,
            "content": text
        })

        # Extract tables with high precision detection
        tables = page.find_tables()
        for table_num, table in enumerate(tables):
            # table.extract() returns list of lists (rows x columns)
            table_data = table.extract()
            results["tables"].append({
                "page": page_num + 1,
                "table_num": table_num + 1,
                "rows": table_data
            })

    doc.close()
    return results
```

### DOCX Extraction with Document Order

```python
# Source: https://github.com/python-openxml/python-docx
from docx import Document

def extract_docx_structured(docx_path: str) -> dict:
    """Extract text and tables from DOCX preserving structure."""
    doc = Document(docx_path)

    results = {
        "paragraphs": [],
        "tables": []
    }

    # Extract all paragraphs
    for para in doc.paragraphs:
        if para.text.strip():  # Skip empty paragraphs
            results["paragraphs"].append({
                "text": para.text,
                "style": para.style.name
            })

    # Extract all tables
    for table_num, table in enumerate(doc.tables):
        table_data = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            table_data.append(row_data)

        results["tables"].append({
            "table_num": table_num + 1,
            "rows": table_data
        })

    return results
```

### Memory-Efficient XLSX Reading

```python
# Source: https://openpyxl.readthedocs.io/en/3.1/optimized.html
from openpyxl import load_workbook

def extract_xlsx_memory_efficient(xlsx_path: str) -> dict:
    """Extract XLSX data with minimal memory footprint."""
    # read_only=True: constant memory usage
    # data_only=True: read calculated values, not formulas
    wb = load_workbook(xlsx_path, read_only=True, data_only=True)

    results = {}

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]

        rows = []
        # iter_rows with values_only=True is most memory efficient
        for row in sheet.iter_rows(values_only=True):
            # Filter empty rows
            if any(cell is not None for cell in row):
                rows.append(list(row))

        results[sheet_name] = rows

    wb.close()
    return results
```

### GCS Download with Automatic Cleanup

```python
# Source: https://docs.cloud.google.com/python/docs/reference/storage/latest
from google.cloud import storage
import tempfile
import os
from contextlib import contextmanager

@contextmanager
def download_from_gcs(gcs_url: str):
    """Download file from GCS, yield local path, auto-cleanup."""
    # Parse gs://bucket/path/to/file
    parts = gcs_url.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_path = parts[1]

    # Create temp file
    _, ext = os.path.splitext(blob_path)
    fd, temp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)

    try:
        # Download
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(temp_path)

        yield temp_path
    finally:
        # Cleanup (even if exception occurred)
        if os.path.exists(temp_path):
            os.unlink(temp_path)

# Usage
with download_from_gcs("gs://my-bucket/invoice.pdf") as pdf_path:
    result = extract_pdf(pdf_path)
    # temp_path automatically deleted here
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pdfplumber for all PDFs | PyMuPDF for digital, Claude Vision for scanned | 2024-2025 | PyMuPDF is 10x faster than pdfplumber. Claude Vision handles scanned PDFs better than Tesseract OCR while also providing extraction logic. |
| Tesseract OCR for scanned PDFs | Claude Vision API | Late 2024 | Claude's multimodal PDF support (Feb 2025) eliminated need for separate OCR service. Text + vision processing in single API call. |
| PyPDF2 for extraction | PyMuPDF (fitz) | 2023-2024 | PyMuPDF is 30-45x faster and handles complex layouts. PyPDF2 development stalled. |
| docx library | python-docx | 2020+ | python-docx is actively maintained. Original docx library unmaintained since 2014. |
| Claude API prompt caching: 5-min only | 1-hour cache duration option | Q4 2025 | For repeated document queries (like processing attachments in batches), 1-hour cache reduces costs 90% vs uncached. |

**Deprecated/outdated:**
- **PyPDF2**: Unmaintained, use PyMuPDF instead
- **pdfminer**: Too slow (45x slower than PyMuPDF), use for text mining research only
- **docx library (not python-docx)**: Abandoned in 2014, use python-docx
- **xlrd for .xlsx**: Only supports old .xls format well, use openpyxl for .xlsx
- **Claude API without token budgets**: Cost control is now essential with Vision API ($3-25 per million tokens)

## Open Questions

Things that couldn't be fully resolved:

1. **Optimal scanned PDF detection threshold**
   - What we know: Text-to-filesize ratio < 0.01 is common heuristic. PyMuPDF community discussion suggests checking if image bounding box covers entire page.
   - What's unclear: Best threshold for German business documents (may differ from general PDFs). False positive rate (digital PDF with images incorrectly flagged as scanned).
   - Recommendation: Start with 0.01 threshold. Log detection results for first 100 PDFs and manually review to tune threshold. Consider hybrid: ratio < 0.01 OR image covers >90% of page.

2. **Claude Vision accuracy for German financial amounts**
   - What we know: Claude Vision excels at structured extraction. German number format (1.234,56 EUR) is trainable via examples in prompt.
   - What's unclear: Error rate on real creditor documents. Does Claude confuse Hauptforderung vs Gesamtforderung in complex invoices?
   - Recommendation: Implement confidence scoring based on response structure. Flag extractions with <HIGH confidence for human review. Build validation dataset from first 50 real emails to measure accuracy.

3. **Memory usage on Render 512MB for concurrent jobs**
   - What we know: PyMuPDF + openpyxl read-only mode are memory-efficient. Individual files process fine.
   - What's unclear: Render worker memory profile under load (multiple Dramatiq actors processing simultaneously). Does Redis connection pooling + GCS client + PyMuPDF exceed 512MB when processing 5 PDFs concurrently?
   - Recommendation: Start with 1 Dramatiq worker thread per instance. Monitor memory with Render metrics. Scale horizontally (more workers) not vertically if memory constrained.

4. **GCS download latency impact on job throughput**
   - What we know: GCS client supports streaming. Average download time depends on attachment size and Render network speed.
   - What's unclear: 95th percentile latency for 5MB PDFs. Does batching downloads (all attachments at once) vs sequential improve throughput?
   - Recommendation: Download all attachments for an email in parallel before processing. Use asyncio with httpx for concurrent downloads. Measure and log download times for optimization.

5. **Token budget allocation across attachment types**
   - What we know: User decision sets 100K tokens max per job. PDFs with Claude Vision use ~2000 tokens/page.
   - What's unclear: Should budget be split equally across attachments or allocated dynamically (prioritize email body + PDFs, skip images if budget low)?
   - Recommendation: Process in priority order: 1) Email body (low cost), 2) PDFs (highest information density), 3) DOCX/XLSX (native extraction, low cost), 4) Images (only if >50% budget remains). Implement budget allocation policy in extraction orchestrator.

## Sources

### Primary (HIGH confidence)

- [Claude Vision API Documentation](https://platform.claude.com/docs/en/build-with-claude/vision) - Image size limits, token calculation, supported formats
- [Claude PDF Support Documentation](https://platform.claude.com/docs/en/docs/build-with-claude/pdf-support) - PDF processing requirements, page limits, token estimation (1500-3000 tokens/page for text, image costs additional)
- [PyMuPDF Text Extraction](https://pymupdf.readthedocs.io/en/latest/recipes-text.html) - Official documentation on text extraction methods, table detection with find_tables()
- [PyMuPDF GitHub Discussion #1653](https://github.com/pymupdf/PyMuPDF/discussions/1653) - Scanned PDF detection methods (text-to-filesize ratio < 0.01)
- [python-docx Documentation](https://python-docx.readthedocs.io/) - DOCX extraction API reference
- [openpyxl Optimized Modes](https://openpyxl.readthedocs.io/en/3.1/optimized.html) - Read-only mode for memory efficiency
- [Google Cloud Storage Python Client](https://docs.cloud.google.com/python/docs/reference/storage/latest) - GCS client API for downloads, uploads, cleanup
- [Claude API Pricing (Feb 2026)](https://platform.claude.com/docs/en/about-claude/pricing) - Sonnet 4.5: $3 input / $15 output per million tokens

### Secondary (MEDIUM confidence)

- [PyMuPDF Performance Comparison](https://unstract.com/blog/evaluating-python-pdf-to-text-libraries/) - Benchmark showing PyMuPDF 3x faster than pdftotext, 30-45x faster than PyPDF2
- [James McCaffrey Blog: Detect Scanned PDFs](https://jamesmccaffreyblog.com/2025/10/03/programmatically-determine-if-a-pdf-document-is-scanned-or-digital-using-python/) - Text-to-filesize ratio implementation pattern
- [python-docx GitHub Examples](https://github.com/python-openxml/python-docx/issues/276) - Document order extraction patterns
- [docx2python GitHub](https://github.com/ShayHill/docx2python) - Alternative for structured DOCX extraction
- [Claude API Rate Limits 2026](https://platform.claude.com/docs/en/api/rate-limits) - Token budget enforcement, usage tier limits
- [Render Memory Optimization Patterns](https://dev.to/maigaridavid/pymupdf-a-python-library-that-reduces-the-size-of-pdf-files-1anp) - PyMuPDF compression for memory-constrained environments

### Tertiary (LOW confidence)

- [Confidence Scoring in ML](https://www.mindee.com/blog/how-use-confidence-scores-ml-models) - General ML confidence patterns (not Claude-specific, needs validation for LLM extraction)
- [GCS Best Practices (Community)](https://github.com/GoogleCloudPlatform/python-docs-samples/blob/main/notebooks/rendered/cloud-storage-client-library.md) - Code examples, not official best practices doc

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - PyMuPDF, python-docx, openpyxl, Claude API are industry-standard with official docs
- Architecture: HIGH - Patterns verified from official documentation and community best practices
- Pitfalls: MEDIUM - Based on documented issues and real-world reports, but not all tested in production with German creditor documents
- Cost control: HIGH - Claude API pricing and rate limits from official docs, token budget patterns are standard
- Memory optimization: MEDIUM - openpyxl/PyMuPDF memory patterns documented, but Render 512MB limits not specifically tested

**Research date:** 2026-02-04
**Valid until:** 60 days (Claude API pricing stable, library versions stable, patterns are long-term)
