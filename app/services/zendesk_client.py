"""
Zendesk API Client
Handles interactions with Zendesk API for side conversations and ticket management
"""

import httpx
from typing import Optional, List, Dict
from app.config import settings
import logging
import base64

logger = logging.getLogger(__name__)


class ZendeskClient:
    """
    Client for interacting with Zendesk API

    Features:
    - Add emails to side conversations
    - Close/update tickets
    - Add tags
    - Add internal notes
    """

    def __init__(self):
        if not all([settings.zendesk_subdomain, settings.zendesk_email, settings.zendesk_api_token]):
            logger.warning("Zendesk credentials not fully configured")
            self.configured = False
            return

        self.configured = True
        self.base_url = f"https://{settings.zendesk_subdomain}.zendesk.com/api/v2"

        # Create auth header (email/token format)
        credentials = f"{settings.zendesk_email}/token:{settings.zendesk_api_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json"
        }

    async def add_email_to_side_conversation(
        self,
        parent_ticket_id: str,
        side_conversation_id: str,
        email_subject: str,
        email_body: str,
        from_email: str
    ) -> bool:
        """
        Add an incoming email to an existing side conversation

        Args:
            parent_ticket_id: Main ticket ID
            side_conversation_id: Side conversation ID
            email_subject: Subject of the email
            email_body: Body of the email
            from_email: Sender's email

        Returns:
            True if successful, False otherwise
        """
        if not self.configured:
            logger.warning("Zendesk not configured - skipping side conversation update")
            return False

        try:
            url = f"{self.base_url}/tickets/{parent_ticket_id}/side_conversations/{side_conversation_id}/events"

            payload = {
                "message": {
                    "subject": email_subject,
                    "body": email_body,
                    "from": {
                        "email": from_email
                    },
                    "to": [{
                        "email": settings.zendesk_email
                    }]
                }
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()

            logger.info(
                f"Email added to side conversation {side_conversation_id} "
                f"on ticket {parent_ticket_id}"
            )
            return True

        except httpx.HTTPError as e:
            logger.error(f"Failed to add email to side conversation: {e}")
            return False

    async def close_ticket(
        self,
        ticket_id: str,
        note: Optional[str] = None
    ) -> bool:
        """
        Close a Zendesk ticket

        Args:
            ticket_id: Ticket ID to close
            note: Optional internal note to add before closing

        Returns:
            True if successful, False otherwise
        """
        if not self.configured:
            logger.warning("Zendesk not configured - skipping ticket close")
            return False

        try:
            url = f"{self.base_url}/tickets/{ticket_id}"

            payload = {
                "ticket": {
                    "status": "solved"
                }
            }

            if note:
                payload["ticket"]["comment"] = {
                    "body": note,
                    "public": False  # Internal note
                }

            async with httpx.AsyncClient() as client:
                response = await client.put(url, json=payload, headers=self.headers)
                response.raise_for_status()

            logger.info(f"Ticket {ticket_id} closed")
            return True

        except httpx.HTTPError as e:
            logger.error(f"Failed to close ticket {ticket_id}: {e}")
            return False

    async def add_tags_to_ticket(
        self,
        ticket_id: str,
        tags: List[str]
    ) -> bool:
        """
        Add tags to a Zendesk ticket

        Args:
            ticket_id: Ticket ID
            tags: List of tags to add

        Returns:
            True if successful, False otherwise
        """
        if not self.configured:
            logger.warning("Zendesk not configured - skipping tag addition")
            return False

        try:
            url = f"{self.base_url}/tickets/{ticket_id}/tags"

            payload = {
                "tags": tags
            }

            async with httpx.AsyncClient() as client:
                response = await client.put(url, json=payload, headers=self.headers)
                response.raise_for_status()

            logger.info(f"Tags {tags} added to ticket {ticket_id}")
            return True

        except httpx.HTTPError as e:
            logger.error(f"Failed to add tags to ticket {ticket_id}: {e}")
            return False

    async def add_internal_note(
        self,
        ticket_id: str,
        note_body: str
    ) -> bool:
        """
        Add an internal note to a ticket

        Args:
            ticket_id: Ticket ID
            note_body: Note content (supports markdown)

        Returns:
            True if successful, False otherwise
        """
        if not self.configured:
            logger.warning("Zendesk not configured - skipping internal note")
            return False

        try:
            url = f"{self.base_url}/tickets/{ticket_id}"

            payload = {
                "ticket": {
                    "comment": {
                        "body": note_body,
                        "public": False
                    }
                }
            }

            async with httpx.AsyncClient() as client:
                response = await client.put(url, json=payload, headers=self.headers)
                response.raise_for_status()

            logger.info(f"Internal note added to ticket {ticket_id}")
            return True

        except httpx.HTTPError as e:
            logger.error(f"Failed to add internal note to ticket {ticket_id}: {e}")
            return False

    async def process_auto_matched_email(
        self,
        new_ticket_id: str,
        parent_ticket_id: str,
        side_conversation_id: str,
        email_subject: str,
        email_body: str,
        from_email: str,
        confidence_score: float,
        client_name: str,
        creditor_name: str
    ) -> bool:
        """
        Process an auto-matched email (high confidence)

        Steps:
        1. Add email to correct side conversation
        2. Close the new ticket
        3. Add tags
        4. Add internal note with confidence info

        Args:
            new_ticket_id: ID of the newly created ticket
            parent_ticket_id: ID of the original client ticket
            side_conversation_id: ID of the creditor's side conversation
            email_subject: Email subject
            email_body: Email body
            from_email: Sender email
            confidence_score: Match confidence (0.0-1.0)
            client_name: Client name
            creditor_name: Creditor name

        Returns:
            True if successful, False otherwise
        """
        logger.info(
            f"Auto-processing matched email - "
            f"New ticket: {new_ticket_id}, Parent: {parent_ticket_id}, "
            f"Confidence: {confidence_score:.2f}"
        )

        # Step 1: Add to side conversation
        success = await self.add_email_to_side_conversation(
            parent_ticket_id=parent_ticket_id,
            side_conversation_id=side_conversation_id,
            email_subject=email_subject,
            email_body=email_body,
            from_email=from_email
        )

        if not success:
            logger.error("Failed to add email to side conversation")
            return False

        # Step 2: Add internal note with match info
        note = f"""✅ **Automatisch zugeordnet** (Confidence: {confidence_score:.1%})

**Mandant**: {client_name}
**Gläubiger**: {creditor_name}
**Von**: {from_email}

Diese Gläubiger-Antwort wurde automatisch dem ursprünglichen Ticket #{parent_ticket_id} zugeordnet.

_Powered by AI Email Matcher_"""

        await self.add_internal_note(new_ticket_id, note)

        # Step 3: Add tags
        await self.add_tags_to_ticket(
            new_ticket_id,
            ["auto_matched", "creditor_reply", f"confidence_{int(confidence_score * 100)}"]
        )

        # Step 4: Close the new ticket
        await self.close_ticket(
            new_ticket_id,
            note="Automatisch geschlossen - Email wurde Side Conversation zugeordnet"
        )

        logger.info(f"Auto-matched email processing complete for ticket {new_ticket_id}")
        return True

    async def process_review_queue_email(
        self,
        new_ticket_id: str,
        confidence_score: float,
        client_name: Optional[str],
        creditor_name: Optional[str],
        suggested_parent_ticket: str
    ) -> bool:
        """
        Process an email that needs manual review (medium confidence)

        Steps:
        1. Add internal note with suggestion
        2. Add tags for review queue
        3. Keep ticket open for manual review

        Args:
            new_ticket_id: ID of the new ticket
            confidence_score: Match confidence
            client_name: Suggested client name
            creditor_name: Suggested creditor name
            suggested_parent_ticket: Suggested parent ticket ID

        Returns:
            True if successful
        """
        logger.info(
            f"Adding email to review queue - "
            f"Ticket: {new_ticket_id}, Confidence: {confidence_score:.2f}"
        )

        # Add internal note with suggestion
        note = f"""⚠️ **Manuelle Prüfung erforderlich** (Confidence: {confidence_score:.1%})

**Vorgeschlagene Zuordnung:**
- Mandant: {client_name or 'Unbekannt'}
- Gläubiger: {creditor_name or 'Unbekannt'}
- Original-Ticket: #{suggested_parent_ticket}

Bitte prüfen Sie die Zuordnung manuell.

_Powered by AI Email Matcher_"""

        await self.add_internal_note(new_ticket_id, note)

        # Add tags
        await self.add_tags_to_ticket(
            new_ticket_id,
            ["needs_review", "creditor_reply", f"confidence_{int(confidence_score * 100)}"]
        )

        logger.info(f"Email {new_ticket_id} added to review queue")
        return True


# Global instance
zendesk_client = ZendeskClient()
