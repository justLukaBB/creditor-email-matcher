"""
Storage Services (Phase 3: Multi-Format Document Extraction)

GCS attachment handling with automatic temp file cleanup.
"""

from .gcs_client import GCSAttachmentHandler, FileTooLargeError

__all__ = ["GCSAttachmentHandler", "FileTooLargeError"]
