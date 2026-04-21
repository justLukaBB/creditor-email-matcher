"""
Content Extractor Actor

Orchestrates extraction from email body + all attachments:
1. Downloads attachments from GCS/URLs
2. Routes each to appropriate extractor based on format
3. Consolidates results using business rules
4. Returns ConsolidatedExtractionResult
"""

import dramatiq
import structlog
import gc
from typing import List, Optional, Dict, Any

from app.actors import broker
from app.config import settings
from app.models.extraction_result import (
    SourceExtractionResult,
    ConsolidatedExtractionResult,
)
from app.services.extraction import (
    detect_file_format,
    FileFormat,
    EmailBodyExtractor,
    PDFExtractor,
    DOCXExtractor,
    XLSXExtractor,
    ImageExtractor,
    ExtractionConsolidator,
)
from app.services.storage import (
    GCSAttachmentHandler,
    FileTooLargeError,
    sanitize_filename,
    build_attachment_blob_path,
)
from app.services.cost_control import (
    TokenBudgetTracker,
    TokenBudgetExceeded,
    DailyCostCircuitBreaker,
)

logger = structlog.get_logger()


class ContentExtractionService:
    """
    Orchestrates extraction from all sources for an email.

    Handles:
    - Email body text extraction
    - Attachment download and format routing
    - Token budget enforcement
    - Daily cost circuit breaker
    - Result consolidation
    """

    def __init__(self, redis_client=None):
        self.token_budget = TokenBudgetTracker()

        # Initialize circuit breaker if Redis available
        if redis_client:
            self.circuit_breaker = DailyCostCircuitBreaker(redis_client)
        else:
            self.circuit_breaker = None

        # Initialize extractors
        self.email_extractor = EmailBodyExtractor()
        self.pdf_extractor = PDFExtractor(token_budget=self.token_budget)
        self.docx_extractor = DOCXExtractor()
        self.xlsx_extractor = XLSXExtractor()
        self.image_extractor = ImageExtractor(token_budget=self.token_budget)
        self.consolidator = ExtractionConsolidator()
        self.gcs_handler = GCSAttachmentHandler()

    def extract_all(
        self,
        email_body: Optional[str],
        attachment_urls: Optional[List[Dict[str, Any]]],
        kanzlei_id: Optional[str] = None,
        resend_email_id: Optional[str] = None,
    ) -> ConsolidatedExtractionResult:
        """
        Extract from email body and all attachments.

        Args:
            email_body: Cleaned email body text
            attachment_urls: List of attachment metadata dicts with url, filename, content_type, size.
                MUTATED IN PLACE — each dict receives a `permanent_url` key after successful GCS archive.
            kanzlei_id: Tenant ID used to isolate archived attachments per kanzlei.
                None → archived under the "_unassigned" folder.
            resend_email_id: Resend email ID used as the per-email sub-folder in GCS.

        Returns:
            ConsolidatedExtractionResult with merged data
        """
        source_results: List[SourceExtractionResult] = []

        # Check circuit breaker first
        if self.circuit_breaker and self.circuit_breaker.is_open():
            logger.warning("daily_cost_limit_exceeded", action="skipping_extraction")
            return self._make_circuit_breaker_result()

        # 1. Extract from email body
        if email_body:
            try:
                body_result = self.email_extractor.extract(email_body)
                source_results.append(body_result)
                logger.info("email_body_extracted",
                    has_amount=body_result.gesamtforderung is not None)
            except Exception as e:
                logger.error("email_body_extraction_failed", error=str(e))

        # 2. Process attachments in priority order (PDFs first, then DOCX/XLSX, then images)
        if attachment_urls:
            # Sort by priority: PDF > DOCX > XLSX > Images
            priority_order = {
                FileFormat.PDF: 1,
                FileFormat.DOCX: 2,
                FileFormat.XLSX: 3,
                FileFormat.IMAGE_JPG: 4,
                FileFormat.IMAGE_PNG: 4,
            }

            sorted_attachments = sorted(
                attachment_urls,
                key=lambda a: priority_order.get(
                    detect_file_format(a.get("filename", ""), a.get("content_type")),
                    5
                )
            )

            for idx, attachment in enumerate(sorted_attachments):
                # Check if we still have token budget
                if self.token_budget.remaining() < 1000:
                    logger.warning("token_budget_low", remaining=self.token_budget.remaining())
                    # Even if we can't extract further, attempt archive for the remaining
                    # attachments so originals are still preserved. Fail-soft on errors.
                    self._archive_only(
                        attachment=attachment,
                        kanzlei_id=kanzlei_id,
                        resend_email_id=resend_email_id,
                        index=idx,
                    )
                    continue

                result = self._extract_attachment(
                    attachment,
                    kanzlei_id=kanzlei_id,
                    resend_email_id=resend_email_id,
                    index=idx,
                )
                if result:
                    source_results.append(result)

        # 3. Consolidate all results
        consolidated = self.consolidator.consolidate(source_results)

        # 4. Record cost to circuit breaker
        if self.circuit_breaker and consolidated.total_tokens_used > 0:
            cost_usd = self.token_budget.estimate_cost_usd()
            self.circuit_breaker.check_and_record(cost_usd)

        logger.info("extraction_complete",
            sources=len(source_results),
            sources_with_amount=consolidated.sources_with_amount,
            final_amount=consolidated.gesamtforderung,
            confidence=consolidated.confidence,
            tokens_used=consolidated.total_tokens_used)

        # Memory cleanup
        gc.collect()

        return consolidated

    def _extract_attachment(
        self,
        attachment: Dict[str, Any],
        kanzlei_id: Optional[str] = None,
        resend_email_id: Optional[str] = None,
        index: int = 0,
    ) -> Optional[SourceExtractionResult]:
        """Extract from a single attachment and archive the original to GCS."""
        url = attachment.get("url")
        filename = attachment.get("filename", "unknown")
        content_type = attachment.get("content_type")

        if not url:
            logger.warning("attachment_missing_url", filename=filename)
            return None

        file_format = detect_file_format(filename, content_type)

        # Even for UNKNOWN formats and files too large to extract, we still want
        # to preserve the original in the GCS archive (Issue #169).
        if file_format == FileFormat.UNKNOWN:
            logger.info("unsupported_format", filename=filename, content_type=content_type)
            self._archive_only(
                attachment=attachment,
                kanzlei_id=kanzlei_id,
                resend_email_id=resend_email_id,
                index=index,
            )
            return None

        try:
            # Download attachment to temp file. Inside the with-block:
            #   1) upload a copy to GCS archive (best-effort — never blocks extraction)
            #   2) run the extraction pipeline on the temp file
            # Cleanup happens automatically when the with-block exits.
            with self.gcs_handler.download_from_url(url) as temp_path:
                permanent_url = self._upload_attachment(
                    temp_path=temp_path,
                    attachment=attachment,
                    kanzlei_id=kanzlei_id,
                    resend_email_id=resend_email_id,
                    index=index,
                )

                result = self._extract_from_file(temp_path, file_format, filename)
                if result is not None and permanent_url:
                    result.permanent_url = permanent_url
                return result

        except FileTooLargeError as e:
            logger.warning("attachment_too_large", filename=filename, error=str(e))
            # Archive even when extraction is skipped — the original file is still
            # business-critical. Use a separate fresh download since the size-check
            # short-circuited the download_from_url path.
            self._archive_only(
                attachment=attachment,
                kanzlei_id=kanzlei_id,
                resend_email_id=resend_email_id,
                index=index,
            )
            return SourceExtractionResult(
                source_type=file_format.value,
                source_name=filename,
                extraction_method="skipped",
                error="file_too_large",
                permanent_url=attachment.get("permanent_url"),
            )
        except Exception as e:
            logger.error("attachment_download_failed", filename=filename, error=str(e))
            return SourceExtractionResult(
                source_type=file_format.value if file_format != FileFormat.UNKNOWN else "unknown",
                source_name=filename,
                extraction_method="skipped",
                error=str(e)
            )

    def _upload_attachment(
        self,
        temp_path: str,
        attachment: Dict[str, Any],
        kanzlei_id: Optional[str],
        resend_email_id: Optional[str],
        index: int,
    ) -> Optional[str]:
        """
        Upload a downloaded attachment to the GCS archive bucket.

        Mutates `attachment` in place with `permanent_url` on success so the
        caller (email_processor) can forward it to the portal webhook. Returns
        the gs:// URL, or None on failure. Fail-soft: a failed upload must never
        prevent extraction from continuing.
        """
        if not settings.gcs_bucket_name:
            logger.debug("attachment_archive_skipped_no_bucket")
            return None

        raw_filename = attachment.get("filename")
        content_type = attachment.get("content_type")
        safe_name = sanitize_filename(raw_filename, index=index)
        blob_path = build_attachment_blob_path(
            kanzlei_id=kanzlei_id,
            resend_email_id=resend_email_id,
            filename=safe_name,
        )

        try:
            gs_url = self.gcs_handler.upload_file(
                local_path=temp_path,
                dest_blob_path=blob_path,
                content_type=content_type,
                bucket_name=settings.gcs_bucket_name,
            )
            attachment["permanent_url"] = gs_url
            logger.info(
                "attachment_archived",
                filename=raw_filename,
                kanzlei_id=kanzlei_id,
                resend_email_id=resend_email_id,
                gcs_url=gs_url,
            )
            return gs_url
        except Exception as upload_err:
            logger.warning(
                "attachment_archive_failed",
                filename=raw_filename,
                error=str(upload_err),
                kanzlei_id=kanzlei_id,
                resend_email_id=resend_email_id,
            )
            return None

    def _archive_only(
        self,
        attachment: Dict[str, Any],
        kanzlei_id: Optional[str],
        resend_email_id: Optional[str],
        index: int,
    ) -> Optional[str]:
        """
        Download + upload an attachment without running any extraction.

        Used for UNKNOWN formats, over-size files, or when the token budget is
        exhausted — the original still deserves preservation. Fail-soft.
        """
        url = attachment.get("url")
        if not url or not settings.gcs_bucket_name:
            return None

        try:
            with self.gcs_handler.download_from_url(url) as temp_path:
                return self._upload_attachment(
                    temp_path=temp_path,
                    attachment=attachment,
                    kanzlei_id=kanzlei_id,
                    resend_email_id=resend_email_id,
                    index=index,
                )
        except Exception as err:
            logger.warning(
                "attachment_archive_only_failed",
                filename=attachment.get("filename"),
                error=str(err),
            )
            return None

    def _extract_from_file(
        self,
        file_path: str,
        file_format: FileFormat,
        filename: str
    ) -> SourceExtractionResult:
        """Route to appropriate extractor based on format."""
        try:
            if file_format == FileFormat.PDF:
                return self.pdf_extractor.extract(file_path)
            elif file_format == FileFormat.DOCX:
                return self.docx_extractor.extract(file_path)
            elif file_format == FileFormat.XLSX:
                return self.xlsx_extractor.extract(file_path)
            elif file_format in (FileFormat.IMAGE_JPG, FileFormat.IMAGE_PNG):
                mime = "image/png" if file_format == FileFormat.IMAGE_PNG else "image/jpeg"
                return self.image_extractor.extract(file_path, content_type=mime)
            else:
                return SourceExtractionResult(
                    source_type="unknown",
                    source_name=filename,
                    extraction_method="skipped",
                    error=f"unsupported_format: {file_format}"
                )
        except TokenBudgetExceeded as e:
            logger.warning("token_budget_exceeded", filename=filename)
            return SourceExtractionResult(
                source_type=file_format.value,
                source_name=filename,
                extraction_method="skipped",
                error=str(e)
            )
        except Exception as e:
            logger.error("extraction_failed", filename=filename, error=str(e))
            return SourceExtractionResult(
                source_type=file_format.value,
                source_name=filename,
                extraction_method="skipped",
                error=str(e)
            )

    def _make_circuit_breaker_result(self) -> ConsolidatedExtractionResult:
        """Return result when circuit breaker is open."""
        return ConsolidatedExtractionResult(
            gesamtforderung=None,
            client_name=None,
            creditor_name=None,
            confidence="LOW",
            extraction_reason="circuit_breaker_open",
            extraction_method_final="none",
            sources_processed=0,
            sources_with_amount=0,
            total_tokens_used=0,
            source_results=[]
        )


