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

__all__ = [
    "detect_file_format",
    "is_scanned_pdf",
    "is_encrypted_pdf",
    "get_pdf_page_count",
    "FileFormat",
    "SUPPORTED_FORMATS",
]
