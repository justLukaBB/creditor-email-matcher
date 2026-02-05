"""
Dramatiq Actors - Async Job Processing

This module sets up the Dramatiq broker and registers all actors.
The broker is configured to use:
- RedisBroker when REDIS_URL is set (production)
- StubBroker when REDIS_URL is not set (testing/development)

Usage:
    from app.actors import broker
"""

import structlog
from app.config import settings

logger = structlog.get_logger()


def setup_broker():
    """
    Initialize and configure Dramatiq broker.

    Returns:
        Broker instance (RedisBroker or StubBroker)
    """
    if settings.redis_url:
        # Production mode - use Redis
        import dramatiq
        from dramatiq.brokers.redis import RedisBroker

        broker = RedisBroker(
            url=settings.redis_url,
            namespace="creditor_matcher",
            max_connections=10,
            socket_timeout=5,
            socket_connect_timeout=5,
            socket_keepalive=True,
            retry_on_timeout=True,
            heartbeat_timeout=30000,
            dead_message_ttl=86400000
        )
        dramatiq.set_broker(broker)
        logger.info("broker_configured", type="RedisBroker", url=settings.redis_url)
    else:
        # Testing/development mode - use StubBroker
        import dramatiq
        from dramatiq.brokers.stub import StubBroker

        broker = StubBroker()
        dramatiq.set_broker(broker)
        logger.info("broker_configured", type="StubBroker", mode="testing")

    return broker


# Initialize broker at module level
broker = setup_broker()

# Actor imports (registered with broker on import)
from app.actors import email_processor  # noqa: F401
from app.actors import content_extractor  # noqa: F401
from app.actors import consolidation_agent  # noqa: F401
from app.actors import intent_classifier  # noqa: F401

# Export specific actors for convenience
from app.actors.email_processor import process_email  # noqa: F401
from app.actors.content_extractor import extract_content  # noqa: F401
from app.actors.intent_classifier import classify_intent  # noqa: F401
