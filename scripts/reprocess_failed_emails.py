#!/usr/bin/env python
"""
Reprocess Failed Emails (Local-Safe)

Finds emails that failed due to the existing_amount UnboundLocalError bug
and retriggers them via the Render API (no local Redis needed).

The bug caused all emails with intent != debt_statement/payment_plan to crash
at the logging step because existing_amount was never initialized.

Usage:
    # Dry run (default) - shows what would be reprocessed
    DATABASE_URL="postgresql://..." python scripts/reprocess_failed_emails.py

    # Actually reprocess via Render API
    DATABASE_URL="postgresql://..." python scripts/reprocess_failed_emails.py --execute

    # Reprocess emails since a specific date
    DATABASE_URL="postgresql://..." python scripts/reprocess_failed_emails.py --execute --since 2026-03-01

    # Custom matcher URL
    DATABASE_URL="postgresql://..." MATCHER_API_URL="https://..." python scripts/reprocess_failed_emails.py --execute

Environment Variables:
    DATABASE_URL       - PostgreSQL connection string (required)
    MATCHER_API_URL    - Render matcher URL (default: https://creditor-email-matcher.onrender.com)
"""

import argparse
import os
import sys
import time
from datetime import datetime

import httpx
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker

# Direct DB connection without app bootstrap
DATABASE_URL = os.environ.get("DATABASE_URL")
MATCHER_API_URL = os.environ.get("MATCHER_API_URL", "https://creditor-email-matcher.onrender.com")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is required")
    print("Usage: DATABASE_URL='postgresql://...' python scripts/reprocess_failed_emails.py")
    sys.exit(1)

# Convert for psycopg3 driver
db_url = DATABASE_URL
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Import model after engine setup
from app.models.incoming_email import IncomingEmail


def find_failed_emails(db, since=None, include_misclassified=False):
    """Find emails that failed or were misclassified."""
    statuses = ["failed"]
    if include_misclassified:
        statuses.append("not_creditor_reply")

    filters = [
        IncomingEmail.processing_status.in_(statuses),
    ]
    if since:
        filters.append(IncomingEmail.received_at >= since)

    return db.query(IncomingEmail).filter(and_(*filters)).order_by(
        IncomingEmail.received_at.asc()
    ).all()


def reprocess_via_api(emails, execute=False):
    """Retry failed emails via the Render /api/v1/jobs/{id}/retry endpoint."""
    print(f"Found {len(emails)} failed email(s)\n")

    for email in emails:
        error_preview = (email.processing_error or "N/A")[:80]
        print(f"  ID={email.id:>4}  from={email.from_email:<35}  "
              f"received={str(email.received_at)[:19]}  "
              f"error={error_preview}")

    if not execute:
        print(f"\nDry run complete. Use --execute to reprocess these emails.")
        return 0

    print(f"\nRetriggering {len(emails)} emails via {MATCHER_API_URL}...")
    success = 0
    failed = 0

    with httpx.Client(timeout=30.0) as client:
        for email in emails:
            url = f"{MATCHER_API_URL}/api/v1/jobs/{email.id}/retry"
            try:
                resp = client.post(url)
                if resp.status_code == 200:
                    success += 1
                    print(f"  OK  ID={email.id}")
                else:
                    failed += 1
                    print(f"  FAIL ID={email.id}  status={resp.status_code}  body={resp.text[:100]}")
            except Exception as e:
                failed += 1
                print(f"  ERR  ID={email.id}  {e}")

            # Small delay to not overwhelm the service
            time.sleep(0.5)

    print(f"\nDone. Success: {success}, Failed: {failed}")
    return success


def main():
    parser = argparse.ArgumentParser(description="Reprocess failed emails via Render API")
    parser.add_argument("--execute", action="store_true",
                        help="Actually reprocess (default is dry run)")
    parser.add_argument("--since", type=str, default=None,
                        help="Only reprocess emails received after this date (YYYY-MM-DD)")
    parser.add_argument("--include-misclassified", action="store_true",
                        help="Also reprocess not_creditor_reply emails (likely misclassified)")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)

    db = SessionLocal()
    try:
        emails = find_failed_emails(db, since=since, include_misclassified=args.include_misclassified)
        if not emails:
            print("No failed emails found.")
            return
        reprocess_via_api(emails, execute=args.execute)
    finally:
        db.close()


if __name__ == "__main__":
    main()