# Dramatiq actor for async extraction
@dramatiq.actor(
    broker=broker,
    max_retries=3,
    min_backoff=15000,  # 15 seconds
    max_backoff=300000,  # 5 minutes
)
def extract_content(
    email_id: int,
    email_body: Optional[str],
    attachment_urls: Optional[List[Dict[str, Any]]],
    intent_result: Optional[Dict[str, Any]] = None,
    kanzlei_id: Optional[str] = None,
    resend_email_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dramatiq actor for content extraction (Agent 2).

    Called by email_processor after Agent 1 intent classification.

    Pipeline checkpoints:
    - Checks for existing agent_2_extraction checkpoint (skip-on-retry)
    - Skips extraction for auto_reply and spam intents
    - Checks Agent 1 confidence threshold before extraction
    - Saves checkpoint after successful extraction

    Args:
        email_id: IncomingEmail.id for logging
        email_body: Cleaned email body text
        attachment_urls: List of attachment metadata
        intent_result: Optional Agent 1 intent classification result with:
            - intent: str (EmailIntent value)
            - confidence: float
            - skip_extraction: bool
            - needs_review: bool

    Returns:
        Dict representation of ConsolidatedExtractionResult + needs_review flag
    """
    logger.info("extract_content_started", email_id=email_id,
                has_intent=bool(intent_result))

    # Lazy imports to avoid circular dependencies
    from app.database import SessionLocal
    from app.services.validation import (
        has_valid_checkpoint,
        get_checkpoint,
        save_checkpoint
    )

    db = SessionLocal()
    try:
        # Step 1: Check for existing checkpoint (skip-on-retry pattern)
        if has_valid_checkpoint(db, email_id, "agent_2_extraction"):
            logger.info("extraction_checkpoint_exists", email_id=email_id)
            checkpoint = get_checkpoint(db, email_id, "agent_2_extraction")

            # Return cached result
            return {
                "gesamtforderung": checkpoint.get("gesamtforderung"),
                "client_name": checkpoint.get("client_name"),
                "creditor_name": checkpoint.get("creditor_name"),
                "confidence": checkpoint.get("confidence"),
                "sources_processed": checkpoint.get("sources_processed", 0),
                "sources_with_amount": checkpoint.get("sources_with_amount", 0),
                "total_tokens_used": checkpoint.get("total_tokens_used", 0),
                "source_results": checkpoint.get("source_results", []),
                "needs_review": checkpoint.get("needs_review", False)
            }

        # Step 2: Check intent and skip extraction for auto_reply/spam
        needs_review = False
        if intent_result:
            intent = intent_result.get("intent", "")
            skip_extraction = intent_result.get("skip_extraction", False)
            agent1_confidence = intent_result.get("confidence", 1.0)

            # Skip extraction for auto_reply and spam (USER DECISION)
            if skip_extraction or intent in ("auto_reply", "spam"):
                logger.info("skipping_extraction_for_intent",
                           email_id=email_id,
                           intent=intent,
                           skip_extraction=skip_extraction)

                # Return minimal result - no extraction needed
                skip_result = {
                    "gesamtforderung": None,
                    "client_name": None,
                    "creditor_name": None,
                    "confidence": "LOW",
                    "sources_processed": 0,
                    "sources_with_amount": 0,
                    "total_tokens_used": 0,
                    "source_results": [],
                    "needs_review": False,
                    "skipped_reason": f"intent_{intent}"
                }

                # Save checkpoint
                save_checkpoint(db, email_id, "agent_2_extraction", skip_result)

                return skip_result

            # Step 3: Check Agent 1 confidence threshold (REQ-PIPELINE-06)
            confidence_threshold = 0.7
            if agent1_confidence < confidence_threshold:
                logger.warning("low_confidence_from_agent1",
                              email_id=email_id,
                              confidence=agent1_confidence,
                              threshold=confidence_threshold)
                needs_review = True

        # Step 4: Run extraction
        # Get Redis client from broker if available
        redis_client = None
        if settings.redis_url:
            import redis
            redis_client = redis.from_url(settings.redis_url)

        service = ContentExtractionService(redis_client=redis_client)
        result = service.extract_all(
            email_body,
            attachment_urls,
            kanzlei_id=kanzlei_id,
            resend_email_id=resend_email_id,
        )

        logger.info("extract_content_completed",
            email_id=email_id,
            amount=result.gesamtforderung,
            confidence=result.confidence,
            tokens=result.total_tokens_used,
            needs_review=needs_review)

        # Step 5: Prepare result dict with needs_review flag
        result_dict = result.model_dump()
        result_dict["needs_review"] = needs_review

        # Collect all extracted texts from attachments for entity extraction
        attachment_texts = []
        for source in result.source_results:
            if source.extracted_text and source.source_type != "email_body":
                attachment_texts.append(source.extracted_text)
        result_dict["attachment_texts"] = attachment_texts

        # Step 6: Save checkpoint after successful extraction
        save_checkpoint(db, email_id, "agent_2_extraction", result_dict)

        return result_dict

    except Exception as e:
        logger.error("extract_content_failed", email_id=email_id, error=str(e))
        raise
    finally:
        db.close()
        gc.collect()
