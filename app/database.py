"""
Database Configuration and Session Management
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings
import logging

logger = logging.getLogger(__name__)

# Create SQLAlchemy engine
engine = None
SessionLocal = None

def init_db():
    """Initialize database connection"""
    global engine, SessionLocal

    if not settings.database_url:
        logger.warning("DATABASE_URL not configured - database features disabled")
        return

    logger.info(f"Connecting to database...")
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,  # Verify connections before using them
        pool_size=5,
        max_overflow=10
    )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info("Database connection established")


def get_db():
    """
    Dependency for getting database session (optional)
    Usage: db: Session = Depends(get_db)

    Returns None if PostgreSQL is not configured (MongoDB-only mode)
    """
    if SessionLocal is None:
        logger.warning("PostgreSQL not configured - running in MongoDB-only mode")
        yield None
        return

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Base class for all models
Base = declarative_base()
