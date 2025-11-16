"""FastAPI dependency injection for shared resources.

Uses app.state to access singletons instead of module globals to avoid circular imports.
"""

from typing import Generator
import time
from fastapi import Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError


def get_db(request: Request):
    """Provide Database instance from app state."""
    return request.app.state.db


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """
    Provide request-scoped database session.

    Note: Uses scoped_session which is thread-local. FastAPI uses thread pools
    for sync endpoints, so each request gets its own session.
    """
    session = request.app.state.db.get_session()
    try:
        yield session
        # Commit with exponential backoff retry to mitigate transient SQLite locks
        # that can occur due to WAL and concurrent background tasks (e.g., telemetry)
        max_attempts = 3
        base_delay = 0.05
        attempt = 0
        while True:
            try:
                session.commit()
                break
            except OperationalError as exc:
                msg = str(exc).lower()
                if ("database is locked" in msg or "database is busy" in msg) and attempt < (max_attempts - 1):
                    # Exponential backoff: 0.05s, 0.1s, 0.2s
                    time.sleep(base_delay * (2 ** attempt))
                    attempt += 1
                    continue
                raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_search_service(request: Request):
    """Provide singleton SearchService instance."""
    return request.app.state.search_service


def get_config(request: Request):
    """Provide singleton Config instance."""
    return request.app.state.config


def get_settings_service(request: Request):
    """Provide initialized SettingsService instance."""
    return request.app.state.settings_service


def get_operations_service(request: Request):
    """Provide OperationsService singleton."""
    return request.app.state.operations_service


def get_worker_control_service(request: Request):
    """Provide WorkerControlService singleton."""
    return request.app.state.worker_control_service


def get_telemetry_service(request: Request):
    """Provide TelemetryService singleton."""
    return request.app.state.telemetry_service


def get_mode_runtime(request: Request):
    """Provide ModeRuntime singleton with search/worker wiring."""
    return request.app.state.mode_runtime
