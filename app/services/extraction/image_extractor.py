"""
Image Extractor (Phase 3: Multi-Format Document Extraction)

Extracts data from JPG/PNG image attachments using Claude Vision API.

Key behaviors:
- Uses Claude Vision API for image analysis
- Resizes large images (>5MB) to reduce token usage
- Checks token budget before API calls
- Cleans up resized temp files in finally block
- Returns MEDIUM confidence (images typically lower than PDFs)
"""

import os
import re
import json
import base64
from typing import Optional

import structlog

try:
    from PIL import Image
except ImportError:
    Image = None

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


logger = structlog.get_logger(__name__)


# Claude Vision extraction prompt for images (German - USER DECISION)
IMAGE_EXTRACTION_PROMPT = """Analysiere dieses Bild eines deutschen Glaeubiger-/Inkassodokuments.

Extrahiere die folgenden Informationen, falls sichtbar:
1. Gesamtforderung (Gesamtbetrag) - suche nach Waehrungsbetraegen in EUR
2. Falls kein Gesamtbetrag: Summiere Hauptforderung + Zinsen + Kosten
3. Glaeubiger (Name des Glaeubigerers/der Firma)
4. Schuldner (Name des Schuldners/Kunden)

WICHTIG:
- Akzeptiere Synonyme: "Schulden", "offener Betrag", "Restschuld", "Forderungshoehe"
- Deutsche Zahlenformatierung: 1.234,56 EUR bedeutet 1234.56
- Bei unleserlichen Stellen: null zurueckgeben statt raten

BEISPIELE:
- "Gesamtforderung: 1.234,56 EUR" -> 1234.56
- "Offener Betrag per 01.01.2026: 2.500 EUR" -> 2500.00

Gib NUR valides JSON zurueck:
{
  "gesamtforderung": 1234.56,
  "glaeubiger": "Firmenname",
  "schuldner": "Personenname"
}

Falls die Information nicht sichtbar ist oder das Bild kein relevantes Dokument zeigt:
{"gesamtforderung": null, "glaeubiger": null, "schuldner": null}"""


