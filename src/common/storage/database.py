"""Database connection and session management for CHL (shared)."""

from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from .schema import Base


class Database:
    """Database connection manager."""

    def __init__(self, database_path: str, echo: bool = False):
        """
        Initialize database connection.

        Args:
            database_path: Path to SQLite database file
            echo: Enable SQLAlchemy SQL logging
        """
        self.database_path = database_path
        self.echo = echo
        self.engine = None
        self.session_factory = None
        self._initialized = False

    def init_database(self):
        """Initialize database engine and session factory."""
        if self._initialized:
            return

        # Ensure database directory exists
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create engine with WAL mode and foreign keys enabled
        connection_string = f"sqlite:///{self.database_path}"
        # Use a higher SQLite connection timeout and allow cross-thread usage.
        # busy_timeout PRAGMA is also set below on every new connection.
        # Use NullPool to prevent connection pooling issues with multi-process access
        self.engine = create_engine(
            connection_string,
            echo=self.echo,
            poolclass=NullPool,  # No connection pooling for multi-process safety
            connect_args={
                "check_same_thread": False,  # Allow SQLite in multi-threaded context
                "timeout": 30.0,             # Connection-level timeout for busy database
            },
        )

        # Enable foreign keys and WAL mode for SQLite
        @event.listens_for(Engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")  # 30 second timeout for concurrent access
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                except Exception:
                    # Some filesystems (network, cloud-synced) may not support WAL.
                    # Fall back to DELETE journal mode to avoid disk I/O errors.
                    cursor.execute("PRAGMA journal_mode=DELETE")
            except Exception:
                # As a last resort, ignore PRAGMA failures to keep the connection usable.
                pass
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass

        # Create session factory (new session per request)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Ensure tables exist (no-op if already created)
        Base.metadata.create_all(self.engine)
        self._run_bootstrap_migrations()

        self._initialized = True

    def create_tables(self):
        """Create all tables defined in schema."""
        if not self._initialized:
            raise RuntimeError("Database not initialized. Call init_database() first.")

        Base.metadata.create_all(self.engine)

    def drop_tables(self):
        """Drop all tables. Use with caution!"""
        if not self._initialized:
            raise RuntimeError("Database not initialized. Call init_database() first.")

        Base.metadata.drop_all(self.engine)

    def get_session(self):
        """
        Get a new database session.

        Returns:
            SQLAlchemy session

        Note: Caller is responsible for closing the session.
        """
        if not self._initialized:
            raise RuntimeError("Database not initialized. Call init_database() first.")

        return self.session_factory()

    @contextmanager
    def session_scope(self):
        """
        Provide a transactional scope around a series of operations.

        Usage:
            with db.session_scope() as session:
                session.add(obj)
                # ... more operations
            # Session is automatically committed or rolled back
        """
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self):
        """Close database connections."""
        self.session_factory = None
        if self.engine:
            self.engine.dispose()
        self._initialized = False

    def _run_bootstrap_migrations(self):
        """Apply lightweight SQLite migrations for new bootstrap columns."""
        if self.engine is None or self.engine.url.get_backend_name() != "sqlite":
            return

        def _has_column(conn, table: str, column: str) -> bool:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return any(row[1] == column for row in rows)

        with self.engine.begin() as conn:
            def _add_column(table: str, column: str, ddl: str, fill_sql: str | None = None):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                if fill_sql:
                    conn.execute(text(fill_sql))

            # Job history: add job_id + error_detail columns
            if not _has_column(conn, "job_history", "job_id"):
                _add_column("job_history", "job_id", "TEXT")
                conn.execute(
                    text(
                        "UPDATE job_history SET job_id = printf('legacy-%s', id) "
                        "WHERE job_id IS NULL OR job_id = ''"
                    )
                )
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS job_history_job_id_idx "
                        "ON job_history(job_id)"
                    )
                )

            if not _has_column(conn, "job_history", "error_detail"):
                _add_column("job_history", "error_detail", "TEXT")
                if _has_column(conn, "job_history", "error"):
                    conn.execute(
                        text(
                            "UPDATE job_history SET error_detail = error "
                            "WHERE error_detail IS NULL AND error IS NOT NULL"
                        )
                    )

            # Operation locks: add owner_id/created_at/expires_at
            if not _has_column(conn, "operation_locks", "owner_id"):
                _add_column("operation_locks", "owner_id", "TEXT")
            if not _has_column(conn, "operation_locks", "created_at"):
                _add_column("operation_locks", "created_at", "TEXT")
                conn.execute(
                    text(
                        "UPDATE operation_locks SET created_at = "
                        "datetime('now') WHERE created_at IS NULL"
                    )
                )
            if not _has_column(conn, "operation_locks", "expires_at"):
                _add_column("operation_locks", "expires_at", "TEXT")

            # Settings extras used by the initial UI
            if not _has_column(conn, "settings", "checksum"):
                _add_column("settings", "checksum", "TEXT")
            if not _has_column(conn, "settings", "notes"):
                _add_column("settings", "notes", "TEXT")

            # Telemetry samples upgraded schema
            if not _has_column(conn, "telemetry_samples", "metric"):
                _add_column(
                    "telemetry_samples",
                    "metric",
                    "TEXT",
                    "UPDATE telemetry_samples SET metric = sample_type WHERE metric IS NULL",
                )
            if not _has_column(conn, "telemetry_samples", "value_json"):
                _add_column(
                    "telemetry_samples",
                    "value_json",
                    "TEXT",
                    "UPDATE telemetry_samples SET value_json = payload WHERE value_json IS NULL",
                )
            if not _has_column(conn, "telemetry_samples", "recorded_at"):
                _add_column(
                    "telemetry_samples",
                    "recorded_at",
                    "TEXT",
                    "UPDATE telemetry_samples SET recorded_at = created_at WHERE recorded_at IS NULL",
                )

            # Worker metrics upgraded schema
            if not _has_column(conn, "worker_metrics", "worker_id"):
                _add_column(
                    "worker_metrics",
                    "worker_id",
                    "TEXT",
                    "UPDATE worker_metrics SET worker_id = worker_name WHERE worker_id IS NULL",
                )
            if not _has_column(conn, "worker_metrics", "status"):
                _add_column("worker_metrics", "status", "TEXT")
            if not _has_column(conn, "worker_metrics", "heartbeat_at"):
                _add_column(
                    "worker_metrics",
                    "heartbeat_at",
                    "TEXT",
                    "UPDATE worker_metrics SET heartbeat_at = created_at WHERE heartbeat_at IS NULL",
                )
            if not _has_column(conn, "worker_metrics", "queue_depth"):
                _add_column("worker_metrics", "queue_depth", "INTEGER")
            if not _has_column(conn, "worker_metrics", "processed"):
                _add_column("worker_metrics", "processed", "INTEGER")
            if not _has_column(conn, "worker_metrics", "failed"):
                _add_column("worker_metrics", "failed", "INTEGER")
            if not _has_column(conn, "worker_metrics", "payload"):
                _add_column("worker_metrics", "payload", "TEXT")


# Global database instance (will be initialized by config)
_db_instance: Database | None = None


def get_database() -> Database:
    """Get the global database instance."""
    if _db_instance is None:
        raise RuntimeError("Database not initialized. Call init_database() from config first.")
    return _db_instance


def init_database(database_path: str, echo: bool = False) -> Database:
    """
    Initialize the global database instance.

    Args:
        database_path: Path to SQLite database file
        echo: Enable SQLAlchemy SQL logging

    Returns:
        Initialized Database instance
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(database_path, echo)
        _db_instance.init_database()
    return _db_instance


@contextmanager
def get_session():
    """
    Get a database session as a context manager.

    Usage:
        with get_session() as session:
            # ... database operations
        # Session is automatically committed and closed

    Yields:
        SQLAlchemy session
    """
    db = get_database()
    with db.session_scope() as session:
        yield session
