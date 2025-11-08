"""FastAPI dependency injection for shared resources."""

from typing import Generator
import time
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError


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
        # Commit with a short retry loop to mitigate transient SQLite locks
        # that can occur due to WAL and concurrent background tasks (e.g., telemetry)
        max_attempts = 3
        delay = 0.05
        attempt = 0
        while True:
            try:
                session.commit()
                break
            except OperationalError as exc:
                msg = str(exc).lower()
                if ("database is locked" in msg or "database is busy" in msg) and attempt < (max_attempts - 1):
                    time.sleep(delay * (attempt + 1))
                    attempt += 1
                    continue
                raise
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


def get_settings_service():
    """Provide initialized SettingsService instance."""
    from src.api_server import settings_service
    return settings_service


def get_operations_service():
    """Provide OperationsService singleton."""
    from src.api_server import operations_service
    return operations_service


def get_worker_control_service():
    """Provide WorkerControlService singleton."""
    from src.api_server import worker_control_service
    return worker_control_service


def get_telemetry_service():
    """Provide TelemetryService singleton."""
    from src.api_server import telemetry_service
    return telemetry_service
