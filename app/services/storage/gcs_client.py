"""
GCS Attachment Handler (Phase 3: Multi-Format Document Extraction)

Downloads attachments from GCS or HTTPS URLs with automatic temp file cleanup.
Supports both gs://bucket/path and HTTPS URLs (Zendesk attachment URLs).

Also handles persistent archiving of creditor email attachments (Issue #169):
uploads downloaded attachments into the tenant-isolated archive bucket so
originals remain available after Resend's short-lived URLs expire.
"""

import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from typing import Optional, Generator

import httpx
import structlog
from google.cloud import storage

from app.config import settings
from app.services.monitoring.circuit_breakers import get_gcs_breaker, CircuitBreakerError


logger = structlog.get_logger(__name__)


class FileTooLargeError(Exception):
    """Raised when file exceeds maximum allowed size."""
    pass


# Characters we keep verbatim in a blob filename. Everything else becomes "_".
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_FILENAME_LEN = 200


def sanitize_filename(filename: Optional[str], index: int = 0) -> str:
    """
    Produce a GCS-safe filename from untrusted input.

    Resend delivers attachment filenames that can contain path separators,
    non-ASCII characters, or be missing entirely. We normalize them before
    using them as a blob name so they can never escape the tenant folder
    or collide with the object-path separator.

    Args:
        filename: Raw filename from Resend (may be None/empty/malicious).
        index: Positional index of the attachment (used when name is empty).

    Returns:
        A filename containing only [A-Za-z0-9._-], max 200 chars, never empty.
    """
    if not filename:
        return f"attachment_{index}"

    # Unicode → ASCII-ish. NFKD decomposes diacritics so we keep the base letter.
    normalized = unicodedata.normalize("NFKD", filename)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Strip any path segments defensively — filename should be a leaf.
    leaf = os.path.basename(ascii_only.replace("\\", "/"))

    # Replace disallowed chars with underscores.
    cleaned = _SAFE_FILENAME_RE.sub("_", leaf).strip("._")

    if not cleaned:
        return f"attachment_{index}"

    if len(cleaned) > _MAX_FILENAME_LEN:
        stem, ext = os.path.splitext(cleaned)
        keep = _MAX_FILENAME_LEN - len(ext)
        cleaned = stem[: max(keep, 1)] + ext

    return cleaned


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

        # Wrap GCS download with circuit breaker
        breaker = get_gcs_breaker()
        try:
            breaker.call(blob.download_to_filename, dest_path)
        except CircuitBreakerError:
            logger.error("gcs_circuit_open", gcs_url=gcs_url)
            raise  # Let caller handle retry

    def _download_from_https(self, url: str, dest_path: str) -> None:
        """Download file from HTTPS URL to local path using streaming."""
        with httpx.Client() as client:
            with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()

                with open(dest_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)

    def upload_file(
        self,
        local_path: str,
        dest_blob_path: str,
        content_type: Optional[str] = None,
        bucket_name: Optional[str] = None,
    ) -> str:
        """
        Upload a local file into the archive bucket and return its gs:// URL.

        Used by the creditor attachment archive flow (Issue #169). The caller
        is responsible for building a tenant-safe blob path (see
        `build_attachment_blob_path`).

        Args:
            local_path: Path to the file on disk.
            dest_blob_path: Path INSIDE the bucket (no leading slash).
            content_type: Optional MIME type to store alongside the blob.
            bucket_name: Override target bucket. Defaults to self.bucket_name.

        Returns:
            gs://{bucket}/{dest_blob_path}

        Raises:
            ValueError: If the bucket name is missing or the blob path is empty.
            Exception: Propagated from the GCS client on upload failure.
        """
        target_bucket = bucket_name or self.bucket_name
        if not target_bucket:
            raise ValueError("No GCS bucket configured for attachment archive")
        if not dest_blob_path or dest_blob_path.startswith("/"):
            raise ValueError(f"Invalid dest_blob_path: {dest_blob_path!r}")

        bucket = self.client.bucket(target_bucket)
        blob = bucket.blob(dest_blob_path)
        if content_type:
            blob.content_type = content_type

        log = logger.bind(
            local_path=local_path,
            gcs_path=f"gs://{target_bucket}/{dest_blob_path}",
            content_type=content_type,
        )
        log.info("attachment_upload_started")

        breaker = get_gcs_breaker()
        try:
            breaker.call(blob.upload_from_filename, local_path, content_type=content_type)
        except CircuitBreakerError:
            log.error("gcs_circuit_open_on_upload")
            raise

        gs_url = f"gs://{target_bucket}/{dest_blob_path}"
        log.info("attachment_upload_completed", gcs_url=gs_url)
        return gs_url


def build_attachment_blob_path(
    kanzlei_id: Optional[str],
    resend_email_id: Optional[str],
    filename: str,
    *,
    prefix: Optional[str] = None,
    unassigned_folder: Optional[str] = None,
) -> str:
    """
    Construct the tenant-isolated blob path for a creditor attachment.

    Layout:
        {prefix}/{kanzlei_id | _unassigned_folder}/{resend_email_id}/{filename}

    - `kanzlei_id` falsy → attachment lands under `_unassigned_folder`.
    - `resend_email_id` falsy → falls back to `no_email_id` bucket so we still
      preserve the file (should only happen for out-of-band testing).
    - `filename` must already be sanitized by the caller.
    """
    base_prefix = (prefix or settings.gcs_attachments_prefix or "creditor-attachments").strip("/")
    unassigned = unassigned_folder or settings.gcs_attachments_unassigned_folder or "_unassigned"

    tenant = kanzlei_id.strip() if isinstance(kanzlei_id, str) and kanzlei_id.strip() else unassigned
    email_scope = resend_email_id.strip() if isinstance(resend_email_id, str) and resend_email_id.strip() else "no_email_id"

    # Tenant segments must never contain slashes — guard defensively.
    tenant = tenant.replace("/", "_")
    email_scope = email_scope.replace("/", "_")

    return f"{base_prefix}/{tenant}/{email_scope}/{filename}"
