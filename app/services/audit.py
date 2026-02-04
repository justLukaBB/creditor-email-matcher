"""
AuditService for PostgreSQL-MongoDB consistency checking

One-time audit comparing PostgreSQL incoming_emails with MongoDB clients.final_creditor_list data.
Produces detailed mismatch report with recovery plan.
"""

from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
import structlog

from app.models.incoming_email import IncomingEmail
from app.services.mongodb_client import MongoDBService

logger = structlog.get_logger()


class AuditService:
    """
    One-time audit comparing PostgreSQL incoming_emails
    with MongoDB clients.final_creditor_list data.
    """

    def __init__(self, session_factory, mongodb_service: MongoDBService):
        """
        Initialize audit service

        Args:
            session_factory: SQLAlchemy sessionmaker for creating DB sessions
            mongodb_service: MongoDBService instance for MongoDB operations
        """
        self.session_factory = session_factory
        self.mongodb_service = mongodb_service

    def run_full_audit(self, lookback_days: int = 30) -> Dict[str, Any]:
        """
        Compare PostgreSQL and MongoDB data for the last N days.

        Args:
            lookback_days: Number of days to look back for comparison (default 30)

        Returns:
            Audit report dict with summary, mismatches, recovery plan, and health score
        """
        logger.info("audit_started", lookback_days=lookback_days)

        session = self.session_factory()
        try:
            # Calculate audit period
            cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)
            audit_timestamp = datetime.utcnow()

            # Initialize report structure
            report = {
                "summary": {
                    "pg_total_emails": 0,
                    "pg_completed_emails": 0,
                    "pg_matched_emails": 0,
                    "mongo_total_clients": 0,
                    "mongo_clients_with_responses": 0,
                    "audit_period_days": lookback_days,
                    "audit_timestamp": audit_timestamp.isoformat(),
                    "mongodb_available": self.mongodb_service.is_available()
                },
                "mismatches": [],
                "recovery_plan": {
                    "auto_recoverable": 0,
                    "manual_review_needed": 0,
                    "no_action_needed": 0,
                    "stalled_processing": 0,
                    "actions": []
                },
                "health_score": 1.0
            }

            # Check if PostgreSQL has any data
            pg_total = session.query(IncomingEmail).count()
            report["summary"]["pg_total_emails"] = pg_total

            if pg_total == 0:
                logger.info("audit_complete", result="clean", reason="no_pg_records")
                report["health_score"] = 1.0
                return report

            # Check if MongoDB is available
            if not self.mongodb_service.is_available():
                logger.warning("audit_incomplete", reason="mongodb_unavailable")
                report["summary"]["mongodb_available"] = False
                report["health_score"] = 0.0
                return report

            # Get MongoDB statistics
            try:
                clients_collection = self.mongodb_service.db['clients']
                mongo_total_clients = clients_collection.count_documents({})
                mongo_clients_with_responses = clients_collection.count_documents({
                    'final_creditor_list.creditor_response_amount': {'$exists': True, '$ne': None}
                })
                report["summary"]["mongo_total_clients"] = mongo_total_clients
                report["summary"]["mongo_clients_with_responses"] = mongo_clients_with_responses
            except Exception as e:
                logger.error("mongodb_stats_failed", error=str(e))
                report["health_score"] = 0.0
                return report

            # Query PostgreSQL emails in audit period
            pg_emails = session.query(IncomingEmail).filter(
                IncomingEmail.created_at >= cutoff_date
            ).all()

            # Count by processing status
            pg_completed = sum(1 for e in pg_emails if e.processing_status == 'completed')
            pg_matched = sum(1 for e in pg_emails if e.match_status == 'auto_matched')

            report["summary"]["pg_completed_emails"] = pg_completed
            report["summary"]["pg_matched_emails"] = pg_matched

            # Audit each email
            for email in pg_emails:
                self._audit_email(email, report, session)

            # Calculate health score
            total_checked = len(pg_emails)
            total_issues = len(report["mismatches"])

            if total_checked > 0:
                report["health_score"] = max(0.0, (total_checked - total_issues) / total_checked)
            else:
                report["health_score"] = 1.0

            # Count recovery actions
            report["recovery_plan"]["auto_recoverable"] = sum(
                1 for a in report["recovery_plan"]["actions"] if a["action"] == "re-sync"
            )
            report["recovery_plan"]["manual_review_needed"] = sum(
                1 for a in report["recovery_plan"]["actions"] if a["action"] == "manual_review"
            )
            report["recovery_plan"]["stalled_processing"] = sum(
                1 for a in report["recovery_plan"]["actions"] if a["action"] == "stalled"
            )
            report["recovery_plan"]["no_action_needed"] = total_checked - total_issues

            logger.info("audit_complete",
                       total_checked=total_checked,
                       mismatches=total_issues,
                       health_score=report["health_score"])

            return report

        finally:
            session.close()

    def _audit_email(self, email: IncomingEmail, report: Dict[str, Any], session: Session):
        """
        Audit a single email record for PostgreSQL-MongoDB consistency

        Args:
            email: IncomingEmail instance to audit
            report: Audit report dict to update with findings
            session: SQLAlchemy session for additional queries
        """
        # Skip emails that haven't been processed yet or are too recent (< 5 min)
        if email.processing_status in ['received', 'parsed']:
            age_minutes = (datetime.utcnow() - email.created_at).total_seconds() / 60
            if age_minutes > 1440:  # More than 24 hours old
                self._add_mismatch(
                    report,
                    mismatch_type="stalled_processing",
                    email_id=email.id,
                    email=email,
                    severity="medium",
                    details=f"Email stuck in '{email.processing_status}' for {age_minutes/60:.1f} hours",
                    recovery_action="stalled",
                    recovery_reason=f"Processing stalled at {email.processing_status} status"
                )
            return

        # Check failed processing
        if email.processing_status == 'failed':
            self._add_mismatch(
                report,
                mismatch_type="processing_failed",
                email_id=email.id,
                email=email,
                severity="high",
                details=f"Processing failed: {email.processing_error or 'Unknown error'}",
                recovery_action="manual_review",
                recovery_reason="Failed processing needs investigation"
            )
            return

        # For completed, auto-matched emails, check MongoDB consistency
        if email.match_status == 'auto_matched' and email.processing_status == 'completed':
            self._check_mongodb_match(email, report)

    def _check_mongodb_match(self, email: IncomingEmail, report: Dict[str, Any]):
        """
        Check if auto-matched email has corresponding MongoDB record

        Args:
            email: IncomingEmail instance with auto_matched status
            report: Audit report dict to update with findings
        """
        # Extract data from IncomingEmail.extracted_data
        extracted = email.extracted_data or {}
        client_name = extracted.get('client_name')
        client_aktenzeichen = extracted.get('reference_numbers', [None])[0] if extracted.get('reference_numbers') else None
        creditor_name = extracted.get('creditor_name')
        creditor_email = email.from_email
        debt_amount = extracted.get('debt_amount')

        if not client_name or not creditor_name or not debt_amount:
            # Missing extraction data - can't verify MongoDB
            self._add_mismatch(
                report,
                mismatch_type="incomplete_extraction",
                email_id=email.id,
                email=email,
                severity="medium",
                details="Missing client_name, creditor_name, or debt_amount in extracted_data",
                recovery_action="manual_review",
                recovery_reason="Cannot verify MongoDB without complete extraction data"
            )
            return

        # Find MongoDB record
        mongo_match = self._find_mongo_match(client_name, client_aktenzeichen, creditor_name, creditor_email)

        if not mongo_match:
            # PostgreSQL has matched record, MongoDB doesn't
            self._add_mismatch(
                report,
                mismatch_type="pg_matched_no_mongo_record",
                email_id=email.id,
                email=email,
                severity="high",
                details=f"Client: {client_name}, Creditor: {creditor_name}, Amount: {debt_amount}",
                recovery_action="re-sync",
                recovery_reason="PostgreSQL has matched data, MongoDB missing - can auto-fix from extracted_data"
            )
        else:
            # Compare amounts
            mongo_amount = mongo_match.get('creditor_response_amount') or mongo_match.get('claim_amount')

            if mongo_amount and abs(float(mongo_amount) - float(debt_amount)) > 0.01:
                # Amounts differ
                self._add_mismatch(
                    report,
                    mismatch_type="amount_mismatch",
                    email_id=email.id,
                    email=email,
                    severity="high",
                    details=f"PG: {debt_amount}, MongoDB: {mongo_amount}",
                    recovery_action="manual_review",
                    recovery_reason=f"Both databases have data but amounts differ - needs human decision"
                )

    def _find_mongo_match(
        self,
        client_name: str,
        client_aktenzeichen: Optional[str],
        creditor_name: str,
        creditor_email: str
    ) -> Optional[Dict[str, Any]]:
        """
        Search MongoDB for a client with matching creditor in final_creditor_list.
        Uses same matching logic as mongodb_client.py (aktenzeichen > name search).

        Args:
            client_name: Client name from extraction
            client_aktenzeichen: Client case number (optional)
            creditor_name: Creditor name from extraction
            creditor_email: Creditor email address

        Returns:
            The creditor entry dict if found, None otherwise
        """
        if not self.mongodb_service.is_available():
            return None

        try:
            clients_collection = self.mongodb_service.db['clients']

            # Find client (aktenzeichen first, then name)
            client = None

            if client_aktenzeichen:
                client = clients_collection.find_one({'aktenzeichen': client_aktenzeichen})

            if not client and client_name:
                # Try name-based search
                name_parts = client_name.strip().split(None, 1)
                if len(name_parts) == 2:
                    first_name, last_name = name_parts
                    client = clients_collection.find_one({
                        'firstName': {'$regex': f'^{first_name}$', '$options': 'i'},
                        'lastName': {'$regex': f'^{last_name}$', '$options': 'i'}
                    })

            if not client:
                return None

            # Find creditor in final_creditor_list
            creditors = client.get('final_creditor_list', [])

            for cred in creditors:
                # Match by email (primary) or name (fallback)
                email_match = False
                name_match = False

                if creditor_email and cred.get('sender_email'):
                    cred_email = cred.get('sender_email', '').lower().strip()
                    search_email = creditor_email.lower().strip()
                    email_match = (search_email in cred_email) or (cred_email in search_email)

                if creditor_name and cred.get('sender_name'):
                    cred_name = cred.get('sender_name', '').lower().strip()
                    search_name = creditor_name.lower().strip()
                    # Simple contains check
                    name_match = (search_name in cred_name) or (cred_name in search_name)

                if email_match or name_match:
                    return cred

            return None

        except Exception as e:
            logger.error("mongo_match_search_failed", error=str(e))
            return None

    def _add_mismatch(
        self,
        report: Dict[str, Any],
        mismatch_type: str,
        email_id: int,
        email: IncomingEmail,
        severity: str,
        details: str,
        recovery_action: str,
        recovery_reason: str
    ):
        """
        Add a mismatch to the audit report

        Args:
            report: Audit report dict
            mismatch_type: Type of mismatch found
            email_id: PostgreSQL email ID
            email: IncomingEmail instance
            severity: Severity level (high, medium, low)
            details: Description of the mismatch
            recovery_action: Recommended action (re-sync, manual_review, stalled)
            recovery_reason: Explanation for the recovery action
        """
        extracted = email.extracted_data or {}

        mismatch = {
            "type": mismatch_type,
            "email_id": email_id,
            "client_name": extracted.get('client_name', 'Unknown'),
            "creditor_name": extracted.get('creditor_name', 'Unknown'),
            "debt_amount": extracted.get('debt_amount'),
            "severity": severity,
            "details": details,
            "recovery_action": recovery_action
        }

        report["mismatches"].append(mismatch)

        # Add to recovery plan
        report["recovery_plan"]["actions"].append({
            "email_id": email_id,
            "action": recovery_action,
            "reason": recovery_reason
        })
