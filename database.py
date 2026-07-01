"""
Database configuration and session management using SQLAlchemy.
Phase 2: Transitioning from filesystem-only to SQLite-backed state.
"""

import logging
import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger("artwork-display-api.database")

# Pre-flight setup: Ensure data directories exist for zero-touch deployments
os.makedirs("./data", exist_ok=True)
os.makedirs("./Artwork", exist_ok=True)

# Architectural Choice: SQLite for local, single-user performance and simplicity.
SQLALCHEMY_DATABASE_URL = "sqlite:///./data/artwork.db"

# Explanation: connect_args={"check_same_thread": False} is required for SQLite in FastAPI.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """
    Base class for SQLAlchemy models.
    """
    pass

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a SQLAlchemy database session.

    Yields:
        Session: The database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# NOTE: schema creation is intentionally NOT done here. Alembic owns the schema as the single
# source of truth — boot runs db_migrate.run_migrations() (base -> head builds it, or a legacy
# DB is reconciled to the baseline). `Base.metadata.create_all` is used only by the test suite
# against throwaway engines. See ADR-035 for why create_all was removed from the boot path.
