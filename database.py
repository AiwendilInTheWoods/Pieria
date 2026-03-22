"""
Database configuration and session management using SQLAlchemy.
Phase 2: Transitioning from filesystem-only to SQLite-backed state.
"""

import logging
from typing import Generator
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase

# Configure logger as per GEMINI.md
logger = logging.getLogger("artwork-display-api.database")

# Architectural Choice: SQLite for local, single-user performance and simplicity.
SQLALCHEMY_DATABASE_URL = "sqlite:///./artwork.db"

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

def apply_migrations() -> None:
    """
    Lightweight migration helper that adds missing columns to existing tables.
    """
    inspector = inspect(engine)
    with engine.connect() as conn:
        for table_name, table in Base.metadata.tables.items():
            if not inspector.has_table(table_name):
                continue
            
            existing_columns = [c['name'] for c in inspector.get_columns(table_name)]
            for column in table.columns:
                if column.name not in existing_columns:
                    logger.info(f"Migration: Adding column '{column.name}' to table '{table_name}'")
                    # SQLite ALTER TABLE ADD COLUMN is limited but works for basic types
                    # We determine a safe default if not provided
                    col_type = str(column.type.compile(engine.dialect))
                    
                    default_clause = ""
                    if column.default is not None:
                        # This is a bit simplified but works for basic defaults
                        arg = column.default.arg
                        if isinstance(arg, bool):
                            default_clause = f" DEFAULT {1 if arg else 0}"
                        elif isinstance(arg, (int, float)):
                            default_clause = f" DEFAULT {arg}"
                        elif isinstance(arg, str):
                            default_clause = f" DEFAULT '{arg}'"
                    elif not column.nullable:
                        # Provide safe defaults for NOT NULL columns without explicit defaults
                        if "INT" in col_type.upper() or "FLOAT" in col_type.upper():
                            default_clause = " DEFAULT 0"
                        elif "BOOL" in col_type.upper():
                            default_clause = " DEFAULT 0"
                        else:
                            default_clause = " DEFAULT ''"

                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type}{default_clause}"))
        conn.commit()

def init_db() -> None:
    """
    Initializes the database by creating all tables and applying migrations.
    """
    try:
        # Create tables that don't exist
        Base.metadata.create_all(bind=engine)
        # Apply lightweight migrations for new columns
        apply_migrations()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}", exc_info=True)
        raise
