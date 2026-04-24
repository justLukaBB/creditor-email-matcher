"""
Scan Analysis Router
Handles analysis of scanned mail (single-page PDFs) uploaded via the admin portal.

Flow:
1. Receive single-page PDF via multipart upload
2. Save to temp file
3. Extract content using PDFExtractor (PyMuPDF for digital, Claude Vision for scanned)
4. Match client by Aktenzeichen via MongoDB
5. If client matched: look up creditor in final_creditor_list
6. Return structured analysis result

Endpoint: POST /api/v1/analyze-scan
Called by: Portal Backend (server/routes/admin-post-upload.js)
"""

import os
import re
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

_mongodb_service = MongoDBService()


# ── Response Model ─────────────────────────────────────────────

class ScanAnalysisResponse(BaseModel):
    """Response from analyzing a single scanned page."""
    # Client match
    client_aktenzeichen: Optional[str] = None
    client_name: Optional[str] = None
    client_id: Optional[str] = None

    # Creditor (extracted from document)
    creditor_name: Optional[str] = None
    creditor_email: Optional[str] = None

    # Creditor match from final_creditor_list
    matched_creditor_name: Optional[str] = Field(None, description="Existing creditor name from client's list")
    matched_creditor_id: Optional[str] = Field(None, description="Creditor entry ID from final_creditor_list")
    previous_amount: Optional[float] = Field(None, description="Previous claim_amount from creditor list")

    # Extracted data
    letter_type: Optional[str] = Field(None, description="first or second")
    new_debt_amount: Optional[float] = None
    extraction_confidence: Optional[float] = Field(None, description="0.0-1.0")

    # Status
    match_status: str = Field(default="no_match", description="auto_matched, needs_review, no_match")
    needs_review: bool = True

    # Settlement (only for letter_type == "second")
    settlement_status: Optional[str] = Field(None, description="accepted, declined, counter_offer, inquiry, no_clear_response")
    settlement_confidence: Optional[float] = Field(None, description="0.0-1.0")
    settlement_counter_offer_amount: Optional[float] = None

    # Raw data
    raw_text: Optional[str] = None
    extracted_fields: Optional[dict] = None
    extraction_method: Optional[str] = None
    error: Optional[str] = None


# ── Endpoint ───────────────────────────────────────────────────

