#!/usr/bin/env python3
"""
One-time data consistency audit between PostgreSQL and MongoDB.

Run: python scripts/audit_consistency.py [--lookback-days 30]

Produces human-readable report of mismatches and recovery plan.
Saves JSON report to scripts/audit_report_{timestamp}.json.

Exit codes:
  0 - Healthy (health_score >= 0.95)
  1 - Issues found (health_score < 0.95)
  2 - Audit failed (database connection error)
"""

import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    from app.database import init_db, SessionLocal
    from app.services.mongodb_client import mongodb_service
    from app.services.audit import AuditService
except ImportError as e:
    print(f"ERROR: Failed to import required modules: {e}")
    print("Make sure you're running from the project root and dependencies are installed.")
    sys.exit(2)


def format_report(report: dict) -> str:
    """
    Format audit report as human-readable text

    Args:
        report: Audit report dict from AuditService

    Returns:
        Formatted report string
    """
    lines = []
    lines.append("=" * 80)
    lines.append("POSTGRESQL-MONGODB CONSISTENCY AUDIT REPORT")
    lines.append("=" * 80)
    lines.append("")

    # Summary section
    summary = report["summary"]
    lines.append("SUMMARY")
    lines.append("-" * 80)
    lines.append(f"Audit Period:              {summary['audit_period_days']} days")
    lines.append(f"Audit Timestamp:           {summary['audit_timestamp']}")
    lines.append(f"MongoDB Available:         {'Yes' if summary['mongodb_available'] else 'No'}")
    lines.append("")
    lines.append(f"PostgreSQL Total Emails:   {summary['pg_total_emails']}")
    lines.append(f"PostgreSQL Completed:      {summary['pg_completed_emails']}")
    lines.append(f"PostgreSQL Auto-Matched:   {summary['pg_matched_emails']}")
    lines.append("")
    lines.append(f"MongoDB Total Clients:     {summary['mongo_total_clients']}")
    lines.append(f"MongoDB With Responses:    {summary['mongo_clients_with_responses']}")
    lines.append("")

    # Health score
    health_score = report["health_score"]
    health_status = "HEALTHY" if health_score >= 0.95 else "NEEDS ATTENTION"
    health_icon = "✓" if health_score >= 0.95 else "✗"

    lines.append(f"Overall Health Score:      {health_score:.2%} [{health_icon} {health_status}]")
    lines.append("")

    # Mismatches section
    mismatches = report["mismatches"]
    lines.append("MISMATCHES FOUND")
    lines.append("-" * 80)

    if not mismatches:
        lines.append("No mismatches found. Databases are consistent.")
        lines.append("")
    else:
        lines.append(f"Total Mismatches: {len(mismatches)}")
        lines.append("")

        # Group by type
        by_type = {}
        for m in mismatches:
            mtype = m["type"]
            if mtype not in by_type:
                by_type[mtype] = []
            by_type[mtype].append(m)

        for mtype, items in by_type.items():
            lines.append(f"\n{mtype.upper().replace('_', ' ')} ({len(items)} found)")
            lines.append("  " + "-" * 76)

            for item in items[:5]:  # Show first 5 of each type
                lines.append(f"  Email ID:       {item['email_id']}")
                lines.append(f"  Client:         {item['client_name']}")
                lines.append(f"  Creditor:       {item['creditor_name']}")
                if item['debt_amount']:
                    lines.append(f"  Amount:         {item['debt_amount']}")
                lines.append(f"  Severity:       {item['severity'].upper()}")
                lines.append(f"  Details:        {item['details']}")
                lines.append(f"  Recovery:       {item['recovery_action']}")
                lines.append("")

            if len(items) > 5:
                lines.append(f"  ... and {len(items) - 5} more")
                lines.append("")

    # Recovery plan section
    recovery = report["recovery_plan"]
    lines.append("RECOVERY PLAN")
    lines.append("-" * 80)
    lines.append(f"Auto-Recoverable (re-sync):        {recovery['auto_recoverable']}")
    lines.append(f"Manual Review Needed:              {recovery['manual_review_needed']}")
    lines.append(f"Stalled Processing:                {recovery['stalled_processing']}")
    lines.append(f"No Action Needed (consistent):     {recovery['no_action_needed']}")
    lines.append("")

    if recovery['auto_recoverable'] > 0:
        lines.append("AUTO-RECOVERABLE ACTIONS:")
        lines.append("These can be fixed by reconciliation service automatically:")
        auto_actions = [a for a in recovery['actions'] if a['action'] == 're-sync']
        for action in auto_actions[:10]:  # Show first 10
            lines.append(f"  - Email {action['email_id']}: {action['reason']}")
        if len(auto_actions) > 10:
            lines.append(f"  ... and {len(auto_actions) - 10} more")
        lines.append("")

    if recovery['manual_review_needed'] > 0:
        lines.append("MANUAL REVIEW REQUIRED:")
        lines.append("These need human investigation:")
        manual_actions = [a for a in recovery['actions'] if a['action'] == 'manual_review']
        for action in manual_actions[:10]:  # Show first 10
            lines.append(f"  - Email {action['email_id']}: {action['reason']}")
        if len(manual_actions) > 10:
            lines.append(f"  ... and {len(manual_actions) - 10} more")
        lines.append("")

    lines.append("=" * 80)

    return "\n".join(lines)


def main():
    """Main audit script entry point"""
    parser = argparse.ArgumentParser(
        description="Audit PostgreSQL-MongoDB consistency for creditor email processing"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Number of days to look back for audit (default: 30)"
    )
    args = parser.parse_args()

    print("Initializing database connections...")

    # Initialize PostgreSQL
    try:
        init_db()
        if SessionLocal is None:
            print("ERROR: PostgreSQL not configured. Set DATABASE_URL environment variable.")
            sys.exit(2)
    except Exception as e:
        print(f"ERROR: Failed to connect to PostgreSQL: {e}")
        sys.exit(2)

    # Check MongoDB availability
    if not mongodb_service.is_available():
        print("WARNING: MongoDB not available. Audit will be limited.")
        print("Set MONGODB_URL environment variable to enable full audit.")
        print("")

    # Run audit
    print(f"Running consistency audit (lookback: {args.lookback_days} days)...")
    print("")

    try:
        audit_service = AuditService(SessionLocal, mongodb_service)
        report = audit_service.run_full_audit(lookback_days=args.lookback_days)
    except Exception as e:
        print(f"ERROR: Audit failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)

    # Print formatted report
    formatted = format_report(report)
    print(formatted)

    # Save JSON report
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    report_path = project_root / "scripts" / f"audit_report_{timestamp}.json"

    try:
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON report saved to: {report_path}")
    except Exception as e:
        print(f"\nWARNING: Failed to save JSON report: {e}")

    # Exit code based on health score
    health_score = report["health_score"]
    if health_score >= 0.95:
        print("\n✓ AUDIT PASSED: Databases are healthy")
        sys.exit(0)
    else:
        print(f"\n✗ AUDIT FAILED: Health score {health_score:.2%} below threshold (95%)")
        sys.exit(1)


if __name__ == "__main__":
    main()
