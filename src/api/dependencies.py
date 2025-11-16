"""FastAPI dependency injection for shared resources."""

from typing import Generator
import time
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from src.api import server as api_server


def get_db_session() -> Generator[Session, None, None]:
    """
    Provide request-scoped database session.

    Note: Uses scoped_session which is thread-local. FastAPI uses thread pools
    for sync endpoints, so each request gets its own session.
    """
    session = api_server.db.get_session()
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
    return api_server.search_service


def get_config():
    """Provide singleton Config instance."""
    return api_server.config


def get_settings_service():
    """Provide initialized SettingsService instance."""
    return api_server.settings_service


def get_operations_service():
    """Provide OperationsService singleton."""
    return api_server.operations_service


def get_worker_control_service():
    """Provide WorkerControlService singleton."""
    return api_server.worker_control_service


def get_telemetry_service():
    """Provide TelemetryService singleton."""
    return api_server.telemetry_service


def get_mode_runtime():
    """Provide ModeRuntime singleton with search/worker wiring."""
    return api_server.mode_runtime
