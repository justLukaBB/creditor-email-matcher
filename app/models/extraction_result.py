"""
Extraction Result Models

Pydantic models for structured extraction output from multi-format document processing.
All extractors (PDF, DOCX, XLSX, image, email body) return these consistent structures.

Phase 3 Scope (User Decision - Locked):
- Gesamtforderung (total claim amount)
- client_name
- creditor_name

Extended roadmap fields (Forderungsaufschluesselung, Bankdaten, Ratenzahlung) deferred to Phase 4.
"""

from typing import Optional, Literal, List
from pydantic import BaseModel, ConfigDict, Field


class ExtractedAmount(BaseModel):
    """Represents a monetary amount with extraction metadata."""

    model_config = ConfigDict(from_attributes=True)

    value: float = Field(description="The numeric amount")
    currency: str = Field(default="EUR", description="Currency code")
    raw_text: Optional[str] = Field(
        default=None,
        description="Original text before parsing (e.g., '1.234,56 EUR')"
    )
    source: str = Field(
        description="Where this was found: email_body, pdf, docx, xlsx, image"
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Confidence based on format precision: precise currency format = HIGH, numeric = MEDIUM, missing = LOW"
    )


class ExtractedEntity(BaseModel):
    """Name extraction result for client or creditor."""

    model_config = ConfigDict(from_attributes=True)

    value: str = Field(description="The extracted name")
    entity_type: Literal["client_name", "creditor_name"] = Field(
        description="Type of entity extracted"
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Confidence based on extraction clarity"
    )


class SourceExtractionResult(BaseModel):
    """
    Result from extracting a single source (one attachment or email body).

    Each extractor (PDF, DOCX, XLSX, image, text) returns this structure.
    """

    model_config = ConfigDict(from_attributes=True)

    source_type: Literal["email_body", "pdf", "docx", "xlsx", "image"] = Field(
        description="Type of source processed"
    )
    source_name: Optional[str] = Field(
        default=None,
        description="Filename for attachments, None for email body"
    )
    gesamtforderung: Optional[ExtractedAmount] = Field(
        default=None,
        description="Total claim amount if found"
    )
    components: Optional[dict] = Field(
        default=None,
        description=(
            "Intermediate values for computing Gesamtforderung when no explicit total: "
            "{'hauptforderung': float, 'zinsen': float, 'kosten': float}. "
            "NOT for extended roadmap fields - those are deferred to Phase 4."
        )
    )
    client_name: Optional[ExtractedEntity] = Field(
        default=None,
        description="Extracted client/debtor name"
    )
    creditor_name: Optional[ExtractedEntity] = Field(
        default=None,
        description="Extracted creditor name"
    )
    extraction_method: Literal[
        "pymupdf", "claude_vision", "python_docx", "openpyxl", "text_parsing", "skipped"
    ] = Field(description="Library/method used for extraction (skipped for errors/encrypted)")
    tokens_used: int = Field(
        default=0,
        description="Claude API tokens consumed (0 for native extraction methods)"
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if extraction failed for this source"
    )


class ConsolidatedExtractionResult(BaseModel):
    """
    Final merged result after processing all sources for an email.

    Consolidation rules (User Decisions):
    - gesamtforderung: Highest amount wins across all sources, or 100 EUR default
    - confidence: Weakest link (lowest confidence from any used source)
    - Names: First non-null value found
    """

    model_config = ConfigDict(from_attributes=True)

    gesamtforderung: float = Field(
        description="Final claim amount (highest-wins or 100.0 EUR default)"
    )
    client_name: Optional[str] = Field(
        default=None,
        description="Final client/debtor name"
    )
    creditor_name: Optional[str] = Field(
        default=None,
        description="Final creditor name"
    )
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Weakest link confidence across all sources"
    )
    sources_processed: int = Field(
        description="Total number of sources processed (email body + attachments)"
    )
    sources_with_amount: int = Field(
        description="Number of sources that had an extractable amount"
    )
    total_tokens_used: int = Field(
        description="Total Claude API tokens consumed across all sources"
    )
    source_results: List[SourceExtractionResult] = Field(
        default_factory=list,
        description="Individual extraction results from each source"
    )


# Backward-compatible alias for simpler imports
ExtractionResult = ConsolidatedExtractionResult

__all__ = [
    "ExtractedAmount",
    "ExtractedEntity",
    "SourceExtractionResult",
    "ConsolidatedExtractionResult",
    "ExtractionResult",
]