class ImageExtractor:
    """
    Image content extractor using Claude Vision API.

    Extracts structured data from JPG/PNG images of creditor documents.
    Large images are resized before API call to reduce token usage.

    Usage:
        tracker = TokenBudgetTracker()
        extractor = ImageExtractor(token_budget=tracker)

        result = extractor.extract("/path/to/document.jpg")
        if result.error:
            print(f"Extraction failed: {result.error}")
        else:
            print(f"Gesamtforderung: {result.gesamtforderung}")
    """

    def __init__(
        self,
        token_budget: TokenBudgetTracker,
        claude_client: Optional["Anthropic"] = None,
        max_image_size_kb: int = 5000,  # 5MB max before resize
    ):
        """
        Initialize image extractor.

        Args:
            token_budget: Token budget tracker for Claude API calls
            claude_client: Optional Anthropic client (creates new one if not provided)
            max_image_size_kb: Maximum image size in KB before resizing (default 5MB)
        """
        self.token_budget = token_budget
        self.max_image_size_kb = max_image_size_kb

        # Initialize Claude client lazily
        self._claude_client = claude_client

        logger.debug(
            "image_extractor_initialized",
            max_image_size_kb=self.max_image_size_kb,
            has_claude_client=claude_client is not None,
        )

    @property
    def claude_client(self) -> "Anthropic":
        """Lazy-initialize Claude client on first use."""
        if self._claude_client is None:
            if Anthropic is None:
                raise ImportError(
                    "anthropic package required for Claude Vision. "
                    "Install with: pip install anthropic"
                )
            self._claude_client = Anthropic()
        return self._claude_client

    def extract(self, image_path: str) -> SourceExtractionResult:
        """
        Extract content from image file using Claude Vision.

        Handles:
        - Large image resizing (>5MB)
        - Token budget check before API call
        - Temp file cleanup in finally block

        Args:
            image_path: Absolute path to image file (JPG/PNG)

        Returns:
            SourceExtractionResult with extracted data or error message
        """
        log = logger.bind(image_path=image_path)
        source_name = os.path.basename(image_path)

        result = SourceExtractionResult(
            source_type="image",
            source_name=source_name,
            extraction_method="claude_vision",
            tokens_used=0,
        )

        # Verify file exists
        if not os.path.exists(image_path):
            log.error("image_file_not_found")
            result.error = "file_not_found"
            result.extraction_method = "skipped"
            return result

        # Check PIL is available
        if Image is None:
            log.error("pillow_not_installed")
            result.error = "pillow_not_installed"
            result.extraction_method = "skipped"
            return result

        resized_path = None  # Track if we created a temp file

        try:
            # Check file size and resize if needed
            file_size_kb = os.path.getsize(image_path) / 1024
            if file_size_kb > self.max_image_size_kb:
                log.info(
                    "resizing_large_image",
                    original_size_kb=round(file_size_kb, 1),
                    max_size_kb=self.max_image_size_kb,
                )
                resized_path = self._resize_image(image_path)
                working_path = resized_path
            else:
                working_path = image_path

            # Estimate tokens: ~2000 tokens for typical image
            estimated_tokens = 2000

            # Check token budget BEFORE calling API
            if not self.token_budget.check_budget(estimated_tokens):
                remaining = self.token_budget.remaining()
                log.warning(
                    "token_budget_exceeded",
                    estimated=estimated_tokens,
                    remaining=remaining,
                )
                result.error = (
                    f"token_budget_exceeded: would need {estimated_tokens}, "
                    f"remaining {remaining}"
                )
                result.extraction_method = "skipped"
                return result

            # Determine media type from extension
            ext = os.path.splitext(working_path)[1].lower()
            if ext in [".jpg", ".jpeg"]:
                media_type = "image/jpeg"
            elif ext == ".png":
                media_type = "image/png"
            else:
                # Default to jpeg for unknown extensions
                media_type = "image/jpeg"

            # Read and encode image as base64
            with open(working_path, "rb") as f:
                image_data = base64.standard_b64encode(f.read()).decode("utf-8")

            log.info(
                "calling_claude_vision",
                estimated_tokens=estimated_tokens,
                media_type=media_type,
            )

            # Call Claude Vision API
            message = self.claude_client.messages.create(
                model="claude-sonnet-4-5-20250514",
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {"type": "text", "text": IMAGE_EXTRACTION_PROMPT},
                        ],
                    }
                ],
            )

            # Record token usage
            tokens_used = message.usage.input_tokens + message.usage.output_tokens
            self.token_budget.add_usage(
                message.usage.input_tokens, message.usage.output_tokens
            )
            result.tokens_used = tokens_used

            log.info(
                "claude_vision_response",
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
                total_tokens=tokens_used,
            )

            # Parse response
            return self._parse_response(message.content[0].text, result)

        except Exception as e:
            log.error("image_extraction_failed", error=str(e))
            result.error = f"image_extraction_error: {str(e)}"
            return result

        finally:
            # CRITICAL: Clean up resized temp file if we created one
            if resized_path is not None and os.path.exists(resized_path):
                try:
                    os.unlink(resized_path)
                    log.debug("temp_file_cleaned_up", path=resized_path)
                except OSError as e:
                    log.warning(
                        "temp_file_cleanup_failed", path=resized_path, error=str(e)
                    )

    def _resize_image(self, image_path: str) -> str:
        """
        Resize large image to reduce token usage.

        Resizes to max 1500px on longest side while maintaining aspect ratio.
        Saves to temp file.

        Args:
            image_path: Path to original image

        Returns:
            Path to resized temp file (caller must clean up)
        """
        import tempfile

        img = Image.open(image_path)

        # Calculate new size (max 1500px on longest side)
        max_size = 1500
        ratio = min(max_size / img.width, max_size / img.height)

        if ratio < 1:
            new_size = (int(img.width * ratio), int(img.height * ratio))
            # Use LANCZOS for high-quality downsampling
            img = img.resize(new_size, Image.LANCZOS)

        # Save to temp file with same extension
        _, ext = os.path.splitext(image_path)
        fd, temp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)

        # Save with reasonable quality
        if ext.lower() in [".jpg", ".jpeg"]:
            img.save(temp_path, quality=85)
        else:
            img.save(temp_path)

        img.close()

        logger.debug(
            "image_resized",
            original_size=(img.width if hasattr(img, "width") else "unknown"),
            temp_path=temp_path,
        )

        return temp_path

    def _parse_response(
        self, response_text: str, result: SourceExtractionResult
    ) -> SourceExtractionResult:
        """
        Parse Claude Vision API response JSON.

        Args:
            response_text: Raw response from Claude API
            result: SourceExtractionResult to populate

        Returns:
            Updated SourceExtractionResult with parsed fields
        """
        log = logger.bind(
            source_name=result.source_name, response_length=len(response_text)
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
                    source="image",
                    confidence="MEDIUM",  # Images typically lower confidence than PDFs
                )
                log.info(
                    "gesamtforderung_extracted",
                    value=data["gesamtforderung"],
                    confidence="MEDIUM",
                )

            # Extract creditor name (glaeubiger)
            if data.get("glaeubiger"):
                result.creditor_name = ExtractedEntity(
                    value=data["glaeubiger"],
                    entity_type="creditor_name",
                    confidence="MEDIUM",
                )
                log.info("creditor_extracted", value=data["glaeubiger"])

            # Extract client name (schuldner)
            if data.get("schuldner"):
                result.client_name = ExtractedEntity(
                    value=data["schuldner"],
                    entity_type="client_name",
                    confidence="MEDIUM",
                )
                log.info("client_extracted", value=data["schuldner"])

        except json.JSONDecodeError as e:
            log.error("json_parse_error", error=str(e), response=response_text[:200])
            result.error = f"json_parse_error: {str(e)}"
        except Exception as e:
            log.error("parse_error", error=str(e))
            result.error = f"parse_error: {str(e)}"

        return result


__all__ = ["ImageExtractor", "IMAGE_EXTRACTION_PROMPT"]
