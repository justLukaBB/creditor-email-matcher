"""
Webhook Rate-Limit Middleware — Anti-Probing Protection

Protects POST /api/v1/resend/webhook against brute-force attempts on the 3-char
random suffix of V2 routing IDs (46k combinations).

Strategy:
- Redis-backed sliding window counter per sender IP.
- Counts UNIQUE to_addresses seen in the window (not total requests).
- If > N unique to_addresses per 60s from same IP → block for 10 min + alert.

Key format:  webhook:probe:{ip}:window     (sorted set of to_address fingerprints)
             webhook:probe:{ip}:blocked    (flag, TTL = block_duration)

If Redis is unavailable, the middleware is a no-op (fail-open). Rate-limiting
is a probing defense, not a security boundary — the routing ID itself is the
authz gate. We prefer availability over strict limits.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Defaults — override via env (RATE_LIMIT_*).
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_UNIQUE_ADDR_THRESHOLD = 20
DEFAULT_BLOCK_DURATION_SECONDS = 600  # 10 min


def _get_redis():
    """Return a Redis client, or None if unavailable."""
    try:
        from app.config import settings
        if not settings.redis_url:
            return None
        import redis
        # decode_responses=True so we get str not bytes
        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception as e:
        logger.warning("webhook_rate_limit_redis_unavailable", extra={"error": str(e)})
        return None


def _fingerprint(addr: str) -> str:
    """Short hash of an address — keeps the sorted set compact."""
    return hashlib.sha1(addr.strip().lower().encode("utf-8")).hexdigest()[:12]


def check_webhook_probing(
    sender_ip: str,
    to_addresses: Iterable[str],
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    threshold: int = DEFAULT_UNIQUE_ADDR_THRESHOLD,
    block_duration: int = DEFAULT_BLOCK_DURATION_SECONDS,
) -> tuple[bool, Optional[str]]:
    """
    Check whether `sender_ip` has exceeded the probing threshold.

    Returns (is_blocked, reason). When is_blocked=True, the caller should
    return 429. A block lasts `block_duration` seconds regardless of subsequent traffic.

    Fail-open: any Redis error returns (False, None) so webhooks keep flowing.
    """
    if not sender_ip or not to_addresses:
        return False, None

    rdb = _get_redis()
    if rdb is None:
        return False, None

    try:
        blocked_key = f"webhook:probe:{sender_ip}:blocked"
        if rdb.exists(blocked_key):
            return True, "ip_temporarily_blocked"

        window_key = f"webhook:probe:{sender_ip}:window"
        now = time.time()
        cutoff = now - window_seconds

        pipe = rdb.pipeline()
        # Drop entries outside the sliding window
        pipe.zremrangebyscore(window_key, 0, cutoff)
        # Add each to_address fingerprint with timestamp as score
        for addr in to_addresses:
            if addr:
                pipe.zadd(window_key, {_fingerprint(addr): now})
        # Keep window alive for one more window_seconds (GC)
        pipe.expire(window_key, window_seconds * 2)
        # Count unique fingerprints in the window
        pipe.zcard(window_key)
        results = pipe.execute()
        unique_count = results[-1] if results else 0

        if unique_count > threshold:
            rdb.setex(blocked_key, block_duration, "1")
            logger.warning(
                "webhook_probing_detected",
                extra={
                    "sender_ip": sender_ip,
                    "unique_addresses": unique_count,
                    "threshold": threshold,
                    "window_seconds": window_seconds,
                    "block_duration_seconds": block_duration,
                },
            )
            return True, "probing_threshold_exceeded"

        return False, None

    except Exception as e:
        logger.warning("webhook_rate_limit_error", extra={"error": str(e), "type": type(e).__name__})
        return False, None


def extract_client_ip(request) -> str:
    """
    Extract the client IP from a FastAPI Request, respecting X-Forwarded-For.

    Render / most PaaS providers terminate TLS at an edge and forward the real
    client IP in X-Forwarded-For. Fall back to request.client.host otherwise.
    """
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xff:
        # XFF can be a comma-separated chain — the left-most is the original client
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
