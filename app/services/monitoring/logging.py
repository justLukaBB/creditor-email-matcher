"""
Structured JSON Logging with Correlation ID
Provides JSON formatter that automatically injects correlation IDs into all log entries
"""

import logging
import sys
import os
from pythonjsonlogger import jsonlogger
from asgi_correlation_id.context import correlation_id


class CorrelationJsonFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter with automatic correlation ID injection.

    Extends python-json-logger to add correlation_id field to every log record.
    The correlation ID is retrieved from async context (set by CorrelationIdMiddleware).
    """

    def add_fields(self, log_record, record, message_dict):
        """
        Add custom fields to log record.

        Called by JsonFormatter for each log entry. Adds:
        - correlation_id: From async context or 'none' if not available
        - service: Application name for multi-service environments
        - environment: Deployment environment (development/production)

        Args:
            log_record: Dictionary to be serialized to JSON
            record: Standard logging.LogRecord object
            message_dict: Additional fields from logger call
        """
        super().add_fields(log_record, record, message_dict)

        # Add correlation ID from async context
        log_record['correlation_id'] = correlation_id.get() or 'none'

        # Add service identifier
        log_record['service'] = 'creditor-answer-analysis'

        # Add environment
        log_record['environment'] = os.getenv('ENVIRONMENT', 'development')


def setup_logging():
    """
    Configure structured JSON logging to stdout.

    Sets up root logger with:
    - CorrelationJsonFormatter for machine-parseable JSON output
    - INFO level logging (production default)
    - StreamHandler outputting to stdout

    All subsequent logging calls (logging.info, logger.info, etc.) will
    automatically output JSON with correlation_id, timestamp, level, etc.

    Returns:
        logging.Handler: The configured handler (for testing)
    """
    handler = logging.StreamHandler(sys.stdout)

    formatter = CorrelationJsonFormatter(
        '%(timestamp)s %(level)s %(name)s %(message)s',
        rename_fields={
            'timestamp': 'asctime',
            'level': 'levelname'
        }
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)  # INFO level for production

    return handler
