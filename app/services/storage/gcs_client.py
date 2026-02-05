"""
GCS Attachment Handler (Phase 3: Multi-Format Document Extraction)

Downloads attachments from GCS or HTTPS URLs with automatic temp file cleanup.
Supports both gs://bucket/path and HTTPS URLs (Zendesk attachment URLs).
"""

import os
import tempfile
from contextlib import contextmanager
from typing import Optional, Generator
from urllib.parse import urlparse

import httpx
import structlog
from google.cloud import storage

from app.config import settings


logger = structlog.get_logger(__name__)


class FileTooLargeError(Exception):
    """Raised when file exceeds maximum allowed size."""
    pass


class GCSAttachmentHandler:
    """
    Handle GCS and HTTPS downloads with automatic cleanup.

    Uses context manager pattern to ensure temp files are always cleaned up,
    even when exceptions occur during processing.
    """

    def __init__(self, bucket_name: Optional[str] = None):
        """
        Initialize handler with optional bucket name override.

        Args:
            bucket_name: GCS bucket name. Defaults to settings.gcs_bucket_name.
        """
        self.bucket_name = bucket_name or settings.gcs_bucket_name
        self._client: Optional[storage.Client] = None

    @property
    def client(self) -> storage.Client:
        """Lazy-initialize GCS client."""
        if self._client is None:
            self._client = storage.Client()
        return self._client

    def _get_extension(self, url: str) -> str:
        """Extract file extension from URL, handling query parameters."""
        # Remove query parameters
        clean_url = url.split('?')[0]
        _, ext = os.path.splitext(clean_url)
        return ext if ext else ''

    def _truncate_url_for_logging(self, url: str, max_length: int = 80) -> str:
        """Truncate URL for logging while keeping beginning and end visible."""
        if len(url) <= max_length:
            return url
        half = (max_length - 3) // 2
        return f"{url[:half]}...{url[-half:]}"

    def _parse_gcs_url(self, gcs_url: str) -> tuple[str, str]:
        """
        Parse GCS URL into bucket name and blob path.

        Args:
            gcs_url: URL in format gs://bucket/path/to/file

        Returns:
            Tuple of (bucket_name, blob_path)

        Raises:
            ValueError: If URL is not a valid GCS URL
        """
        if not gcs_url.startswith("gs://"):
            raise ValueError(f"Invalid GCS URL (must start with gs://): {gcs_url}")

        parts = gcs_url[5:].split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid GCS URL (missing path): {gcs_url}")

        bucket_name = parts[0]
        blob_path = parts[1]
        return bucket_name, blob_path

    @contextmanager
    def download_attachment(self, url: str) -> Generator[str, None, None]:
        """
        Download file from GCS or HTTPS URL to temp file with auto-cleanup.

        Supports both:
        - GCS URLs: gs://bucket/path/to/file.pdf
        - HTTPS URLs: https://domain.com/path/to/file.pdf

        Args:
            url: GCS URL (gs://) or HTTPS URL

        Yields:
            Path to downloaded temp file

        Raises:
            ValueError: If URL format is invalid
            Exception: If download fails
        """
        ext = self._get_extension(url)
        fd, temp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)

        log = logger.bind(
            url=self._truncate_url_for_logging(url),
            temp_path=temp_path
        )
        log.info("download_started")

        try:
            if url.startswith("gs://"):
                self._download_from_gcs(url, temp_path)
            else:
                self._download_from_https(url, temp_path)

            file_size = os.path.getsize(temp_path)
            log.info("download_completed", file_size_bytes=file_size)

            yield temp_path
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                log.debug("temp_file_cleaned_up")

    @contextmanager
    def download_from_url(self, url: str) -> Generator[str, None, None]:
        """
        Download file from HTTPS URL to temp file with auto-cleanup.

        This is an alias for download_attachment() for HTTPS URLs specifically.

        Args:
            url: HTTPS URL (Zendesk attachment URL)

        Yields:
            Path to downloaded temp file
        """
        with self.download_attachment(url) as temp_path:
            yield temp_path

    @contextmanager
    def download_with_size_check(
        self,
        url: str,
        max_size_mb: Optional[int] = None
    ) -> Generator[str, None, None]:
        """
        Download file only if under size limit.

        Checks file size before downloading to avoid downloading
        files that are too large for processing (e.g., Claude API 32MB limit).

        Args:
            url: GCS URL (gs://) or HTTPS URL
            max_size_mb: Maximum file size in MB. Defaults to settings.gcs_max_file_size_mb.

        Yields:
            Path to downloaded temp file

        Raises:
            FileTooLargeError: If file exceeds size limit
        """
        max_size = max_size_mb or settings.gcs_max_file_size_mb

        log = logger.bind(
            url=self._truncate_url_for_logging(url),
            max_size_mb=max_size
        )

        # Check size before download
        size_bytes = self._get_file_size(url)
        size_mb = size_bytes / (1024 * 1024)

        if size_mb > max_size:
            log.warning("file_too_large", size_mb=round(size_mb, 2))
            raise FileTooLargeError(
                f"File is {size_mb:.2f}MB, exceeds maximum of {max_size}MB"
            )

        log.debug("size_check_passed", size_mb=round(size_mb, 2))

        with self.download_attachment(url) as temp_path:
            yield temp_path

    def _get_file_size(self, url: str) -> int:
        """
        Get file size without downloading.

        For GCS: Uses blob.size property
        For HTTPS: Uses HEAD request Content-Length header

        Returns:
            File size in bytes
        """
        if url.startswith("gs://"):
            bucket_name, blob_path = self._parse_gcs_url(url)
            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            blob.reload()  # Fetch metadata
            return blob.size or 0
        else:
            # HTTPS: Use HEAD request
            with httpx.Client() as client:
                response = client.head(url, follow_redirects=True)
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                return int(content_length) if content_length else 0

    def _download_from_gcs(self, gcs_url: str, dest_path: str) -> None:
        """Download file from GCS to local path."""
        bucket_name, blob_path = self._parse_gcs_url(gcs_url)

        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(dest_path)

    def _download_from_https(self, url: str, dest_path: str) -> None:
        """Download file from HTTPS URL to local path using streaming."""
        with httpx.Client() as client:
            with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()

                with open(dest_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
