"""FastAPI dependency injection for shared resources."""

from typing import Generator
from sqlalchemy.orm import Session


def get_db_session() -> Generator[Session, None, None]:
    """
    Provide request-scoped database session.

    Note: Uses scoped_session which is thread-local. FastAPI uses thread pools
    for sync endpoints, so each request gets its own session.
    """
    from src.api_server import db

    session = db.get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_search_service():
    """Provide singleton SearchService instance."""
    from src.api_server import search_service
    return search_service


def get_config():
    """Provide singleton Config instance."""
    from src.api_server import config
    return config


def get_faiss_lock():
    """Provide global FAISS lock for thread-safe operations."""
    from src.api_server import faiss_lock
    return faiss_lock
