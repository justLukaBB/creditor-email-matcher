#!/usr/bin/env python
"""
Reprocess Failed Emails

Finds emails that failed due to the existing_amount UnboundLocalError bug
and requeues them for processing.

The bug caused all emails with intent != debt_statement/payment_plan to crash
at the logging step (line 763) because existing_amount was never initialized.

Usage:
    # Dry run (default) - shows what would be reprocessed
    python scripts/reprocess_failed_emails.py

    # Actually reprocess
    python scripts/reprocess_failed_emails.py --execute

    # Reprocess emails since a specific date
    python scripts/reprocess_failed_emails.py --execute --since 2026-03-01
"""

import argparse
import sys
from datetime import datetime

from sqlalchemy import and_

import app.database as database
from app.models.incoming_email import IncomingEmail

database.init_db()


def find_failed_emails(db, since=None):
    """Find emails that failed with the existing_amount bug."""
    filters = [
        IncomingEmail.processing_status == "failed",
    ]
    if since:
        filters.append(IncomingEmail.received_at >= since)

    emails = db.query(IncomingEmail).filter(and_(*filters)).order_by(
        IncomingEmail.received_at.asc()
    ).all()

    return emails


def reprocess_emails(emails, db, execute=False):
    """Reset failed emails and requeue them."""
    from app.actors.email_processor import process_email

    print(f"Found {len(emails)} failed email(s)\n")

    for email in emails:
        print(f"  ID={email.id}  from={email.from_email}  "
              f"subject={email.subject[:60] if email.subject else 'N/A'}  "
              f"received={email.received_at}  "
              f"error={email.processing_error[:80] if email.processing_error else 'N/A'}")

    if not execute:
        print(f"\nDry run complete. Use --execute to reprocess these emails.")
        return 0

    print(f"\nResetting and requeuing {len(emails)} emails...")
    requeued = 0

    for email in emails:
        email.processing_status = "queued"
        email.processing_error = None
        email.started_at = None
        email.completed_at = None
        email.retry_count = 0
        db.flush()

        process_email.send(email_id=email.id)
        requeued += 1
        print(f"  Requeued email ID={email.id}")

    db.commit()
    print(f"\nDone. Requeued {requeued} emails for processing.")
    return requeued


def main():
    parser = argparse.ArgumentParser(description="Reprocess failed emails")
    parser.add_argument("--execute", action="store_true",
                        help="Actually reprocess (default is dry run)")
    parser.add_argument("--since", type=str, default=None,
                        help="Only reprocess emails received after this date (YYYY-MM-DD)")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)

    db = database.SessionLocal()
    try:
        emails = find_failed_emails(db, since=since)
        if not emails:
            print("No failed emails found.")
            return
        reprocess_emails(emails, db, execute=args.execute)
    finally:
        db.close()


if __name__ == "__main__":
    main()
