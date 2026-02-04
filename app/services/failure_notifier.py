"""
Failure Notification Service
Sends email alerts when jobs permanently fail (after all retries exhausted)
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import structlog

from app.config import settings

logger = structlog.get_logger()


class FailureNotifier:
    """
    Service to send email notifications when jobs permanently fail
    """

    def __init__(self):
        """Load SMTP settings from app configuration"""
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_username = settings.smtp_username
        self.smtp_password = settings.smtp_password
        self.admin_email = settings.admin_email

    def send_failure_notification(
        self,
        email_id: int,
        error: str,
        from_email: str,
        subject: str
    ):
        """
        Send email notification for permanent job failure

        Args:
            email_id: IncomingEmail ID that failed
            error: Error message/reason for failure
            from_email: Original sender email
            subject: Original email subject

        Returns:
            None (logs success/failure)
        """
        # Graceful degradation if SMTP not configured
        if not self.smtp_host:
            logger.warning(
                "smtp_not_configured",
                message="Cannot send failure notification - SMTP host not configured"
            )
            return

        # Fallback for admin email
        recipient = self.admin_email
        if not recipient:
            recipient = "admin@example.com"
            logger.warning(
                "admin_email_not_configured",
                message="Using fallback admin email",
                fallback=recipient
            )

        # Build email message
        msg = MIMEMultipart()
        msg['From'] = self.smtp_username or "noreply@creditor-matcher.com"
        msg['To'] = recipient
        msg['Subject'] = f"[ALERT] Email Processing Failed - ID {email_id}"

        # Build plain text body
        timestamp = datetime.utcnow().isoformat()
        body = f"""
EMAIL PROCESSING PERMANENT FAILURE

Email ID: {email_id}
Original Sender: {from_email}
Original Subject: {subject}
Timestamp: {timestamp}

Error:
{error}

---
View job details: /api/v1/jobs/{email_id}
Retry job: POST /api/v1/jobs/{email_id}/retry
"""

        msg.attach(MIMEText(body, 'plain'))

        # Send email via SMTP
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)

            logger.info(
                "failure_notification_sent",
                email_id=email_id,
                recipient=recipient
            )

        except Exception as e:
            # Do NOT raise - notification failure should not cascade
            logger.error(
                "failure_notification_failed",
                email_id=email_id,
                error=str(e),
                smtp_host=self.smtp_host
            )


# Module-level singleton
failure_notifier = FailureNotifier()


def notify_permanent_failure(email_id: int):
    """
    Standalone function to notify on permanent failure

    Called by the actor's on_failure callback (defined in app/actors/email_processor.py).
    Loads email from database and sends notification.

    Args:
        email_id: IncomingEmail ID that permanently failed

    Returns:
        None (best-effort notification)
    """
    try:
        from app.database import SessionLocal
        from app.models.incoming_email import IncomingEmail

        if SessionLocal is None:
            logger.warning(
                "permanent_failure_notification_skipped",
                email_id=email_id,
                reason="database_not_configured"
            )
            return

        # Load email from database
        db = SessionLocal()
        try:
            email = db.query(IncomingEmail).filter(IncomingEmail.id == email_id).first()

            if not email:
                logger.error(
                    "permanent_failure_notification_failed",
                    email_id=email_id,
                    reason="email_not_found"
                )
                return

            # Send notification
            failure_notifier.send_failure_notification(
                email_id=email_id,
                error=email.processing_error or "Unknown error",
                from_email=email.from_email,
                subject=email.subject or "(no subject)"
            )

            logger.info(
                "permanent_failure_notification_sent",
                email_id=email_id
            )

        finally:
            db.close()

    except Exception as e:
        # Never raise - this is best-effort notification
        logger.error(
            "permanent_failure_notification_exception",
            email_id=email_id,
            error=str(e),
            exc_info=True
        )
