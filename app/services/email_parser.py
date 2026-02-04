"""
Email Parser & Cleaner Service
Cleans and extracts content from incoming creditor emails
Reduces token count by ~90% (2000 → 200 tokens)
"""

import re
from typing import Dict, Optional
from bs4 import BeautifulSoup
import html2text
from email_reply_parser import EmailReplyParser
import logging

logger = logging.getLogger(__name__)


class EmailParser:
    """
    Parses and cleans incoming emails to extract relevant content
    while removing noise (HTML, signatures, quoted replies, etc.)
    """

    def __init__(self):
        # HTML to text converter
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = True
        self.html_converter.ignore_images = True
        self.html_converter.ignore_emphasis = True
        self.html_converter.body_width = 0  # Don't wrap lines

    def parse_email(self, html_body: Optional[str], text_body: Optional[str]) -> Dict:
        """
        Main entry point for parsing an email

        Args:
            html_body: HTML version of email body
            text_body: Plain text version of email body

        Returns:
            Dict with:
                - cleaned_body: Clean text content
                - token_count_before: Estimated tokens before cleaning
                - token_count_after: Estimated tokens after cleaning
                - creditor_info: Extracted creditor information from signature
        """
        # Prefer HTML body, fall back to text
        raw_content = html_body or text_body or ""

        # Step 1: Convert HTML to text
        if html_body:
            text_content = self._html_to_text(html_body)
        else:
            text_content = text_body or ""

        token_count_before = self._estimate_tokens(text_content)

        # Step 2: Remove Zendesk metadata
        text_content = self._remove_zendesk_metadata(text_content)

        # Step 3: Remove quoted/forwarded content
        text_content = self._remove_quoted_content(text_content)

        # Step 4: Extract creditor info from signature (before removing it)
        creditor_info = self._extract_creditor_from_signature(text_content)

        # Step 5: Remove email signatures
        # DISABLED: Claude needs to see signatures to extract creditor names
        # text_content = self._remove_signature(text_content)

        # Step 6: Remove common email footers/disclaimers
        text_content = self._remove_disclaimers(text_content)

        # Step 7: Clean up whitespace and formatting
        cleaned_body = self._clean_whitespace(text_content)

        token_count_after = self._estimate_tokens(cleaned_body)

        logger.info(
            f"Email parsed: {token_count_before} → {token_count_after} tokens "
            f"({self._calculate_reduction(token_count_before, token_count_after)}% reduction)"
        )

        return {
            "cleaned_body": cleaned_body,
            "token_count_before": token_count_before,
            "token_count_after": token_count_after,
            "creditor_info": creditor_info,
        }

    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML to clean text"""
        try:
            # Use html2text for conversion
            text = self.html_converter.handle(html_content)
            return text
        except Exception as e:
            logger.warning(f"HTML conversion failed: {e}, falling back to BeautifulSoup")
            # Fallback: use BeautifulSoup
            soup = BeautifulSoup(html_content, "lxml")
            return soup.get_text()

    def _remove_zendesk_metadata(self, text: str) -> str:
        """
        Remove Zendesk UI metadata that sometimes gets included in emails

        Examples:
        - "Aktualisiert von: Name, DD. Mmm. YYYY, HH:MM"
        - "Updated by: Name, MMM DD, YYYY, HH:MM AM/PM"
        - Other Zendesk system metadata lines
        """
        # Remove "Aktualisiert von:" / "Updated by:" lines
        text = re.sub(
            r'(?i)^(Aktualisiert von|Updated by):.*?(\d{2}:\d{2}).*?$',
            '',
            text,
            flags=re.MULTILINE
        )

        # Remove horizontal separators that Zendesk adds
        text = re.sub(r'^-{20,}$', '', text, flags=re.MULTILINE)

        return text

    def _remove_quoted_content(self, text: str) -> str:
        """
        Remove quoted/forwarded content using email-reply-parser
        """
        try:
            # EmailReplyParser removes quoted content automatically
            parsed = EmailReplyParser.parse_reply(text)
            return parsed
        except Exception as e:
            logger.warning(f"Email reply parsing failed: {e}")
            # Fallback: manual removal
            return self._manual_quote_removal(text)

    def _manual_quote_removal(self, text: str) -> str:
        """
        Manually remove common quote patterns
        """
        # Remove "On YYYY-MM-DD, ... wrote:" and everything after
        text = re.sub(
            r'(?i)(On\s+\d{4}-\d{2}-\d{2}.*?wrote:).*',
            '',
            text,
            flags=re.DOTALL
        )

        # Remove "Am DD.MM.YYYY schrieb" (German)
        text = re.sub(
            r'(?i)(Am\s+\d{2}\.\d{2}\.\d{4}.*?schrieb).*',
            '',
            text,
            flags=re.DOTALL
        )

        # Remove lines starting with > (quote markers)
        lines = text.split('\n')
        lines = [line for line in lines if not line.strip().startswith('>')]
        text = '\n'.join(lines)

        return text

    def _extract_creditor_from_signature(self, text: str) -> Dict:
        """
        Extract creditor information from email signature

        Returns:
            Dict with:
                - company_name: Extracted company name
                - contact_person: Person name if found
                - phone: Phone number if found
        """
        creditor_info = {
            "company_name": None,
            "contact_person": None,
            "phone": None,
        }

        # Extract company name (often in signature)
        # Pattern: Look for common German company suffixes
        company_pattern = r'([A-ZÄÖÜ][a-zäöüß\s]+(?:GmbH|AG|KG|e\.V\.|eV|Bank|Sparkasse|Versicherung))'
        company_match = re.search(company_pattern, text)
        if company_match:
            creditor_info["company_name"] = company_match.group(1).strip()

        # Extract phone numbers
        phone_pattern = r'(?:Tel\.?|Telefon|Phone)[\s:]*(\+?\d{1,4}[\s\-]?\(?\d{1,5}\)?[\s\-]?\d{1,10})'
        phone_match = re.search(phone_pattern, text, re.IGNORECASE)
        if phone_match:
            creditor_info["phone"] = phone_match.group(1).strip()

        return creditor_info

    def _remove_signature(self, text: str) -> str:
        """
        Remove email signature

        Common signature markers:
        - -- (double dash)
        - Mit freundlichen Grüßen
        - Best regards
        """
        # Remove everything after common signature markers
        signature_markers = [
            r'--\s*$',  # -- followed by newline
            r'(?i)^Mit freundlichen Grüßen',
            r'(?i)^Freundliche Grüße',
            r'(?i)^Best regards',
            r'(?i)^Kind regards',
            r'(?i)^Greetings',
        ]

        lines = text.split('\n')
        for i, line in enumerate(lines):
            for marker in signature_markers:
                if re.match(marker, line.strip()):
                    # Keep everything before this line
                    text = '\n'.join(lines[:i])
                    return text

        return text

    def _remove_disclaimers(self, text: str) -> str:
        """
        Remove common email disclaimers and footers
        """
        disclaimer_patterns = [
            r'(?i)(Diese E-Mail.*?vertraulich).*',
            r'(?i)(This email.*?confidential).*',
            r'(?i)(Disclaimer:).*',
            r'(?i)(Hinweis:.*?Nachricht).*',
            r'(?i)(HINWEIS:.*?bestimmt).*',
        ]

        for pattern in disclaimer_patterns:
            text = re.sub(pattern, '', text, flags=re.DOTALL)

        return text

    def _clean_whitespace(self, text: str) -> str:
        """
        Clean up excessive whitespace and formatting
        """
        # Remove multiple newlines
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)

        # Remove trailing/leading whitespace from each line
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)

        # Remove leading/trailing whitespace from entire text
        text = text.strip()

        return text

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count
        Rough approximation: 1 token ≈ 4 characters
        """
        return len(text) // 4

    def _calculate_reduction(self, before: int, after: int) -> int:
        """Calculate percentage reduction"""
        if before == 0:
            return 0
        return int(((before - after) / before) * 100)


# Global instance
email_parser = EmailParser()
