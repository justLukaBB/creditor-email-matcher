"""
Email Notification Service
Sends email notifications when creditor responses are matched
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import logging
import os

logger = logging.getLogger(__name__)


class EmailNotifier:
    """
    Service to send email notifications about matched creditor responses
    """

    def __init__(self):
        """Initialize email configuration"""
        self.notification_email = "glaubiger@scuric.zendesk.com"

        # SMTP configuration from environment
        self.smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.from_email = os.getenv('SMTP_FROM_EMAIL', self.smtp_user)

        # Check if SMTP is configured
        if not self.smtp_user or not self.smtp_password:
            logger.warning("SMTP credentials not configured - email notifications disabled")
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"Email notifier initialized - will send to {self.notification_email}")

    def is_enabled(self) -> bool:
        """Check if email notifications are enabled"""
        return self.enabled

    def send_debt_update_notification(
        self,
        client_name: str,
        creditor_name: str,
        creditor_email: str,
        old_debt_amount: Optional[float],
        new_debt_amount: float,
        side_conversation_id: str,
        zendesk_ticket_id: str,
        reference_numbers: Optional[list] = None,
        confidence_score: float = 1.0
    ) -> bool:
        """
        Send email notification about debt amount update

        Args:
            client_name: Name of the client
            creditor_name: Name of the creditor
            creditor_email: Email of the creditor
            old_debt_amount: Previous debt amount (if known)
            new_debt_amount: New debt amount from creditor response
            side_conversation_id: Zendesk side conversation ID
            zendesk_ticket_id: Main Zendesk ticket ID
            reference_numbers: Reference numbers from response
            confidence_score: Match confidence score (0.0-1.0)

        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.is_enabled():
            logger.info("Email notifications disabled - skipping notification")
            return False

        try:
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'Gläubiger-Antwort: {client_name} - {creditor_name}'
            msg['From'] = self.from_email
            msg['To'] = self.notification_email

            # Build email body
            text_body = self._build_text_body(
                client_name=client_name,
                creditor_name=creditor_name,
                creditor_email=creditor_email,
                old_debt_amount=old_debt_amount,
                new_debt_amount=new_debt_amount,
                side_conversation_id=side_conversation_id,
                zendesk_ticket_id=zendesk_ticket_id,
                reference_numbers=reference_numbers,
                confidence_score=confidence_score
            )

            html_body = self._build_html_body(
                client_name=client_name,
                creditor_name=creditor_name,
                creditor_email=creditor_email,
                old_debt_amount=old_debt_amount,
                new_debt_amount=new_debt_amount,
                side_conversation_id=side_conversation_id,
                zendesk_ticket_id=zendesk_ticket_id,
                reference_numbers=reference_numbers,
                confidence_score=confidence_score
            )

            # Attach both plain text and HTML versions
            part1 = MIMEText(text_body, 'plain', 'utf-8')
            part2 = MIMEText(html_body, 'html', 'utf-8')
            msg.attach(part1)
            msg.attach(part2)

            # Send email via SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info(
                f"✅ Email notification sent - Client: {client_name}, "
                f"Creditor: {creditor_name}, Amount: {new_debt_amount}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send email notification: {e}", exc_info=True)
            return False

    def _build_text_body(
        self,
        client_name: str,
        creditor_name: str,
        creditor_email: str,
        old_debt_amount: Optional[float],
        new_debt_amount: float,
        side_conversation_id: str,
        zendesk_ticket_id: str,
        reference_numbers: Optional[list],
        confidence_score: float
    ) -> str:
        """Build plain text email body"""

        refs = ", ".join(reference_numbers) if reference_numbers else "Keine"
        old_amount_text = f"{old_debt_amount:.2f} EUR" if old_debt_amount else "Unbekannt"
        change_text = ""

        if old_debt_amount and old_debt_amount != new_debt_amount:
            diff = new_debt_amount - old_debt_amount
            change_text = f" (Änderung: {diff:+.2f} EUR)"

        return f"""
Gläubiger-Antwort automatisch zugeordnet

MANDANT
  Name: {client_name}

GLÄUBIGER
  Name: {creditor_name}
  E-Mail: {creditor_email}

FORDERUNGSBETRAG
  Vorheriger Betrag: {old_amount_text}
  Aktueller Betrag: {new_debt_amount:.2f} EUR{change_text}

ZENDESK INFORMATIONEN
  Ticket-ID: {zendesk_ticket_id}
  Side Conversation ID: {side_conversation_id}

REFERENZNUMMERN
  {refs}

MATCHING
  Konfidenz: {confidence_score*100:.0f}%

---
Diese E-Mail wurde automatisch generiert durch das Creditor Email Matcher System.
Der Forderungsbetrag wurde in der Datenbank aktualisiert.
"""

    def _build_html_body(
        self,
        client_name: str,
        creditor_name: str,
        creditor_email: str,
        old_debt_amount: Optional[float],
        new_debt_amount: float,
        side_conversation_id: str,
        zendesk_ticket_id: str,
        reference_numbers: Optional[list],
        confidence_score: float
    ) -> str:
        """Build HTML email body"""

        refs_html = ", ".join(reference_numbers) if reference_numbers else "<em>Keine</em>"
        old_amount_html = f"{old_debt_amount:.2f} EUR" if old_debt_amount else "<em>Unbekannt</em>"

        change_html = ""
        if old_debt_amount and old_debt_amount != new_debt_amount:
            diff = new_debt_amount - old_debt_amount
            color = "green" if diff < 0 else "red"
            change_html = f'<br><small style="color: {color};">(Änderung: {diff:+.2f} EUR)</small>'

        confidence_color = "green" if confidence_score >= 0.8 else "orange" if confidence_score >= 0.6 else "red"

        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4CAF50; color: white; padding: 15px; border-radius: 5px; }}
        .section {{ background-color: #f9f9f9; padding: 15px; margin: 15px 0; border-left: 4px solid #4CAF50; }}
        .section h3 {{ margin-top: 0; color: #4CAF50; }}
        .amount {{ font-size: 1.3em; font-weight: bold; color: #2c3e50; }}
        .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; font-size: 0.9em; color: #777; }}
        .badge {{ display: inline-block; padding: 5px 10px; border-radius: 3px; font-size: 0.9em; }}
        .badge-success {{ background-color: #4CAF50; color: white; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2 style="margin: 0;">✅ Gläubiger-Antwort automatisch zugeordnet</h2>
        </div>

        <div class="section">
            <h3>👤 Mandant</h3>
            <p><strong>{client_name}</strong></p>
        </div>

        <div class="section">
            <h3>🏦 Gläubiger</h3>
            <p><strong>Name:</strong> {creditor_name}<br>
            <strong>E-Mail:</strong> <a href="mailto:{creditor_email}">{creditor_email}</a></p>
        </div>

        <div class="section">
            <h3>💰 Forderungsbetrag</h3>
            <p><strong>Vorheriger Betrag:</strong> {old_amount_html}<br>
            <strong>Aktueller Betrag:</strong> <span class="amount">{new_debt_amount:.2f} EUR</span>{change_html}</p>
        </div>

        <div class="section">
            <h3>📋 Zendesk Informationen</h3>
            <p><strong>Ticket-ID:</strong> {zendesk_ticket_id}<br>
            <strong>Side Conversation ID:</strong> <code>{side_conversation_id}</code></p>
        </div>

        <div class="section">
            <h3>🔢 Referenznummern</h3>
            <p>{refs_html}</p>
        </div>

        <div class="section">
            <h3>🎯 Matching-Qualität</h3>
            <p><span class="badge badge-success" style="background-color: {confidence_color};">Konfidenz: {confidence_score*100:.0f}%</span></p>
        </div>

        <div class="footer">
            <p><em>Diese E-Mail wurde automatisch generiert durch das Creditor Email Matcher System.</em><br>
            Der Forderungsbetrag wurde in der Datenbank aktualisiert.</p>
        </div>
    </div>
</body>
</html>
"""


# Global instance
email_notifier = EmailNotifier()
