"""
Unit tests for _extract_attachment_texts_for_email — the helper that runs
content extraction inline on the deterministic-match path so 2. Schreiben
counter-offers buried in PDFs/DOCX reach the settlement extractor.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.actors.email_processor import _extract_attachment_texts_for_email


def _make_email(attachment_urls=None, zendesk_webhook_id=None, kanzlei_id="K-1"):
    email = MagicMock()
    email.id = 42
    email.attachment_urls = attachment_urls
    email.zendesk_webhook_id = zendesk_webhook_id
    email.kanzlei_id = kanzlei_id
    return email


class TestExtractAttachmentTextsForEmail:
    def test_returns_none_when_no_attachments(self):
        email = _make_email(attachment_urls=None)
        assert _extract_attachment_texts_for_email(email) is None

    def test_returns_none_when_empty_attachment_list(self):
        email = _make_email(attachment_urls=[])
        assert _extract_attachment_texts_for_email(email) is None

    @patch("app.actors.content_extractor.ContentExtractionService")
    def test_extraction_failure_returns_none_safely(self, mock_service_cls):
        mock_service = MagicMock()
        mock_service.extract_all.side_effect = RuntimeError("boom")
        mock_service_cls.return_value = mock_service

        email = _make_email(
            attachment_urls=[{"url": "https://x/y.pdf", "filename": "y.pdf"}],
        )
        result = _extract_attachment_texts_for_email(email)
        assert result is None  # fail-soft

    @patch("app.actors.content_extractor.ContentExtractionService")
    def test_extracts_text_from_attachments(self, mock_service_cls):
        # Build fake source results: one body source (filtered out) + two attachments
        body_source = MagicMock()
        body_source.extracted_text = "email body text"
        body_source.source_type = "email_body"

        att1 = MagicMock()
        att1.extracted_text = "Wir bieten 3500 EUR"
        att1.source_type = "pdf"

        att2 = MagicMock()
        att2.extracted_text = "Bestätigung Vergleichsangebot"
        att2.source_type = "docx"

        consolidated = MagicMock()
        consolidated.source_results = [body_source, att1, att2]

        mock_service = MagicMock()
        mock_service.extract_all.return_value = consolidated
        mock_service_cls.return_value = mock_service

        email = _make_email(
            attachment_urls=[{"url": "https://x/y.pdf", "filename": "y.pdf"}],
        )
        result = _extract_attachment_texts_for_email(email)

        assert result == ["Wir bieten 3500 EUR", "Bestätigung Vergleichsangebot"]
        # Body text must NOT leak in — settlement extractor receives body separately.
        assert "email body text" not in (result or [])

    @patch("app.actors.content_extractor.ContentExtractionService")
    def test_returns_none_when_no_attachment_text_extracted(self, mock_service_cls):
        # All attachments failed extraction — nothing useful to feed the LLM
        body_source = MagicMock()
        body_source.extracted_text = "email body"
        body_source.source_type = "email_body"

        consolidated = MagicMock()
        consolidated.source_results = [body_source]

        mock_service = MagicMock()
        mock_service.extract_all.return_value = consolidated
        mock_service_cls.return_value = mock_service

        email = _make_email(
            attachment_urls=[{"url": "https://x/y.pdf", "filename": "y.pdf"}],
        )
        result = _extract_attachment_texts_for_email(email)
        assert result is None
