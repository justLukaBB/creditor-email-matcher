"""
Dramatiq Worker Entrypoint

This module serves as the entry point for Dramatiq workers.
It imports all actor modules to register them with the broker.

Usage:
    dramatiq app.worker --processes 2 --threads 1 --verbose

Procfile Configuration:
    worker: dramatiq app.worker --processes 2 --threads 1 --verbose

Memory Budget (Render 512MB container):
    - FastAPI process: ~150MB
    - Worker processes: ~350MB (2 processes x 1 thread = 2 concurrent jobs)
    - Recommended: 2 processes x 1 thread for memory efficiency
    - Alternative: 1 process x 2 threads (if CPU-bound tasks)

Process vs Thread Trade-offs:
    - Processes: Better for CPU-intensive tasks, isolated memory, restart on OOM
    - Threads: Lower memory overhead, shared state, GIL for Python code
    - Email processing is I/O-bound (API calls, DB queries) → threads work well
    - PDF extraction is CPU-bound → processes better (added in Plan 03)
"""

import structlog
from app.actors import broker

# Configure basic logging for worker
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()

# Import actors to register them with the broker
# Actor modules are imported here so dramatiq CLI can discover them
# from app.actors import email_processor  # Added in Plan 02-03

# Worker health check log
logger.info("worker_ready", broker=type(broker).__name__)
