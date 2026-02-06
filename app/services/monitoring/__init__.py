"""
Monitoring Module
Exports for structured logging, circuit breakers, metrics collection, and observability
"""

from app.services.monitoring.logging import setup_logging, CorrelationJsonFormatter
from app.services.monitoring.circuit_breakers import (
    get_claude_breaker,
    get_mongodb_breaker,
    get_gcs_breaker,
    with_circuit_breaker,
    CircuitBreakerError,
    CircuitBreakerEmailListener,
)
from app.services.monitoring.metrics import MetricsCollector, get_metrics_collector

__all__ = [
    "setup_logging",
    "CorrelationJsonFormatter",
    "get_claude_breaker",
    "get_mongodb_breaker",
    "get_gcs_breaker",
    "with_circuit_breaker",
    "CircuitBreakerError",
    "CircuitBreakerEmailListener",
    "MetricsCollector",
    "get_metrics_collector",
]
