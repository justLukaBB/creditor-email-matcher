"""
PDF Extractor (Phase 3: Multi-Format Document Extraction)

Extracts text and structured data from PDF files using:
- PyMuPDF (fitz) for digital PDFs (text-selectable)
- Claude Vision API for scanned PDFs (image-based)

Key behaviors:
- Digital PDFs extract without Claude API calls (zero token cost)
- Scanned PDFs fall back to Claude Vision with token budget check
- PDFs over 10 pages: process first 5 + last 5 pages only
- Encrypted PDFs skip gracefully with error message
"""

import os
import re
import base64
import json
from typing import Optional

import structlog

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

from app.models.extraction_result import (
    SourceExtractionResult,
    ExtractedAmount,
    ExtractedEntity,
)
from app.services.cost_control import TokenBudgetTracker
from app.services.extraction.detector import is_scanned_pdf, is_encrypted_pdf


logger = structlog.get_logger(__name__)


# Claude Vision extraction prompt for scanned PDFs (German - USER DECISION)
EXTRACTION_PROMPT = """Analysiere dieses deutsche Glaeubigerdokument und extrahiere die folgenden Informationen.

WICHTIGE REGELN:
1. Suche nach "Gesamtforderung" (Hauptbetrag) - dies ist der wichtigste Betrag
2. Akzeptiere auch Synonyme: "Forderungshoehe", "offener Betrag", "Gesamtsumme", "Schulden", "Restschuld"
3. Deutsche Zahlenformatierung: 1.234,56 EUR bedeutet 1234.56
4. Wenn keine explizite Gesamtforderung: Summiere "Hauptforderung" + "Zinsen" + "Kosten"

BEISPIELE (typische Formulierungen in Glaeubiger-Antworten):
- "Die Gesamtforderung betraegt 1.234,56 EUR" -> gesamtforderung: 1234.56
- "Offener Betrag: 2.500,00 EUR" -> gesamtforderung: 2500.00
- "Restschuld per 01.01.2026: 3.456,78 EUR" -> gesamtforderung: 3456.78
- "Hauptforderung 1.000 EUR, Zinsen 150,50 EUR, Kosten 84,00 EUR" -> gesamtforderung: 1234.50 (Summe)

EXTRAHIERE:
1. gesamtforderung: Gesamtforderungsbetrag in EUR (nur Zahl, z.B. 1234.56)
2. glaeubiger: Name des Glaeubigerers/der Firma (z.B. "XY Inkasso GmbH", "ABC Bank AG")
3. schuldner: Name des Schuldners/Kunden (z.B. "Max Mustermann", "Maria Mueller")
4. components: Falls Gesamtforderung nicht explizit, gib Aufschluesselung an

Gib NUR valides JSON in diesem exakten Format zurueck:
{
  "gesamtforderung": 1234.56,
  "glaeubiger": "Firmenname",
  "schuldner": "Kundenname",
  "components": {
    "hauptforderung": 1000.00,
    "zinsen": 150.56,
    "kosten": 84.00
  }
}

Wenn ein Feld nicht gefunden wird, nutze null. Fuer gesamtforderung gib null nur zurueck, wenn gar keine Betraege gefunden werden."""


