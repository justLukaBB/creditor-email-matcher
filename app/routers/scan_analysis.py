"""
Scan Analysis Router
Handles analysis of scanned mail (single-page PDFs) uploaded via the admin portal.

Flow:
1. Receive single-page PDF via multipart upload
2. Save to temp file
3. Extract content using PDFExtractor (PyMuPDF for digital, Claude Vision for scanned)
4. Match client by Aktenzeichen via MongoDB
5. Return structured analysis result

Endpoint: POST /api/v1/analyze-scan
Called by: Portal Backend (server/routes/admin-post-upload.js)
"""

import os
import tempfile
from typing import Optional, List

import structlog
from fastapi import APIRouter, File, UploadFile, HTTPException
from pydantic import BaseModel, Field

from app.services.extraction.pdf_extractor import PDFExtractor
from app.services.cost_control import TokenBudgetTracker
from app.services.mongodb_client import MongoDBService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["scan-analysis"])

# Singleton MongoDB service (lazy-initialized)
_mongodb_service = MongoDBService()


# ── Response Models ────────────────────────────────────────────

class ScanAnalysisResponse(BaseModel):
    """Response from analyzing a single scanned page."""
    client_aktenzeichen: Optional[str] = None
    client_name: Optional[str] = None
    client_id: Optional[str] = None
    creditor_name: Optional[str] = None
    creditor_email: Optional[str] = None
    letter_type: Optional[str] = Field(None, description="first or second")
    new_debt_amount: Optional[float] = None
    extraction_confidence: Optional[float] = Field(None, description="0.0-1.0")
    match_status: str = Field(default="no_match", description="auto_matched, needs_review, no_match")
    needs_review: bool = True
    raw_text: Optional[str] = None
    extracted_fields: Optional[dict] = None
    extraction_method: Optional[str] = None
    error: Optional[str] = None


# ── Endpoint ───────────────────────────────────────────────────

