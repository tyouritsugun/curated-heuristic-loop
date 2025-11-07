"""Database connection and session management for CHL."""
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, scoped_session
from contextlib import contextmanager

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
        self.engine = create_engine(
            connection_string,
            echo=self.echo,
            connect_args={"check_same_thread": False},  # Allow SQLite in multi-threaded context
        )

        # Enable foreign keys and WAL mode for SQLite
        @event.listens_for(Engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
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

        # Create session factory
        session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session_factory = scoped_session(session_factory)

        # Ensure tables exist (no-op if already created)
        Base.metadata.create_all(self.engine)

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
        if self.session_factory:
            self.session_factory.remove()
        if self.engine:
            self.engine.dispose()
        self._initialized = False


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
