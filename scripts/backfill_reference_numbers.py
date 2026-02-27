#!/usr/bin/env python3
"""
Backfill reference_number for CreditorInquiry rows missing it.

Looks up each client by name in MongoDB and sets reference_number
to client.aktenzeichen. This fixes old inquiries created before
sync_inquiry_to_matcher.js started sending reference_numbers.

Run:  python scripts/backfill_reference_numbers.py [--dry-run] [--verbose]

Exit codes:
  0 - All rows backfilled (or nothing to do)
  1 - Some rows could not be resolved
  2 - Fatal error (database connection, imports)
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    from app.database import init_db, SessionLocal
    from app.models.creditor_inquiry import CreditorInquiry
    from app.services.mongodb_client import mongodb_service
except ImportError as e:
    print(f"ERROR: Failed to import required modules: {e}")
    print("Make sure you're running from the project root and dependencies are installed.")
    sys.exit(2)


def parse_client_name(client_name: str) -> tuple:
    """Parse client_name into (first_name, last_name)."""
    name = client_name.strip()
    if "," in name:
        # "Scuric, Luka" → first="Luka", last="Scuric"
        parts = [p.strip() for p in name.split(",", 1)]
        return parts[1], parts[0]
    else:
        # "Luka Scuric" → first="Luka", last="Scuric"
        parts = name.split(None, 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return name, ""


def find_aktenzeichen(client_name: str, verbose: bool = False) -> str | None:
    """Look up client in MongoDB and return aktenzeichen."""
    first_name, last_name = parse_client_name(client_name)

    if not first_name or not last_name:
        if verbose:
            print(f"    Could not parse name: '{client_name}'")
        return None

    client = mongodb_service.get_client_by_name(first_name, last_name)
    if client:
        az = client.get("aktenzeichen")
        if verbose:
            print(f"    Found client by name ({first_name} {last_name}) → AZ: {az}")
        return az

    if verbose:
        print(f"    Client not found in MongoDB: {first_name} {last_name}")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Backfill reference_number from MongoDB aktenzeichen"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed output for each row"
    )
    args = parser.parse_args()

    # Initialize databases
    print("Connecting to PostgreSQL...")
    try:
        init_db()
        if SessionLocal is None:
            print("ERROR: PostgreSQL not configured (DATABASE_URL missing).")
            sys.exit(2)
    except Exception as e:
        print(f"ERROR: PostgreSQL connection failed: {e}")
        sys.exit(2)

    print("Connecting to MongoDB...")
    if not mongodb_service.is_available():
        print("ERROR: MongoDB not available (MONGODB_URL missing or unreachable).")
        sys.exit(2)

    # Query rows with missing reference_number
    db = SessionLocal()
    try:
        rows = db.query(CreditorInquiry).filter(
            CreditorInquiry.reference_number.is_(None)
        ).order_by(CreditorInquiry.id).all()

        total = len(rows)
        if total == 0:
            print("Nothing to do — all inquiries already have a reference_number.")
            return

        print(f"Found {total} inquiries with NULL reference_number.\n")

        updated = 0
        not_found = 0
        errors = 0

        for row in rows:
            label = f"  [{row.id}] {row.client_name} ({row.creditor_email})"
            try:
                az = find_aktenzeichen(row.client_name, verbose=args.verbose)

                if az:
                    if args.dry_run:
                        print(f"{label} → would set reference_number = '{az}'")
                    else:
                        row.reference_number = az
                        if args.verbose:
                            print(f"{label} → reference_number = '{az}'")
                    updated += 1
                else:
                    print(f"{label} → client not found in MongoDB")
                    not_found += 1
            except Exception as e:
                print(f"{label} → ERROR: {e}")
                errors += 1

        if not args.dry_run and updated > 0:
            db.commit()
            print(f"\nCommitted {updated} updates to PostgreSQL.")
        elif args.dry_run:
            print(f"\n[DRY RUN] Would update {updated} rows.")

        # Summary
        print(f"\n{'=' * 50}")
        print(f"Total:      {total}")
        print(f"Updated:    {updated}")
        print(f"Not found:  {not_found}")
        print(f"Errors:     {errors}")
        print(f"{'=' * 50}")

        if not_found > 0 or errors > 0:
            sys.exit(1)

    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        sys.exit(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