class PDFExtractor:
    """
    PDF content extractor with automatic routing based on PDF type.

    Digital PDFs: Uses PyMuPDF for fast, free extraction
    Scanned PDFs: Falls back to Claude Vision API with token budget check
    Encrypted PDFs: Returns skip result without processing

    Usage:
        tracker = TokenBudgetTracker()
        extractor = PDFExtractor(token_budget=tracker)

        result = extractor.extract("/path/to/document.pdf")
        if result.error:
            print(f"Extraction failed: {result.error}")
        else:
            print(f"Gesamtforderung: {result.gesamtforderung}")
    """

    def __init__(
        self,
        token_budget: TokenBudgetTracker,
        claude_client: Optional["Anthropic"] = None,
        max_pages: int = 10,
    ):
        """
        Initialize PDF extractor.

        Args:
            token_budget: Token budget tracker for Claude API calls
            claude_client: Optional Anthropic client (creates new one if not provided)
            max_pages: Maximum pages to process (default 10). Documents exceeding
                       this limit process first 5 + last 5 pages only.
        """
        self.token_budget = token_budget
        self.max_pages = max_pages

        # Initialize Claude client lazily (only for scanned PDFs)
        self._claude_client = claude_client

        logger.debug(
            "pdf_extractor_initialized",
            max_pages=self.max_pages,
            has_claude_client=claude_client is not None,
        )

    @property
    def claude_client(self) -> "Anthropic":
        """Lazy-initialize Claude client on first use."""
        if self._claude_client is None:
            if Anthropic is None:
                raise ImportError(
                    "anthropic package required for Claude Vision fallback. "
                    "Install with: pip install anthropic"
                )
            self._claude_client = Anthropic()
        return self._claude_client

    def extract(self, pdf_path: str) -> SourceExtractionResult:
        """
        Extract content from PDF file.

        Routes to appropriate extraction method based on PDF type:
        - Encrypted PDFs: Return skip result
        - Scanned PDFs: Use Claude Vision API
        - Digital PDFs: Use PyMuPDF

        Args:
            pdf_path: Absolute path to PDF file

        Returns:
            SourceExtractionResult with extracted data or error message
        """
        log = logger.bind(pdf_path=pdf_path)
        source_name = os.path.basename(pdf_path)

        # Verify file exists
        if not os.path.exists(pdf_path):
            log.error("pdf_file_not_found")
            return SourceExtractionResult(
                source_type="pdf",
                source_name=source_name,
                error="file_not_found",
                extraction_method="skipped",
            )

        # Check if encrypted (password-protected)
        if is_encrypted_pdf(pdf_path):
            log.warning("encrypted_pdf_skipped")
            return SourceExtractionResult(
                source_type="pdf",
                source_name=source_name,
                error="encrypted_pdf_skipped",
                extraction_method="skipped",
            )

        # Route based on PDF type
        if is_scanned_pdf(pdf_path):
            log.info("routing_to_claude_vision", reason="scanned_pdf")
            return self._extract_with_claude_vision(pdf_path)
        else:
            log.info("routing_to_pymupdf", reason="digital_pdf")
            return self._extract_with_pymupdf(pdf_path)

    def _extract_with_pymupdf(self, pdf_path: str) -> SourceExtractionResult:
        """
        Extract text from digital PDF using PyMuPDF.

        Handles page limits: >10 pages processes first 5 + last 5 only.

        Args:
            pdf_path: Path to PDF file

        Returns:
            SourceExtractionResult with parsed entities
        """
        if fitz is None:
            logger.error("pymupdf_not_installed")
            return SourceExtractionResult(
                source_type="pdf",
                source_name=os.path.basename(pdf_path),
                error="pymupdf_not_installed",
                extraction_method="skipped",
            )

        log = logger.bind(pdf_path=pdf_path, method="pymupdf")

        doc = fitz.open(pdf_path)
        try:
            total_pages = len(doc)

            # Handle >max_pages limit: first 5 + last 5 (USER DECISION)
            if total_pages > self.max_pages:
                pages_to_process = list(range(5)) + list(
                    range(total_pages - 5, total_pages)
                )
                log.info(
                    "pdf_truncated",
                    total_pages=total_pages,
                    processing=len(pages_to_process),
                    strategy="first_5_plus_last_5",
                )
            else:
                pages_to_process = list(range(total_pages))

            # Extract text from selected pages
            all_text = []
            for page_num in pages_to_process:
                page = doc[page_num]
                # sort=True for natural reading order
                text = page.get_text("text", sort=True)
                all_text.append(text)

            combined_text = "\n\n".join(all_text)

            log.info(
                "text_extracted",
                total_pages=total_pages,
                pages_processed=len(pages_to_process),
                text_length=len(combined_text),
            )

            # Parse extracted text for entities
            return self._parse_text_for_entities(
                text=combined_text,
                source_name=os.path.basename(pdf_path),
                extraction_method="pymupdf",
            )
        except Exception as e:
            log.error("pymupdf_extraction_failed", error=str(e))
            return SourceExtractionResult(
                source_type="pdf",
                source_name=os.path.basename(pdf_path),
                error=f"pymupdf_error: {str(e)}",
                extraction_method="pymupdf",
            )
        finally:
            doc.close()

    def _parse_text_for_entities(
        self, text: str, source_name: str, extraction_method: str
    ) -> SourceExtractionResult:
        """
        Parse extracted text to find Gesamtforderung, client_name, creditor_name.

        Uses regex patterns for German currency formats:
        - 1.234,56 EUR (German format)
        - EUR 1234.56 (standard format)

        Args:
            text: Extracted text content
            source_name: Filename for result
            extraction_method: Method used for extraction

        Returns:
            SourceExtractionResult with parsed fields
        """
        log = logger.bind(source_name=source_name, text_length=len(text))

        result = SourceExtractionResult(
            source_type="pdf",
            source_name=source_name,
            extraction_method=extraction_method,
            tokens_used=0,  # PyMuPDF = no API cost
        )

        # Search for Gesamtforderung patterns (German currency formats)
        # Patterns: "Gesamtforderung: 1.234,56 EUR", "Gesamt: EUR 1234.56", etc.
        amount_patterns = [
            # Pattern with label followed by amount then currency
            r"[Gg]esamtforderung[:\s]+([0-9][0-9.,]*)\s*(EUR|€)",
            r"[Gg]esamt[:\s]+([0-9][0-9.,]*)\s*(EUR|€)",
            r"[Ff]orderung[:\s]+([0-9][0-9.,]*)\s*(EUR|€)",
            r"[Bb]etrag[:\s]+([0-9][0-9.,]*)\s*(EUR|€)",
            # Pattern with currency before amount
            r"(EUR|€)\s*([0-9][0-9.,]+)",
        ]

        for pattern in amount_patterns:
            match = re.search(pattern, text)
            if match:
                # Handle both pattern types (amount first vs currency first)
                groups = match.groups()
                if groups[0] in ("EUR", "€"):
                    amount_str = groups[1]
                else:
                    amount_str = groups[0]

                try:
                    # German format: 1.234,56 -> replace . with '', then , with .
                    normalized = amount_str.replace(".", "").replace(",", ".")
                    amount_value = float(normalized)

                    # Determine confidence based on format precision
                    # Precise format (e.g., 1234,56 with 2 decimal places) = HIGH
                    has_precise_decimals = re.search(r",\d{2}$", amount_str) is not None

                    result.gesamtforderung = ExtractedAmount(
                        value=amount_value,
                        currency="EUR",
                        raw_text=match.group(0),
                        source="pdf",
                        confidence="HIGH" if has_precise_decimals else "MEDIUM",
                    )
                    log.info(
                        "amount_extracted",
                        value=amount_value,
                        raw_text=match.group(0),
                        pattern=pattern,
                        confidence=result.gesamtforderung.confidence,
                    )
                    break
                except ValueError as e:
                    log.warning("amount_parse_failed", raw=amount_str, error=str(e))
                    continue

        # Note: client_name and creditor_name extraction is basic here
        # Phase 4 (German Document Extraction) will improve name extraction
        # For now, return what we found (amount is primary target)

        if result.gesamtforderung is None:
            log.warning("no_amount_found_in_text")

        return result

    def _extract_with_claude_vision(self, pdf_path: str) -> SourceExtractionResult:
        """
        Extract content from scanned PDF using Claude Vision API.

        Handles:
        - Token budget check before API call
        - Page limits (>10 pages: first 5 + last 5)
        - JSON response parsing

        Args:
            pdf_path: Path to PDF file

        Returns:
            SourceExtractionResult with extracted data
        """
        if fitz is None:
            logger.error("pymupdf_not_installed")
            return SourceExtractionResult(
                source_type="pdf",
                source_name=os.path.basename(pdf_path),
                error="pymupdf_not_installed_for_page_count",
                extraction_method="claude_vision_skipped",
            )

        log = logger.bind(pdf_path=pdf_path, method="claude_vision")
        source_name = os.path.basename(pdf_path)

        doc = fitz.open(pdf_path)
        try:
            total_pages = len(doc)

            # Handle >max_pages limit (USER DECISION: first 5 + last 5)
            if total_pages > self.max_pages:
                pages_to_process = list(range(5)) + list(
                    range(total_pages - 5, total_pages)
                )
                log.info(
                    "pdf_truncated",
                    total_pages=total_pages,
                    processing=len(pages_to_process),
                )
            else:
                pages_to_process = list(range(total_pages))

            # Estimate tokens: ~2000 per page for Claude Vision
            estimated_tokens = len(pages_to_process) * 2000

            # Check token budget BEFORE calling API
            if not self.token_budget.check_budget(estimated_tokens):
                remaining = self.token_budget.remaining()
                log.warning(
                    "token_budget_exceeded",
                    estimated=estimated_tokens,
                    remaining=remaining,
                )
                return SourceExtractionResult(
                    source_type="pdf",
                    source_name=source_name,
                    error=f"token_budget_exceeded: would need {estimated_tokens}, remaining {remaining}",
                    extraction_method="claude_vision_skipped",
                )

            # Close doc before reading file (release file handle)
            doc.close()

            # Read PDF as base64
            with open(pdf_path, "rb") as f:
                pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")

            log.info(
                "calling_claude_vision",
                estimated_tokens=estimated_tokens,
                pdf_size_bytes=len(pdf_data) * 3 // 4,  # Base64 is ~4/3 original size
            )

            # Call Claude Vision API
            message = self.claude_client.messages.create(
                model="claude-sonnet-4-5-20250514",
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
                                    "data": pdf_data,
                                },
                            },
                            {"type": "text", "text": EXTRACTION_PROMPT},
                        ],
                    }
                ],
            )

            # Record token usage
            tokens_used = message.usage.input_tokens + message.usage.output_tokens
            self.token_budget.add_usage(
                message.usage.input_tokens, message.usage.output_tokens
            )

            log.info(
                "claude_vision_response",
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
                total_tokens=tokens_used,
            )

            # Parse response
            return self._parse_claude_response(
                response_text=message.content[0].text,
                source_name=source_name,
                tokens_used=tokens_used,
            )

        except Exception as e:
            log.error("claude_vision_failed", error=str(e))
            return SourceExtractionResult(
                source_type="pdf",
                source_name=source_name,
                error=f"claude_vision_error: {str(e)}",
                extraction_method="claude_vision",
            )
        finally:
            # Ensure doc is closed if still open
            if not doc.is_closed:
                doc.close()

    def _parse_claude_response(
        self, response_text: str, source_name: str, tokens_used: int
    ) -> SourceExtractionResult:
        """
        Parse Claude Vision API response JSON.

        Handles:
        - JSON extraction from markdown code blocks
        - Component summing when no explicit Gesamtforderung
        - Partial extraction (some fields found, others missing)

        Args:
            response_text: Raw response from Claude API
            source_name: Filename for result
            tokens_used: Token count for this API call

        Returns:
            SourceExtractionResult with parsed fields
        """
        log = logger.bind(source_name=source_name, response_length=len(response_text))

        result = SourceExtractionResult(
            source_type="pdf",
            source_name=source_name,
            extraction_method="claude_vision",
            tokens_used=tokens_used,
        )

        try:
            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r"\{[\s\S]*\}", response_text)
            if not json_match:
                log.error("no_json_in_response", response=response_text[:200])
                result.error = "no_json_in_response"
                return result

            data = json.loads(json_match.group())
            log.debug("json_parsed", keys=list(data.keys()))

            # Extract Gesamtforderung
            if data.get("gesamtforderung") is not None:
                result.gesamtforderung = ExtractedAmount(
                    value=float(data["gesamtforderung"]),
                    currency="EUR",
                    source="pdf",
                    confidence="HIGH",  # Claude extraction is generally high confidence
                )
                log.info(
                    "gesamtforderung_extracted",
                    value=data["gesamtforderung"],
                    method="explicit",
                )
            elif data.get("components"):
                # Sum components (USER DECISION)
                components = data["components"]
                total = sum(v for v in components.values() if v is not None)
                if total > 0:
                    result.gesamtforderung = ExtractedAmount(
                        value=total,
                        currency="EUR",
                        source="pdf",
                        confidence="MEDIUM",  # Computed, not explicit
                    )
                    log.info(
                        "gesamtforderung_extracted",
                        value=total,
                        method="computed_from_components",
                        components=components,
                    )
                result.components = components

            # Extract creditor name (glaeubiger)
            if data.get("glaeubiger"):
                result.creditor_name = ExtractedEntity(
                    value=data["glaeubiger"],
                    entity_type="creditor_name",
                    confidence="HIGH" if len(data["glaeubiger"]) > 5 else "MEDIUM",
                )
                log.info("creditor_extracted", value=data["glaeubiger"])

            # Extract client name (schuldner)
            if data.get("schuldner"):
                result.client_name = ExtractedEntity(
                    value=data["schuldner"],
                    entity_type="client_name",
                    confidence="HIGH" if len(data["schuldner"]) > 5 else "MEDIUM",
                )
                log.info("client_extracted", value=data["schuldner"])

        except json.JSONDecodeError as e:
            log.error("json_parse_error", error=str(e), response=response_text[:200])
            result.error = f"json_parse_error: {str(e)}"
        except Exception as e:
            log.error("extraction_error", error=str(e))
            result.error = f"extraction_error: {str(e)}"

        return result


__all__ = ["PDFExtractor", "EXTRACTION_PROMPT"]
