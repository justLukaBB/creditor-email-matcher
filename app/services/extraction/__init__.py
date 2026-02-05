"""
Extraction Services (Phase 3: Multi-Format Document Extraction)

File format detection and content extraction from PDF, DOCX, XLSX, and images.
Consolidator merges results from all sources using business rules.
"""

from .detector import (
    detect_file_format,
    is_scanned_pdf,
    is_encrypted_pdf,
    get_pdf_page_count,
    FileFormat,
    SUPPORTED_FORMATS,
)
from .pdf_extractor import PDFExtractor, EXTRACTION_PROMPT
from .email_body_extractor import EmailBodyExtractor
from .docx_extractor import DOCXExtractor
from .xlsx_extractor import XLSXExtractor
from .image_extractor import ImageExtractor, IMAGE_EXTRACTION_PROMPT
from .consolidator import ExtractionConsolidator

__all__ = [
    # Format detection
    "detect_file_format",
    "is_scanned_pdf",
    "is_encrypted_pdf",
    "get_pdf_page_count",
    "FileFormat",
    "SUPPORTED_FORMATS",
    # PDF extraction
    "PDFExtractor",
    "EXTRACTION_PROMPT",
    # Image extraction
    "ImageExtractor",
    "IMAGE_EXTRACTION_PROMPT",
    # Additional extractors
    "EmailBodyExtractor",
    "DOCXExtractor",
    "XLSXExtractor",
    # Consolidation
    "ExtractionConsolidator",
]
