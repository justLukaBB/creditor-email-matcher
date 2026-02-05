"""
File Format Detector (Phase 3: Multi-Format Document Extraction)

Detects file format from extension and MIME type, and determines
whether PDFs are scanned (need Claude Vision) or digital (use PyMuPDF).
"""

import os
from enum import Enum
from typing import Optional

import structlog

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None  # Handle gracefully if PyMuPDF not installed


logger = structlog.get_logger(__name__)


class FileFormat(str, Enum):
    """Supported file formats for extraction."""
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    IMAGE_JPG = "image_jpg"
    IMAGE_PNG = "image_png"
    UNKNOWN = "unknown"


# Formats we can process
SUPPORTED_FORMATS = {
    FileFormat.PDF,
    FileFormat.DOCX,
    FileFormat.XLSX,
    FileFormat.IMAGE_JPG,
    FileFormat.IMAGE_PNG,
}

# Map file extensions to formats
EXTENSION_MAP = {
    ".pdf": FileFormat.PDF,
    ".docx": FileFormat.DOCX,
    ".xlsx": FileFormat.XLSX,
    ".jpg": FileFormat.IMAGE_JPG,
    ".jpeg": FileFormat.IMAGE_JPG,
    ".png": FileFormat.IMAGE_PNG,
}

# Map MIME types to formats
MIME_TYPE_MAP = {
    "application/pdf": FileFormat.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": FileFormat.DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": FileFormat.XLSX,
    "image/jpeg": FileFormat.IMAGE_JPG,
    "image/png": FileFormat.IMAGE_PNG,
}


def detect_file_format(
    filename: str,
    content_type: Optional[str] = None
) -> FileFormat:
    """
    Detect file format from filename extension and optional MIME type.

    Priority: MIME type > extension

    Args:
        filename: Name of the file (with extension)
        content_type: Optional MIME type from Content-Type header

    Returns:
        Detected FileFormat enum value
    """
    log = logger.bind(filename=filename, content_type=content_type)

    # First, try MIME type if provided
    if content_type:
        # Handle MIME types with parameters (e.g., "application/pdf; charset=utf-8")
        mime_base = content_type.split(';')[0].strip().lower()
        if mime_base in MIME_TYPE_MAP:
            detected = MIME_TYPE_MAP[mime_base]
            log.debug("format_detected_from_mime", format=detected.value)
            return detected

    # Fall back to extension
    _, ext = os.path.splitext(filename.lower())
    if ext in EXTENSION_MAP:
        detected = EXTENSION_MAP[ext]
        log.debug("format_detected_from_extension", format=detected.value)
        return detected

    log.warning("unknown_format")
    return FileFormat.UNKNOWN


def is_scanned_pdf(pdf_path: str, threshold: float = 0.01) -> bool:
    """
    Detect if a PDF is scanned (image-based) vs digitally generated.

    Uses text-to-filesize ratio heuristic:
    - If text length / file size < threshold, likely scanned
    - Encrypted PDFs return True (need Claude Vision to read)

    Performance optimization: Only samples first 5 pages if document is large.

    Args:
        pdf_path: Path to PDF file
        threshold: Ratio threshold (default 0.01 = 1% text content)

    Returns:
        True if PDF appears to be scanned or encrypted
        False if PDF has sufficient extractable text
    """
    if fitz is None:
        logger.error("pymupdf_not_installed")
        return True  # Fallback to Claude Vision if PyMuPDF not available

    log = logger.bind(pdf_path=pdf_path, threshold=threshold)

    try:
        doc = fitz.open(pdf_path)

        try:
            # Check for encryption first
            if doc.is_encrypted:
                log.info("pdf_is_encrypted")
                return True

            # Get file size
            file_size = os.path.getsize(pdf_path)
            if file_size == 0:
                log.warning("pdf_empty_file")
                return True

            # Sample pages for large documents (performance optimization)
            total_pages = len(doc)
            if total_pages > 5:
                pages_to_sample = range(5)  # First 5 pages only
            else:
                pages_to_sample = range(total_pages)

            # Extract text from sampled pages
            total_text = ""
            for page_num in pages_to_sample:
                page = doc[page_num]
                total_text += page.get_text()

            # Calculate ratio based on sampled pages
            # Scale file_size estimate if we only sampled partial document
            if total_pages > 5:
                estimated_file_per_page = file_size / total_pages
                estimated_sampled_size = estimated_file_per_page * 5
            else:
                estimated_sampled_size = file_size

            text_ratio = len(total_text) / estimated_sampled_size if estimated_sampled_size > 0 else 0

            is_scanned = text_ratio < threshold

            log.info(
                "scanned_pdf_detection",
                total_pages=total_pages,
                pages_sampled=len(list(pages_to_sample)),
                text_length=len(total_text),
                file_size=file_size,
                text_ratio=round(text_ratio, 4),
                is_scanned=is_scanned
            )

            return is_scanned

        finally:
            doc.close()

    except Exception as e:
        log.error("scanned_detection_failed", error=str(e))
        # Return True to use Claude Vision as fallback
        return True


def is_encrypted_pdf(pdf_path: str) -> bool:
    """
    Check if a PDF is password-protected/encrypted.

    Args:
        pdf_path: Path to PDF file

    Returns:
        True if PDF is encrypted, False otherwise
    """
    if fitz is None:
        logger.error("pymupdf_not_installed")
        return False  # Can't check, assume not encrypted

    log = logger.bind(pdf_path=pdf_path)

    try:
        doc = fitz.open(pdf_path)
        try:
            is_encrypted = doc.is_encrypted
            log.debug("encryption_check", is_encrypted=is_encrypted)
            return is_encrypted
        finally:
            doc.close()

    except Exception as e:
        log.error("encryption_check_failed", error=str(e))
        return False


def get_pdf_page_count(pdf_path: str) -> int:
    """
    Get the number of pages in a PDF.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Number of pages, or 0 if unable to read
    """
    if fitz is None:
        logger.error("pymupdf_not_installed")
        return 0

    log = logger.bind(pdf_path=pdf_path)

    try:
        doc = fitz.open(pdf_path)
        try:
            page_count = len(doc)
            log.debug("page_count_retrieved", page_count=page_count)
            return page_count
        finally:
            doc.close()

    except Exception as e:
        log.error("page_count_failed", error=str(e))
        return 0
