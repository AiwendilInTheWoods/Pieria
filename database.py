"""
Database configuration and session management using SQLAlchemy.
Phase 2: Transitioning from filesystem-only to SQLite-backed state.
"""

import logging
import os
from typing import Generator
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase

# Configure logger as per GEMINI.md
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

def init_db() -> None:
    """
    Initializes the database by creating all tables.
    """
    try:
        # Create tables that don't exist
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}", exc_info=True)
        raise
