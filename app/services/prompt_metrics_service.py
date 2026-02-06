"""
PromptMetricsService
Records extraction-level performance metrics with cost calculation
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from decimal import Decimal
from datetime import datetime, timedelta
import structlog

from app.models.prompt_metrics import PromptPerformanceMetrics

logger = structlog.get_logger(__name__)

# Claude API pricing (as of 2026)
# Per 1K tokens
CLAUDE_PRICING = {
    'claude-sonnet-4-5-20250514': {'input': 0.003, 'output': 0.015},
    'claude-haiku-4-20250514': {'input': 0.00025, 'output': 0.00125},
    # Fallback for unknown models
    'default': {'input': 0.003, 'output': 0.015}
}


def calculate_api_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int
) -> Decimal:
    """
    Calculate API cost in USD based on model and token usage.

    Args:
        model_name: Model identifier (e.g., 'claude-sonnet-4-5-20250514')
        input_tokens: Input tokens consumed
        output_tokens: Output tokens generated

    Returns:
        Cost in USD as Decimal with 6 decimal places

    Example:
        cost = calculate_api_cost('claude-sonnet-4-5-20250514', 1000, 500)
        # Returns: Decimal('0.010500') (1000*0.003/1000 + 500*0.015/1000)
    """
    # Get pricing for model (fallback to default if not found)
    pricing = CLAUDE_PRICING.get(model_name, CLAUDE_PRICING['default'])

    # Calculate cost per 1K tokens
    input_cost = (input_tokens / 1000) * pricing['input']
    output_cost = (output_tokens / 1000) * pricing['output']

    total_cost = Decimal(str(input_cost + output_cost)).quantize(Decimal('0.000001'))

    logger.debug(
        "api_cost_calculated",
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=float(total_cost)
    )

    return total_cost


def record_extraction_metrics(
    db: Session,
    prompt_template_id: int,
    email_id: int,
    input_tokens: int,
    output_tokens: int,
    model_name: str,
    extraction_success: bool,
    confidence_score: float | None,
    manual_review_required: bool | None,
    execution_time_ms: int
) -> PromptPerformanceMetrics:
    """
    Record extraction-level metrics for a prompt execution.

    REQ-PROMPT-02: Every extraction logs the prompt version used.
    REQ-PROMPT-04: Track tokens, time, success rate per version.

    Args:
        db: Database session
        prompt_template_id: ID of prompt version used
        email_id: IncomingEmail ID being processed
        input_tokens: Input tokens for this API call
        output_tokens: Output tokens from this API call
        model_name: Model used (for cost calculation)
        extraction_success: Did extraction complete successfully?
        confidence_score: Overall confidence (0.0-1.0) or None
        manual_review_required: Was manual review triggered?
        execution_time_ms: Execution time in milliseconds

    Returns:
        Created PromptPerformanceMetrics record

    Example:
        from app.services.prompt_metrics_service import record_extraction_metrics

        metric = record_extraction_metrics(
            db=db,
            prompt_template_id=prompt.id,
            email_id=123,
            input_tokens=1500,
            output_tokens=300,
            model_name='claude-sonnet-4-5-20250514',
            extraction_success=True,
            confidence_score=0.92,
            manual_review_required=False,
            execution_time_ms=850
        )
    """
    # Calculate API cost
    api_cost = calculate_api_cost(model_name, input_tokens, output_tokens)

    # Create metrics record
    metric = PromptPerformanceMetrics(
        prompt_template_id=prompt_template_id,
        email_id=email_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        api_cost_usd=api_cost,
        extraction_success=extraction_success,
        confidence_score=confidence_score,
        manual_review_required=manual_review_required,
        execution_time_ms=execution_time_ms
    )

    db.add(metric)
    db.commit()
    db.refresh(metric)

    logger.info(
        "extraction_metrics_recorded",
        metric_id=metric.id,
        prompt_template_id=prompt_template_id,
        email_id=email_id,
        success=extraction_success,
        confidence=confidence_score,
        cost_usd=float(api_cost),
        tokens_total=input_tokens + output_tokens
    )

    return metric


class PromptMetricsService:
    """
    Service wrapper for prompt metrics operations.

    Provides:
    - record: Log single extraction metrics
    - get_version_stats: Get aggregated stats for a version

    Per RESEARCH.md Pattern 3: Dual-table metrics tracking.
    """

    def __init__(self, db: Session):
        """
        Initialize service with database session.

        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def record(
        self,
        prompt_template_id: int,
        email_id: int,
        input_tokens: int,
        output_tokens: int,
        model_name: str,
        extraction_success: bool,
        confidence_score: float | None = None,
        manual_review_required: bool | None = None,
        execution_time_ms: int = 0
    ) -> PromptPerformanceMetrics:
        """
        Record metrics for single extraction.

        Convenience wrapper around record_extraction_metrics function.

        Args:
            prompt_template_id: ID of prompt version used
            email_id: IncomingEmail ID being processed
            input_tokens: Input tokens consumed
            output_tokens: Output tokens generated
            model_name: Model used (for cost calculation)
            extraction_success: Did extraction complete?
            confidence_score: Overall confidence (0.0-1.0) or None
            manual_review_required: Was manual review triggered?
            execution_time_ms: Execution time in milliseconds

        Returns:
            Created PromptPerformanceMetrics record

        Example:
            service = PromptMetricsService(db)
            metric = service.record(
                prompt_template_id=5,
                email_id=123,
                input_tokens=1500,
                output_tokens=300,
                model_name='claude-sonnet-4-5-20250514',
                extraction_success=True,
                confidence_score=0.92,
                manual_review_required=False,
                execution_time_ms=850
            )
        """
        return record_extraction_metrics(
            db=self.db,
            prompt_template_id=prompt_template_id,
            email_id=email_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_name=model_name,
            extraction_success=extraction_success,
            confidence_score=confidence_score,
            manual_review_required=manual_review_required,
            execution_time_ms=execution_time_ms
        )

    def get_version_stats(
        self,
        prompt_template_id: int,
        days: int = 7
    ) -> dict:
        """
        Get aggregated stats for prompt version over recent days.

        Queries raw extraction-level metrics for recent window.
        For historical data beyond 30 days, use PromptPerformanceDaily table.

        Args:
            prompt_template_id: ID of prompt version
            days: Number of recent days to aggregate (default: 7)

        Returns:
            Dict with:
            - total_extractions: Number of extractions
            - success_rate: Percentage of successful extractions (0.0-1.0)
            - avg_confidence: Average confidence score or None
            - avg_execution_time_ms: Average execution time in ms
            - total_cost_usd: Total API cost in USD

        Example:
            service = PromptMetricsService(db)
            stats = service.get_version_stats(prompt_template_id=5, days=7)
            print(f"Success rate: {stats['success_rate']*100:.1f}%")
            print(f"Avg confidence: {stats['avg_confidence']:.2f}")
            print(f"Total cost: ${stats['total_cost_usd']:.2f}")
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Query metrics for time window
        metrics = self.db.query(
            func.count(PromptPerformanceMetrics.id).label('total_extractions'),
            func.sum(func.cast(PromptPerformanceMetrics.extraction_success, db.Integer)).label('successful_extractions'),
            func.avg(PromptPerformanceMetrics.confidence_score).label('avg_confidence'),
            func.avg(PromptPerformanceMetrics.execution_time_ms).label('avg_execution_time_ms'),
            func.sum(PromptPerformanceMetrics.api_cost_usd).label('total_cost_usd')
        ).filter(
            PromptPerformanceMetrics.prompt_template_id == prompt_template_id,
            PromptPerformanceMetrics.extracted_at >= cutoff_date
        ).first()

        total_extractions = metrics.total_extractions or 0
        successful_extractions = metrics.successful_extractions or 0
        avg_confidence = float(metrics.avg_confidence) if metrics.avg_confidence is not None else None
        avg_execution_time_ms = int(metrics.avg_execution_time_ms) if metrics.avg_execution_time_ms is not None else 0
        total_cost_usd = float(metrics.total_cost_usd) if metrics.total_cost_usd is not None else 0.0

        # Calculate success rate
        success_rate = successful_extractions / total_extractions if total_extractions > 0 else 0.0

        stats = {
            'total_extractions': total_extractions,
            'success_rate': success_rate,
            'avg_confidence': avg_confidence,
            'avg_execution_time_ms': avg_execution_time_ms,
            'total_cost_usd': total_cost_usd
        }

        logger.info(
            "version_stats_retrieved",
            prompt_template_id=prompt_template_id,
            days=days,
            **stats
        )

        return stats
