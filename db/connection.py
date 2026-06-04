"""
Database Connection Pool
==========================
Manages PostgreSQL connection lifecycle for the application.
"""

import logging
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session as DBSession
from sqlalchemy.pool import QueuePool

from config.settings import settings

logger = logging.getLogger("amazon.db.connection")

_engine = None
_SessionLocal = None


def get_engine():
    """Get or create the SQLAlchemy engine with connection pooling."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            poolclass=QueuePool,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=settings.debug,
        )
        logger.info(
            "Database pool created: %s (%d connections)",
            settings.database_url.split("@")[-1].split("/")[0] if "@" in settings.database_url else "local",
            settings.database_pool_size,
        )
    return _engine


def get_session() -> DBSession:
    """Get a new database session."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
        )
    return _SessionLocal()


def close_all():
    """Dispose of the connection pool."""
    global _engine, _SessionLocal
    if _engine:
        _engine.dispose()
        _engine = None
        _SessionLocal = None
        logger.info("Database pool disposed")