@router.post("/analyze-scan", response_model=ScanAnalysisResponse)
async def analyze_scan(file: UploadFile = File(...)):
    """
    Analyze a single-page scanned PDF.

    Receives a PDF (typically one page from a split multi-page scan),
    extracts creditor info, amounts, and attempts client matching.

    Used by the Portal Backend post-upload feature for processing
    scanned mail that arrives by postal service.
    """
    log = logger.bind(filename=file.filename, content_type=file.content_type)

    # Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail=f"Nur PDF-Dateien erlaubt. Erhalten: {file.content_type}"
        )

    # Save to temp file
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        log.info("scan_received", size_bytes=len(content), tmp_path=tmp_path)

        # Extract content using PDFExtractor
        tracker = TokenBudgetTracker()
        extractor = PDFExtractor(token_budget=tracker)
        extraction_result = extractor.extract(tmp_path)

        if extraction_result.error:
            log.warning("extraction_error", error=extraction_result.error)

        # Build response from extraction result
        response = ScanAnalysisResponse(
            extraction_method=extraction_result.extraction_method,
            raw_text=extraction_result.extracted_text,
            error=extraction_result.error,
        )

        # Amount
        if extraction_result.gesamtforderung:
            response.new_debt_amount = extraction_result.gesamtforderung.value
            confidence_map = {"HIGH": 0.95, "MEDIUM": 0.7, "LOW": 0.4}
            response.extraction_confidence = confidence_map.get(
                extraction_result.gesamtforderung.confidence, 0.5
            )

        # Creditor name
        if extraction_result.creditor_name:
            response.creditor_name = extraction_result.creditor_name.value

        # Client name from extraction
        extracted_client_name = None
        if extraction_result.client_name:
            extracted_client_name = extraction_result.client_name.value
            response.client_name = extracted_client_name

        # Extracted fields summary
        response.extracted_fields = {
            "reference_numbers": [],
            "amounts": [extraction_result.gesamtforderung.value] if extraction_result.gesamtforderung else [],
            "components": extraction_result.components,
        }

        # Try to match client via MongoDB
        _try_match_client(response, extracted_client_name, log)

        # Determine letter type heuristic
        response.letter_type = _detect_letter_type(extraction_result.extracted_text or "")

        log.info(
            "scan_analysis_complete",
            match_status=response.match_status,
            creditor_name=response.creditor_name,
            amount=response.new_debt_amount,
            confidence=response.extraction_confidence,
            letter_type=response.letter_type,
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        log.error("scan_analysis_failed", error=str(e), exc_info=True)
        return ScanAnalysisResponse(
            match_status="no_match",
            needs_review=True,
            error=str(e),
        )
    finally:
        # Cleanup temp file
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Helpers ────────────────────────────────────────────────────

def _try_match_client(
    response: ScanAnalysisResponse,
    extracted_client_name: Optional[str],
    log,
):
    """
    Attempt to match the extracted data to a client in MongoDB.
    Uses Aktenzeichen from extracted reference numbers, or client name fallback.
    """
    if not _mongodb_service.is_available():
        log.warning("mongodb_not_available_for_scan_matching")
        return

    try:
        clients_collection = _mongodb_service.db['clients']

        # Try matching by reference numbers (Aktenzeichen patterns)
        reference_numbers = response.extracted_fields.get("reference_numbers", []) if response.extracted_fields else []

        # Also check raw_text for Aktenzeichen patterns
        if response.raw_text:
            import re
            az_patterns = [
                r'(?:Az\.?|Aktenzeichen|AZ)[:\s]*(\d{2,6}[/_]\d{1,5})',
                r'(?:Az\.?|Aktenzeichen|AZ)[:\s]*(\d{4,8})',
                r'(?:unser(?:em?)?\s+Zeichen)[:\s]*(\d{2,6}[/_]\d{1,5})',
            ]
            for pattern in az_patterns:
                matches = re.findall(pattern, response.raw_text, re.IGNORECASE)
                reference_numbers.extend(matches)

            if response.extracted_fields:
                response.extracted_fields["reference_numbers"] = list(set(reference_numbers))

        client = None

        # Try each reference number as Aktenzeichen
        for ref in reference_numbers:
            client = clients_collection.find_one({'aktenzeichen': ref})
            if client:
                response.client_aktenzeichen = ref
                break

            # Try with slash/underscore normalization
            if '/' in ref:
                normalized = ref.replace('/', '_')
                client = clients_collection.find_one({'aktenzeichen': normalized})
                if client:
                    response.client_aktenzeichen = ref
                    break
            elif '_' in ref:
                normalized = ref.replace('_', '/')
                client = clients_collection.find_one({'aktenzeichen': normalized})
                if client:
                    response.client_aktenzeichen = ref
                    break

        # Fallback: try client name
        if not client and extracted_client_name:
            name = extracted_client_name.strip()
            if ',' in name:
                parts = [p.strip() for p in name.split(',', 1)]
                last_name, first_name = parts[0], parts[1] if len(parts) == 2 else (name, "")
            else:
                name_parts = name.split(None, 1)
                first_name = name_parts[0] if name_parts else ""
                last_name = name_parts[1] if len(name_parts) > 1 else ""

            if first_name and last_name:
                client = clients_collection.find_one({
                    'firstName': {'$regex': f'^{re.escape(first_name)}$', '$options': 'i'},
                    'lastName': {'$regex': f'^{re.escape(last_name)}$', '$options': 'i'},
                })

        if client:
            response.client_id = str(client['_id'])
            response.client_name = f"{client.get('firstName', '')} {client.get('lastName', '')}"
            response.client_aktenzeichen = response.client_aktenzeichen or client.get('aktenzeichen')
            response.match_status = "auto_matched"
            response.needs_review = False
            log.info("client_matched", client_id=response.client_id, aktenzeichen=response.client_aktenzeichen)
        else:
            response.match_status = "needs_review" if (response.creditor_name or response.new_debt_amount) else "no_match"
            response.needs_review = True

    except Exception as e:
        log.error("client_matching_failed", error=str(e))
        response.match_status = "no_match"
        response.needs_review = True


def _detect_letter_type(text: str) -> str:
    """
    Heuristic to detect if a scanned letter is a 1. Schreiben (debt statement)
    or 2. Schreiben (settlement response).

    Returns 'first' or 'second'.
    """
    text_lower = text.lower() if text else ""

    second_letter_keywords = [
        "vergleich", "schuldenbereinigung", "einigung", "ratenzahlung",
        "zahlungsvereinbarung", "vergleichsvorschlag", "angenommen",
        "abgelehnt", "akzeptiert", "regulierungsvorschlag",
        "einverstanden", "zustimmung",
    ]

    for keyword in second_letter_keywords:
        if keyword in text_lower:
            return "second"

    return "first"
