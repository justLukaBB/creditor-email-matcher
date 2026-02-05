# Phase 3 Plan 6: Extraction Orchestration Summary

**Completed:** 2026-02-05
**Duration:** 4 minutes

## One-Liner

Dramatiq content extractor actor orchestrating email body + attachment extraction with circuit breaker protection.

## What Was Built

### ContentExtractionService Class
Created `app/actors/content_extractor.py` with:
- Service class that orchestrates all Phase 3 extractors
- Initializes: EmailBodyExtractor, PDFExtractor, DOCXExtractor, XLSXExtractor, ImageExtractor
- TokenBudgetTracker for per-job token limits
- DailyCostCircuitBreaker (when Redis available) for daily cost protection
- GCSAttachmentHandler for attachment download

### extract_all() Method
Orchestration flow:
1. Check circuit breaker (fail fast if daily limit exceeded)
2. Extract from email body text
3. Process attachments in priority order (PDF > DOCX > XLSX > images)
4. Route each attachment to appropriate extractor based on format detection
5. Consolidate all results using business rules (highest-amount-wins, 100 EUR default)
6. Record cost to circuit breaker
7. Memory cleanup with gc.collect()

### extract_content Dramatiq Actor
- Thin wrapper around ContentExtractionService
- max_retries=3 with exponential backoff (15s to 5min)
- Gets Redis client from settings for circuit breaker
- Returns ConsolidatedExtractionResult as dict for serialization

### Email Processor Integration
Updated `app/actors/email_processor.py`:
- Added Phase 3 content extraction step after email parsing
- Stores results in `extracted_data` with backward-compatible schema
- Merges Phase 3 results with entity extraction results
- Phase 3 provides: debt_amount (from attachments), client_name, creditor_name
- Entity extraction provides: is_creditor_reply, reference_numbers, summary

## Commits

| Hash | Description |
|------|-------------|
| 37303a2 | Create ContentExtractionService and extract_content Dramatiq actor |
| 5894b9c | Integrate content extractor with email processor |

## Files Changed

### Created
- `app/actors/content_extractor.py` - ContentExtractionService + extract_content actor

### Modified
- `app/actors/__init__.py` - Added content_extractor import
- `app/actors/email_processor.py` - Integrated Phase 3 extraction

## Key Integration Points

```
app/actors/email_processor.py
    |
    +--> ContentExtractionService.extract_all()
              |
              +--> EmailBodyExtractor.extract()
              +--> GCSAttachmentHandler.download_from_url()
              |         |
              |         +--> PDFExtractor.extract()
              |         +--> DOCXExtractor.extract()
              |         +--> XLSXExtractor.extract()
              |         +--> ImageExtractor.extract()
              |
              +--> ExtractionConsolidator.consolidate()
              |
              +--> DailyCostCircuitBreaker.check_and_record()
```

## Backward-Compatible Schema

```python
email.extracted_data = {
    "is_creditor_reply": True,  # Phase 5 intent classification
    "client_name": "...",       # Phase 3 or entity extraction
    "creditor_name": "...",     # Phase 3 or entity extraction
    "debt_amount": 1234.56,     # Phase 3 (from attachments)
    "reference_numbers": [],    # Entity extraction (Phase 4)
    "confidence": 0.7,          # Float 0-1 for compatibility
    "summary": "...",           # Entity extraction
    "extraction_metadata": {
        "sources_processed": 3,
        "sources_with_amount": 2,
        "total_tokens_used": 5000,
        "method": "phase3_extraction"
    }
}
```

## Deviations from Plan

None - plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Merge Phase 3 + entity extraction | Backward compatibility - entity extraction provides is_creditor_reply until Phase 5 adds intent classification |
| Phase 3 debt_amount takes priority | Attachments often contain authoritative amounts; Phase 3 processes all sources |
| Helper functions at module level | _confidence_to_float and _get_redis_client reusable across actor functions |

## Phase 3 Completion Status

This was the final plan (6 of 6) in Phase 3. All plans complete:

| Plan | Name | Status |
|------|------|--------|
| 03-01 | Extraction result models | Complete |
| 03-02 | GCS storage and format detection | Complete |
| 03-03 | PDF extractor | Complete |
| 03-04 | Email/DOCX/XLSX extractors | Complete |
| 03-05 | Image extractor and consolidator | Complete |
| 03-06 | Extraction orchestration | Complete |

## Next Phase Readiness

**Phase 3 Complete.** Ready for Phase 4 (German Text Processing) which will:
- Add reference number extraction (Aktenzeichen, Mandantenreferenz)
- German-specific text normalization
- Enhanced regex patterns for German formats

**Prerequisites met:**
- All extractors return consistent SourceExtractionResult
- Consolidator merges results from any source type
- extracted_data schema supports additional fields (reference_numbers)
