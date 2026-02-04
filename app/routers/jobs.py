"""
Job Status API Router
Provides REST endpoints for job status visibility and manual retry
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
import structlog

from app.database import get_db
from app.models.incoming_email import IncomingEmail

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by processing_status"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of jobs to return"),
    db: Session = Depends(get_db)
):
    """
    List recent jobs with optional status filter

    Returns list of jobs with summary statistics.

    Args:
        status: Optional processing_status filter (received, queued, processing, completed, failed)
        limit: Maximum number of jobs to return (1-200, default 50)
        db: Database session

    Returns:
        dict with total count, status breakdown, and job list
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    # Build base query
    query = db.query(IncomingEmail)

    # Apply status filter if provided
    if status:
        query = query.filter(IncomingEmail.processing_status == status)

    # Get total count for this filter
    total = query.count()

    # Get jobs ordered by received_at DESC (newest first)
    jobs = query.order_by(IncomingEmail.received_at.desc()).limit(limit).all()

    # Calculate status breakdown (all jobs, not just filtered)
    status_counts = {}
    for status_value in ["received", "queued", "processing", "completed", "failed"]:
        count = db.query(IncomingEmail).filter(
            IncomingEmail.processing_status == status_value
        ).count()
        status_counts[status_value] = count

    # Build response
    job_list = []
    for job in jobs:
        # Truncate subject to 100 chars
        subject = job.subject if job.subject else ""
        if len(subject) > 100:
            subject = subject[:97] + "..."

        job_list.append({
            "id": job.id,
            "processing_status": job.processing_status,
            "from_email": job.from_email,
            "subject": subject,
            "received_at": job.received_at.isoformat() if job.received_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "processing_error": job.processing_error
        })

    logger.info("jobs_listed", total=total, returned=len(job_list), filter=status)

    return {
        "total": total,
        "by_status": status_counts,
        "jobs": job_list
    }


@router.get("/{job_id}")
async def get_job_detail(
    job_id: int,
    db: Session = Depends(get_db)
):
    """
    Get detailed status for a single job

    Args:
        job_id: IncomingEmail ID
        db: Database session

    Returns:
        Detailed job information including extracted data and timing

    Raises:
        404: Job not found
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    job = db.query(IncomingEmail).filter(IncomingEmail.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Calculate processing time if both started_at and completed_at are available
    processing_time_seconds = None
    if job.started_at and job.completed_at:
        processing_time_seconds = (job.completed_at - job.started_at).total_seconds()

    logger.info("job_detail_retrieved", job_id=job_id, status=job.processing_status)

    return {
        "id": job.id,
        "processing_status": job.processing_status,
        "from_email": job.from_email,
        "subject": job.subject,
        "received_at": job.received_at.isoformat() if job.received_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "processing_time_seconds": processing_time_seconds,
        "processing_error": job.processing_error,
        "retry_count": job.retry_count,
        "extracted_data": job.extracted_data,
        "match_status": job.match_status,
        "match_confidence": job.match_confidence,
        "attachment_urls": job.attachment_urls,
        "sync_status": job.sync_status
    }


@router.post("/{job_id}/retry")
async def retry_job(
    job_id: int,
    db: Session = Depends(get_db)
):
    """
    Manually re-enqueue a failed job

    Only works on jobs with processing_status = "failed".
    Resets status to "queued", clears error, increments retry count,
    and enqueues to Dramatiq.

    Args:
        job_id: IncomingEmail ID
        db: Database session

    Returns:
        Updated job status

    Raises:
        404: Job not found
        400: Job is not in "failed" status
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not configured")

    job = db.query(IncomingEmail).filter(IncomingEmail.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.processing_status != "failed":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not in 'failed' status (current: {job.processing_status})"
        )

    # Reset job status
    job.processing_status = "queued"
    job.processing_error = None
    job.retry_count += 1
    db.commit()
    db.refresh(job)

    # Enqueue to Dramatiq
    try:
        from app.actors.email_processor import process_email
        process_email.send(email_id=job_id)
        logger.info("job_manually_retried", job_id=job_id, retry_count=job.retry_count)
    except Exception as e:
        logger.error("job_retry_enqueue_failed", job_id=job_id, error=str(e))
        # Revert status change
        job.processing_status = "failed"
        job.processing_error = f"Failed to enqueue: {str(e)}"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to enqueue job: {str(e)}")

    return {
        "id": job.id,
        "processing_status": job.processing_status,
        "retry_count": job.retry_count,
        "message": "Job re-enqueued for processing"
    }
