"""
Extraction Services (Phase 3: Multi-Format Document Extraction)

File format detection and content extraction from PDF, DOCX, XLSX, and images.
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
    # Additional extractors
    "EmailBodyExtractor",
    "DOCXExtractor",
    "XLSXExtractor",
]