@router.post("/analyze-scan", response_model=ScanAnalysisResponse)
async def analyze_scan(file: UploadFile = File(...), kanzlei_id: Optional[str] = None):
    """
    Analyze a single-page scanned PDF.
    Extracts creditor info, amounts, matches client and existing creditor.

    Args:
        file: Single-page PDF
        kanzlei_id: Optional tenant filter — restricts client matching to this kanzlei
    """
    log = logger.bind(filename=file.filename, content_type=file.content_type, kanzlei_id=kanzlei_id)

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail=f"Nur PDF-Dateien erlaubt. Erhalten: {file.content_type}")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        log.info("scan_received", size_bytes=len(content), tmp_path=tmp_path)

        # Extract content
        tracker = TokenBudgetTracker()
        extractor = PDFExtractor(token_budget=tracker)
        extraction_result = extractor.extract(tmp_path)

        if extraction_result.error:
            log.warning("extraction_error", error=extraction_result.error)

        # Build response
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

        # Match client + creditor in MongoDB (scoped to kanzlei if provided)
        _try_match_client_and_creditor(response, extracted_client_name, log, kanzlei_id=kanzlei_id)

        # Determine letter type
        response.letter_type = _detect_letter_type(extraction_result.extracted_text or "")

        # For 2. Anschreiben: classify settlement decision via Claude Haiku
        if response.letter_type == "second" and extraction_result.extracted_text:
            try:
                from app.services.settlement_extractor import settlement_extractor
                settlement_result = settlement_extractor.extract(
                    email_body=extraction_result.extracted_text,
                    from_email=response.creditor_name or "unknown",
                    subject=None,
                    attachment_texts=None,
                )
                response.settlement_status = settlement_result.settlement_decision.value if hasattr(settlement_result.settlement_decision, 'value') else str(settlement_result.settlement_decision)
                response.settlement_confidence = settlement_result.confidence
                response.settlement_counter_offer_amount = settlement_result.counter_offer_amount
            except Exception as e:
                log.warning("settlement_extraction_failed_in_scan", error=str(e))

        log.info(
            "scan_analysis_complete",
            match_status=response.match_status,
            creditor_name=response.creditor_name,
            matched_creditor=response.matched_creditor_name,
            amount=response.new_debt_amount,
            previous_amount=response.previous_amount,
            confidence=response.extraction_confidence,
            letter_type=response.letter_type,
            settlement_status=response.settlement_status,
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        log.error("scan_analysis_failed", error=str(e), exc_info=True)
        return ScanAnalysisResponse(match_status="no_match", needs_review=True, error=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Helpers ────────────────────────────────────────────────────

def _try_match_client_and_creditor(
    response: ScanAnalysisResponse,
    extracted_client_name: Optional[str],
    log,
    kanzlei_id: Optional[str] = None,
):
    """
    1. Match client by Aktenzeichen or name (scoped to kanzlei if provided)
    2. If client found: search final_creditor_list for matching creditor
    3. Populate response with matched data
    """
    if not _mongodb_service.is_available():
        log.warning("mongodb_not_available_for_scan_matching")
        return

    try:
        clients_collection = _mongodb_service.db['clients']

        # Tenant filter — only match clients belonging to this kanzlei
        tenant_filter = {'kanzleiId': kanzlei_id} if kanzlei_id else {}

        # ── Step 1: Find Aktenzeichen in extracted text ────────
        reference_numbers = response.extracted_fields.get("reference_numbers", []) if response.extracted_fields else []

        if response.raw_text:
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

        # ── Step 2: Find client (tenant-scoped) ───────────────
        client = None

        for ref in reference_numbers:
            client = clients_collection.find_one({'aktenzeichen': ref, **tenant_filter})
            if client:
                response.client_aktenzeichen = ref
                break
            # Slash/underscore normalization
            if '/' in ref:
                client = clients_collection.find_one({'aktenzeichen': ref.replace('/', '_'), **tenant_filter})
                if client:
                    response.client_aktenzeichen = ref
                    break
            elif '_' in ref:
                client = clients_collection.find_one({'aktenzeichen': ref.replace('_', '/'), **tenant_filter})
                if client:
                    response.client_aktenzeichen = ref
                    break

        # Fallback: client name
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
                    **tenant_filter,
                })

        if not client:
            response.match_status = "needs_review" if (response.creditor_name or response.new_debt_amount) else "no_match"
            response.needs_review = True
            return

        # ── Step 3: Populate client data ───────────────────────
        response.client_id = str(client['_id'])
        response.client_name = f"{client.get('firstName', '')} {client.get('lastName', '')}"
        response.client_aktenzeichen = response.client_aktenzeichen or client.get('aktenzeichen')

        # ── Step 4: Find matching creditor in final_creditor_list
        creditor_list = client.get('final_creditor_list') or []
        extracted_creditor = (response.creditor_name or "").lower().strip()

        matched_creditor = None
        if extracted_creditor and creditor_list:
            matched_creditor = _find_creditor_in_list(extracted_creditor, creditor_list, log)

        if matched_creditor:
            response.matched_creditor_name = (
                matched_creditor.get('glaeubiger_name')
                or matched_creditor.get('sender_name')
                or matched_creditor.get('actual_creditor')
            )
            response.matched_creditor_id = matched_creditor.get('id')
            response.previous_amount = (
                matched_creditor.get('current_debt_amount')
                or matched_creditor.get('claim_amount')
            )
            response.match_status = "auto_matched"
            response.needs_review = False
            log.info(
                "creditor_matched",
                client_id=response.client_id,
                matched_creditor=response.matched_creditor_name,
                previous_amount=response.previous_amount,
            )
        else:
            # Client found but creditor not in list — still auto_matched but flag for review
            response.match_status = "auto_matched"
            response.needs_review = len(creditor_list) > 0  # Review if list exists but no match
            log.info(
                "client_matched_no_creditor",
                client_id=response.client_id,
                aktenzeichen=response.client_aktenzeichen,
                extracted_creditor=extracted_creditor,
                creditor_list_size=len(creditor_list),
            )

    except Exception as e:
        log.error("client_matching_failed", error=str(e))
        response.match_status = "no_match"
        response.needs_review = True


def _find_creditor_in_list(extracted_name: str, creditor_list: list, log) -> Optional[dict]:
    """
    Fuzzy match extracted creditor name against final_creditor_list entries.
    Checks sender_name, glaeubiger_name, glaeubigervertreter_name, actual_creditor.
    """
    extracted_words = set(w.lower() for w in extracted_name.split() if len(w) > 2)

    best_match = None
    best_score = 0

    for creditor in creditor_list:
        # Check all name fields
        names_to_check = [
            creditor.get('sender_name', ''),
            creditor.get('glaeubiger_name', ''),
            creditor.get('glaeubigervertreter_name', ''),
            creditor.get('actual_creditor', ''),
        ]

        for name in names_to_check:
            if not name:
                continue
            name_lower = name.lower().strip()

            # Exact substring match
            if extracted_name in name_lower or name_lower in extracted_name:
                log.debug("creditor_exact_substring", extracted=extracted_name, matched=name)
                return creditor

            # Word overlap score
            name_words = set(w.lower() for w in name.split() if len(w) > 2)
            if not name_words or not extracted_words:
                continue

            overlap = len(extracted_words & name_words)
            score = overlap / max(len(extracted_words), len(name_words))

            if score > best_score and score >= 0.4:
                best_score = score
                best_match = creditor

    if best_match:
        log.debug("creditor_fuzzy_match", score=best_score, extracted=extracted_name)

    return best_match


def _detect_letter_type(text: str) -> str:
    """
    Heuristic to detect if a scanned letter is a 1. Schreiben (debt statement)
    or 2. Schreiben (settlement response).
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
